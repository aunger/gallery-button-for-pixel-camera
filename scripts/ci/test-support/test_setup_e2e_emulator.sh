#!/usr/bin/env bash
# test_setup_e2e_emulator.sh: Shell-based tests for setup-e2e-emulator.sh's
# resolution of the Android command-line tools, and for the failures it reports
# on the way to a running emulator.
#
# The script calls sdkmanager and avdmanager by absolute path, out of one
# CMDLINE_TOOLS directory it resolves once. These tests run it against a
# fabricated SDK tree whose command-line tools and emulator are stubs that
# record their own path, so each case can assert what ran and from where. adb is
# stubbed silently, since the post-boot steps call it dozens of times.
#
# Cases (a) to (h) are about resolution, and their stub sdkmanager exits 1: under
# the script's `set -e` that ends the run at the install line, the first place
# the resolved directory is invoked rather than merely tested, and short of the
# emulator launch below it. Cases (i) to (o) are about what happens from the AVD
# onwards, so their sdkmanager succeeds and the run goes further. Those cases
# compress the device and boot waits' bounds to seconds and point $EMULATOR_LOG
# inside the suite's own directory, both through the environment the script
# reads them from.
#
# Covers:
#   (a) cmdline-tools/latest/bin holds sdkmanager -> it is the one invoked
#   (b) No cmdline-tools/latest -> cmdline-tools/bin is used instead
#   (c) cmdline-tools/latest present but holding no sdkmanager -> same fallback
#   (d) A failing sdkmanager is not retried at a second path (issue #1133)
#   (e) Neither directory present -> the script's guard names both candidates
#       and the withdrawn tools/bin is neither invoked nor named (issue #1127)
#   (f) --post-boot, the form CI invokes, touches no command-line tool at all,
#       so the guards in (e) and (g) cannot reach a CI run
#   (g) sdkmanager resolved but no avdmanager beside it -> refused by a guard of
#       its own, ahead of the install line (issue #1141)
#   (h) An avdmanager in the candidate directory the resolution did not choose
#       does not rescue that run: both binaries come from one directory
#   (i) A failing avdmanager ends the run, keeps its message, and starts no
#       emulator for the AVD it did not create (issue #1141)
#   (j) An emulator that never produces a device is given up on, rather than
#       waited for forever. The failure carries adb's account of why, the knob
#       that raises the bound, and the emulator it leaves running (issue #1141).
#       The wait sleeps, and counts, in the interval the environment set
#   (k) An emulator that exits during startup is reported as that, at once,
#       rather than at the bound
#   (l) An emulator that comes online but never finishes booting is given up
#       on, and that failure names the knob raising its own bound: it is the
#       next bound a run that raised DEVICE_TIMEOUT meets (issue #1156). This
#       wait, too, sleeps and counts in the interval the environment set
#   (m) A device that leaves after coming online is reported as adb now finds
#       it, rather than as the slow boot it is indistinguishable from through
#       the polled property alone
#   (n) An emulator that exits while booting is reported as that, at once,
#       rather than at the boot bound
#   (o) A device that does come online carries the run to the end, and a
#       successful AVD creation prints nothing
#
# Because both binaries are required together, the fixtures install them as a
# pair, except where a case is about one of them being absent.
#
# Always exits 0 on success, non-zero on failure.

set -euo pipefail

# The script reads ANDROID_HOME, then ANDROID_SDK_ROOT. A developer's shell and
# an Android CI runner both export them; every case sets ANDROID_HOME to its own
# fabricated tree, so clear the pair here to keep a real SDK out of the tests.
unset ANDROID_HOME ANDROID_SDK_ROOT

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETUP="$SCRIPT_DIR/setup-e2e-emulator.sh"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

TMPDIR_TESTS="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_TESTS"' EXIT

# Every recording stub appends its own path here, so a case can assert on what
# ran. Exported because the stubs read it from the environment the script passes
# down.
export INVOKED="$TMPDIR_TESTS/invoked.log"

# Both waits on the way to a booted emulator are bounded, and both bounds are
# overridable, so the cases that exercise them run in seconds rather than the
# script's defaults of 1200s and 600s. See "Environment" in
# setup-e2e-emulator.sh.
export DEVICE_TIMEOUT=2
export DEVICE_POLL_INTERVAL=1
export BOOT_TIMEOUT=2
export BOOT_POLL_INTERVAL=1

# Inside the suite's own directory, so no case writes over the emulator log of a
# real run on the developer's machine.
export EMULATOR_LOG="$TMPDIR_TESTS/emulator.log"

# Each wait reads its poll interval twice: once into `sleep`, which spaces the
# checks, and once into the counter that decides when the bound is reached. A
# loop that stopped reading the variable in the first place while still adding
# it in the second would print progress lines that look exactly right and take
# five times as long to reach a bound measured in seconds, so the number that
# reaches `sleep` has to be asserted on directly. This stub records it and then
# sleeps for real, so the waits keep their timing and a case can see what they
# asked for.
#
# The real sleep is resolved now, by absolute path, because the stub execs it
# and PATH will have the stub itself in front by then.
REAL_SLEEP="$(command -v sleep)"
if [[ -z "$REAL_SLEEP" ]]; then
  echo "FAIL: no sleep on PATH to record" >&2
  exit 1
fi
export SLEPT="$TMPDIR_TESTS/slept.log"
STUB_BIN="$TMPDIR_TESTS/bin"
mkdir -p "$STUB_BIN"
{
  echo '#!/usr/bin/env bash'
  echo 'echo "$1" >> "$SLEPT"'
  printf 'exec %q "$@"\n' "$REAL_SLEEP"
} > "$STUB_BIN/sleep"
chmod +x "$STUB_BIN/sleep"

make_stub() {
  # Usage: make_stub <path> <exit-code>
  # A stub that records its own path in $INVOKED before exiting.
  local path="$1" code="$2"
  mkdir -p "$(dirname "$path")"
  {
    echo '#!/usr/bin/env bash'
    echo 'echo "$0" >> "$INVOKED"'
    echo "exit $code"
  } > "$path"
  chmod +x "$path"
}

make_quiet_stub() {
  # Usage: make_quiet_stub <path>
  # A stub that succeeds without recording. adb is called dozens of times by the
  # post-boot steps, and recording those would drown out the invocations these
  # tests assert on. The emulator records, since it runs at most once and
  # whether it ran is the thing several cases are about.
  local path="$1"
  mkdir -p "$(dirname "$path")"
  {
    echo '#!/usr/bin/env bash'
    echo 'exit 0'
  } > "$path"
  chmod +x "$path"
}

make_noisy_stub() {
  # Usage: make_noisy_stub <path> <stderr text> <exit code>
  # Records like make_stub, and writes <stderr text> to stderr before exiting.
  # avdmanager reports what went wrong there, so a case can assert that the text
  # reached the developer instead of /dev/null.
  local path="$1" message="$2" code="$3"
  mkdir -p "$(dirname "$path")"
  {
    echo '#!/usr/bin/env bash'
    echo 'echo "$0" >> "$INVOKED"'
    printf 'echo %q >&2\n' "$message"
    echo "exit $code"
  } > "$path"
  chmod +x "$path"
}

make_cmdline_tools() {
  # Usage: make_cmdline_tools <directory> [exit code]
  # A command-line tools directory as an install leaves it: sdkmanager and
  # avdmanager together. Both record, so a case can assert that avdmanager was
  # not reached as well as which sdkmanager ran. The default exit code is 1,
  # which ends a full-setup run at the install line.
  local dir="$1" code="${2:-1}"
  make_stub "$dir/sdkmanager" "$code"
  make_stub "$dir/avdmanager" "$code"
}

emit_get_state_answer() {
  # Usage: emit_get_state_answer <state|error text> <indent>
  # The body of one `adb get-state` answer, as the real one gives it: "device"
  # on stdout and exit 0 when a device is there, and otherwise nothing on
  # stdout, the given text on stderr as `error: <text>`, and exit 1. Written
  # out twice by the stub below when a case gives a later answer, so the two
  # answers cannot drift apart in shape.
  local answer="$1" indent="$2"
  if [[ "$answer" == "device" ]]; then
    printf '%secho device\n' "$indent"
    printf '%sexit 0\n' "$indent"
  else
    printf '%secho %q >&2\n' "$indent" "error: $answer"
    printf '%sexit 1\n' "$indent"
  fi
}

make_adb_stub() {
  # Usage: make_adb_stub <path> <state|error text> [boot value] [later state]
  # An adb for the cases that reach the device wait. `get-state` answers as the
  # real one does (see emit_get_state_answer). `shell getprop` answers with the
  # given value, which defaults to 1 so a case that gets past the device wait
  # is not then held in the boot loop. An empty value is what a real getprop
  # prints for a property that is not set, and holds the case in that loop
  # instead. Everything else succeeds silently.
  #
  # With a later state, the first `get-state` answers with <state> and every
  # one after it with <later state>: the device wait sees a device arrive, and
  # whoever asks next sees what became of it. The switch is recorded in a file
  # beside the stub, because the script runs each adb as its own process and
  # nothing in the stub outlives one call.
  local path="$1" state="$2" boot="${3-1}" later="${4-}"
  local seen="$path.get-state-seen"
  rm -f "$seen"
  mkdir -p "$(dirname "$path")"
  {
    echo '#!/usr/bin/env bash'
    echo 'if [[ "${1:-}" == "get-state" ]]; then'
    if [[ -n "$later" ]]; then
      printf '  if [[ -e %q ]]; then\n' "$seen"
      emit_get_state_answer "$later" "    "
      echo '  fi'
      printf '  : > %q\n' "$seen"
    fi
    emit_get_state_answer "$state" "  "
    echo 'fi'
    echo 'if [[ "${1:-}" == "shell" && "${2:-}" == "getprop" ]]; then'
    printf '  echo %q\n' "$boot"
    echo '  exit 0'
    echo 'fi'
    echo 'exit 0'
  } > "$path"
  chmod +x "$path"
}

make_emulator_stub() {
  # Usage: make_emulator_stub <path> <log line> <exit|hang> [pid file]
  # An emulator that records, then writes <log line> to stdout, which the script
  # redirects into $EMULATOR_LOG; a case can then assert that the log reached
  # the failure message. `exit` leaves at once. `hang` stays up without ever
  # producing a device, and writes its PID to <pid file> so the case can reap
  # it: the script leaves it running when it gives up, as a real run would.
  #
  # The sleep here is the real one, by absolute path, so that the fixture's own
  # wait stays out of the recording stub's log: what that log is read for is the
  # interval the script under test asked to sleep for.
  #
  # `hang` execs its sleep instead of running it as a child. The PID recorded
  # here is this wrapper's, which is also the PID the script under test holds as
  # EMULATOR_PID. A child sleep would survive a kill aimed at the wrapper and be
  # reparented to init, one orphan per run; exec makes that PID the sleep's own,
  # so the kill reaches it.
  local path="$1" line="$2" mode="$3" pidfile="${4:-}"
  mkdir -p "$(dirname "$path")"
  {
    echo '#!/usr/bin/env bash'
    echo 'echo "$0" >> "$INVOKED"'
    printf 'echo %q\n' "$line"
    if [[ "$mode" == "hang" ]]; then
      printf 'echo $$ > %q\n' "$pidfile"
      printf 'exec %q 120\n' "$REAL_SLEEP"
    fi
    echo 'exit 0'
  } > "$path"
  chmod +x "$path"
}

new_sdk() {
  # Usage: new_sdk <case letter>
  # Creates an SDK tree with adb, an emulator, and the withdrawn tools/bin
  # package, and echoes its root. Command-line tools are added per case. The
  # case letter names the tree: this runs in a command substitution, so a
  # counter kept here would not survive back into the caller and every case
  # would share one tree.
  local sdk="$TMPDIR_TESTS/sdk-$1"
  make_quiet_stub "$sdk/platform-tools/adb"
  make_stub "$sdk/emulator/emulator" 0
  make_stub "$sdk/tools/bin/sdkmanager" 1
  make_stub "$sdk/tools/bin/avdmanager" 1
  echo "$sdk"
}

run_setup() {
  # Usage: run_setup <sdk root> [script arguments...]
  # Empties the invocation log, then sets RC and OUTPUT from the run.
  local sdk="$1"
  shift
  : > "$INVOKED"
  : > "$EMULATOR_LOG"
  : > "$SLEPT"
  RC=0
  # Bounded, because what this suite tests is a script that used to wait for a
  # device forever. A case that regains that behavior should fail the run, not
  # hang the job; `timeout` reports 124, which no assertion here accepts.
  OUTPUT="$(ANDROID_HOME="$sdk" PATH="$STUB_BIN:$PATH" timeout 60 bash "$SETUP" "$@" 2>&1)" || RC=$?
}

emulator_started() { grep -qF "/emulator/emulator" "$INVOKED"; }

invocations() { cat "$INVOKED"; }

# (a) cmdline-tools/latest/bin holds sdkmanager -------------------------------
echo ""
echo "=== (a) sdkmanager in cmdline-tools/latest/bin is the one invoked ==="

SDK_A="$(new_sdk a)"
make_cmdline_tools "$SDK_A/cmdline-tools/latest/bin"
make_cmdline_tools "$SDK_A/cmdline-tools/bin"
run_setup "$SDK_A"

if [[ "$(invocations)" == "$SDK_A/cmdline-tools/latest/bin/sdkmanager" ]]; then
  pass "cmdline-tools/latest/bin/sdkmanager invoked, and nothing else"
else
  fail "expected only cmdline-tools/latest/bin/sdkmanager, got: $(invocations)"
fi

# (b) No cmdline-tools/latest -------------------------------------------------
echo ""
echo "=== (b) Without cmdline-tools/latest, cmdline-tools/bin is used ==="

SDK_B="$(new_sdk b)"
make_cmdline_tools "$SDK_B/cmdline-tools/bin"
run_setup "$SDK_B"

if [[ "$(invocations)" == "$SDK_B/cmdline-tools/bin/sdkmanager" ]]; then
  pass "cmdline-tools/bin/sdkmanager invoked"
else
  fail "expected cmdline-tools/bin/sdkmanager, got: $(invocations)"
fi

# (c) cmdline-tools/latest without the tools in it ----------------------------
echo ""
echo "=== (c) An empty cmdline-tools/latest falls back to cmdline-tools/bin ==="

SDK_C="$(new_sdk c)"
mkdir -p "$SDK_C/cmdline-tools/latest/bin"
make_cmdline_tools "$SDK_C/cmdline-tools/bin"
run_setup "$SDK_C"

if [[ "$(invocations)" == "$SDK_C/cmdline-tools/bin/sdkmanager" ]]; then
  pass "cmdline-tools/bin/sdkmanager invoked although latest/bin exists"
else
  fail "expected cmdline-tools/bin/sdkmanager, got: $(invocations)"
fi

# (d) A failing sdkmanager is not retried elsewhere ---------------------------
echo ""
echo "=== (d) A failing sdkmanager is not retried at a second path ==="

# Case (a)'s tree is the one that can show a retry: it holds an sdkmanager in
# both directories, and the resolved one fails. The install must not be sent to
# the other directory after that, and the failure must end the run.
run_setup "$SDK_A"

if [[ $RC -ne 0 ]]; then
  pass "the run fails when sdkmanager fails (exit $RC)"
else
  fail "the run succeeded although sdkmanager exited 1"
fi

if [[ "$(invocations | wc -l)" -eq 1 ]]; then
  pass "exactly one sdkmanager invocation"
else
  fail "expected 1 invocation, got $(invocations | wc -l): $(invocations)"
fi

if grep -qF "$SDK_A/cmdline-tools/bin/sdkmanager" "$INVOKED"; then
  fail "the install was retried at cmdline-tools/bin: $(invocations)"
else
  pass "no second attempt at cmdline-tools/bin"
fi

# (e) Neither directory present -----------------------------------------------
echo ""
echo "=== (e) With no command-line tools, the script's own guard reports it ==="

SDK_E="$(new_sdk e)"
run_setup "$SDK_E"

if [[ $RC -eq 1 ]]; then
  pass "the run exits 1 from the guard, not 127 from a missing binary"
else
  fail "expected exit 1, got $RC: $OUTPUT"
fi

if [[ -s "$INVOKED" ]]; then
  fail "tools/bin was invoked: $(invocations)"
else
  pass "the withdrawn tools/bin package was not invoked"
fi

if grep -qF "ERROR: sdkmanager not found" <<< "$OUTPUT"; then
  pass "the guard's message is what reports the failure"
else
  fail "no guard message in the failure: $OUTPUT"
fi

# Both candidates, so the message does not send a developer to the
# unzipped-in-place layout when `cmdline-tools/latest` is what they want.
for candidate in "$SDK_E/cmdline-tools/latest/bin" "$SDK_E/cmdline-tools/bin"; do
  if grep -qF "$candidate" <<< "$OUTPUT"; then
    pass "the failure names $candidate"
  else
    fail "the failure does not name $candidate: $OUTPUT"
  fi
done

if grep -qF "$SDK_E/tools/bin" <<< "$OUTPUT"; then
  fail "the failure names the withdrawn tools/bin package: $OUTPUT"
else
  pass "the failure does not name tools/bin"
fi

# (f) --post-boot, the form CI invokes ----------------------------------------
echo ""
echo "=== (f) --post-boot invokes no command-line tool ==="

SDK_F="$(new_sdk f)"
run_setup "$SDK_F" --post-boot

if [[ $RC -eq 0 ]]; then
  pass "--post-boot succeeds with no command-line tools installed"
else
  fail "--post-boot failed (exit $RC): $OUTPUT"
fi

if [[ -s "$INVOKED" ]]; then
  fail "--post-boot invoked a command-line tool: $(invocations)"
else
  pass "no sdkmanager or avdmanager invoked"
fi

# (g) sdkmanager resolved, no avdmanager beside it ----------------------------
echo ""
echo "=== (g) sdkmanager without avdmanager is refused before anything runs ==="

SDK_G="$(new_sdk g)"
make_stub "$SDK_G/cmdline-tools/latest/bin/sdkmanager" 1
run_setup "$SDK_G"

if [[ $RC -eq 1 ]]; then
  pass "the run exits 1"
else
  fail "expected exit 1, got $RC: $OUTPUT"
fi

if grep -qF "ERROR: avdmanager not found" <<< "$OUTPUT"; then
  pass "the guard names avdmanager as the missing binary"
else
  fail "no avdmanager guard message in the failure: $OUTPUT"
fi

if grep -qF "$SDK_G/cmdline-tools/latest/bin" <<< "$OUTPUT"; then
  pass "the failure names the directory the resolution chose"
else
  fail "the failure does not name the resolved directory: $OUTPUT"
fi

# The guard sits ahead of the install line, so a run that cannot create an AVD
# does not first spend a system-image download finding that out.
if [[ -s "$INVOKED" ]]; then
  fail "a command-line tool ran before the guard fired: $(invocations)"
else
  pass "no sdkmanager invocation: the guard precedes the install"
fi

# (h) avdmanager only in the candidate that was not chosen --------------------
echo ""
echo "=== (h) avdmanager in the unresolved candidate does not rescue the run ==="

# sdkmanager resolves to cmdline-tools/latest/bin, and the only avdmanager sits
# in cmdline-tools/bin. Issue #1133 settled that one resolved directory serves
# both binaries, so this is still a failure and not a second fallback.
SDK_H="$(new_sdk h)"
make_stub "$SDK_H/cmdline-tools/latest/bin/sdkmanager" 1
make_stub "$SDK_H/cmdline-tools/bin/avdmanager" 1
run_setup "$SDK_H"

if [[ $RC -eq 1 ]] && grep -qF "ERROR: avdmanager not found" <<< "$OUTPUT"; then
  pass "the guard still fires (exit $RC)"
else
  fail "expected the avdmanager guard to fire, got exit $RC: $OUTPUT"
fi

if [[ -s "$INVOKED" ]]; then
  fail "a command-line tool ran: $(invocations)"
else
  pass "the avdmanager in cmdline-tools/bin was not reached for"
fi

# (i) A failing avdmanager is reported, not discarded -------------------------
echo ""
echo "=== (i) A failing avdmanager ends the run and keeps its message ==="

SDK_I="$(new_sdk i)"
make_stub "$SDK_I/cmdline-tools/latest/bin/sdkmanager" 0
make_noisy_stub "$SDK_I/cmdline-tools/latest/bin/avdmanager" \
  "Error: Package path is not valid." 1
run_setup "$SDK_I"

if [[ $RC -eq 1 ]]; then
  pass "the run exits 1"
else
  fail "expected exit 1, got $RC: $OUTPUT"
fi

if grep -qF "Error: Package path is not valid." <<< "$OUTPUT"; then
  pass "avdmanager's own stderr reaches the developer"
else
  fail "avdmanager's stderr was discarded: $OUTPUT"
fi

if grep -qF "ERROR: avdmanager could not create the AVD" <<< "$OUTPUT"; then
  pass "the script names the step that failed"
else
  fail "no diagnosis of the failed step: $OUTPUT"
fi

# The point of the issue: the run used to continue from here and wait forever
# for a device belonging to an AVD that was never created.
if emulator_started; then
  fail "the emulator was started although AVD creation failed: $(invocations)"
else
  pass "no emulator started after a failed AVD creation"
fi

# (j) The device never arrives -------------------------------------------------
echo ""
echo "=== (j) An emulator that never produces a device is given up on ==="

# Everything up to the wait succeeds, and then no device ever appears while the
# emulator stays alive. This is the shape the unbounded `adb wait-for-device`
# used to block on forever (issue #1141).
SDK_J="$(new_sdk j)"
make_cmdline_tools "$SDK_J/cmdline-tools/latest/bin" 0
make_adb_stub "$SDK_J/platform-tools/adb" "more than one device/emulator"
HANGING_EMULATOR_PID="$TMPDIR_TESTS/hanging-emulator.pid"
make_emulator_stub "$SDK_J/emulator/emulator" \
  "emulator: up, no device" hang "$HANGING_EMULATOR_PID"
run_setup "$SDK_J"

if [[ $RC -eq 1 ]]; then
  pass "the run gives up and exits 1"
else
  fail "expected exit 1 (124 means it hung), got $RC: $OUTPUT"
fi

if grep -qF "ERROR: No device came online within ${DEVICE_TIMEOUT}s." <<< "$OUTPUT"; then
  pass "the failure names the bound it waited out"
else
  fail "no timeout message in the failure: $OUTPUT"
fi

if grep -qF "emulator: up, no device" <<< "$OUTPUT"; then
  pass "the emulator log is printed with the failure"
else
  fail "the emulator log was not printed: $OUTPUT"
fi

# What adb says is the difference between a device that has not booted yet and
# one the script can never single out. The poll discards it to read the state,
# so the failure asks again.
if grep -qF "error: more than one device/emulator" <<< "$OUTPUT"; then
  pass "adb's own account of why it saw no device is printed"
else
  fail "adb's message was not printed: $OUTPUT"
fi

# A machine slower than the bound is the one case this wait newly fails, so the
# failure has to name the knob that accommodates it.
if grep -qF "DEVICE_TIMEOUT" <<< "$OUTPUT"; then
  pass "the failure points at the override that raises the bound"
else
  fail "the failure does not name DEVICE_TIMEOUT: $OUTPUT"
fi

# Nothing else here would notice the loop ignoring DEVICE_POLL_INTERVAL: a
# hardcoded sleep reaches the same bound and prints every message asserted
# above, so the documented knob could stop working with the suite still green.
# The two reads of it are covered separately, because either alone can break.
#
# The counter: the progress line is printed from it, and a five-second step
# never prints a first second.
if grep -qF "...waiting for device (1 / ${DEVICE_TIMEOUT}s)" <<< "$OUTPUT"; then
  pass "the wait counts in the steps DEVICE_POLL_INTERVAL set"
else
  fail "DEVICE_POLL_INTERVAL did not reach the counter: $OUTPUT"
fi

# The sleep: this is the read that spaces the checks the header documents, and
# the progress line above cannot see it. A loop that counts in ones while
# sleeping fives prints the same lines and waits five times the bound.
if [[ -s "$SLEPT" && "$(sort -u "$SLEPT")" == "$DEVICE_POLL_INTERVAL" ]]; then
  pass "every sleep the wait took was the interval DEVICE_POLL_INTERVAL set"
else
  fail "with DEVICE_POLL_INTERVAL=$DEVICE_POLL_INTERVAL the wait slept: $(tr '\n' ' ' < "$SLEPT")"
fi

# The emulator outlives the script here, so the failure has to say so: the next
# run's `avdmanager create avd --force` would rewrite the AVD underneath it.
if grep -qE "still running as PID [0-9]+" <<< "$OUTPUT"; then
  pass "the surviving emulator is named, with its PID"
else
  fail "the failure does not say the emulator is still running: $OUTPUT"
fi

# The script leaves the emulator running when it gives up, as a real run does,
# so the suite reaps it.
HANGING_PID="$(cat "$HANGING_EMULATOR_PID" 2>/dev/null || true)"
if [[ -n "$HANGING_PID" ]]; then
  # Asserted while it is still alive, because killing the recorded PID only
  # cleans up if that PID is the whole stub. The stub execs its sleep for
  # exactly this reason: run as a child instead, the sleep survives a kill
  # aimed at its parent and is reparented to init, one orphan per run of this
  # file, which no check on the recorded PID alone would ever notice.
  ORPHANS="$(pgrep -P "$HANGING_PID" 2>/dev/null || true)"
  if [[ -z "$ORPHANS" ]]; then
    pass "the hanging emulator holds no child that a kill would orphan"
  else
    fail "killing the hanging emulator would orphan: $(tr '\n' ' ' <<< "$ORPHANS")"
  fi

  kill "$HANGING_PID" 2>/dev/null || true
  REAPED=false
  for _ in $(seq 1 25); do
    if ! kill -0 "$HANGING_PID" 2>/dev/null; then
      REAPED=true
      break
    fi
    sleep 0.2
  done
  if [[ "$REAPED" == true ]]; then
    pass "the hanging emulator is gone once killed"
  else
    fail "the hanging emulator survived the kill (pid $HANGING_PID)"
  fi
else
  fail "the hanging emulator recorded no pid to reap"
fi

# (k) The emulator exits during startup ---------------------------------------
echo ""
echo "=== (k) An emulator that exits is reported without waiting out the bound ==="

SDK_K="$(new_sdk k)"
make_cmdline_tools "$SDK_K/cmdline-tools/latest/bin" 0
make_adb_stub "$SDK_K/platform-tools/adb" "no devices/emulators found"
make_emulator_stub "$SDK_K/emulator/emulator" "emulator: PANIC: no KVM" exit

# Far beyond run_setup's own 60s bound, so a run that reaches this failure by
# waiting out the clock cannot pass: only the liveness check can end it in time.
DEVICE_TIMEOUT_SAVED="$DEVICE_TIMEOUT"
export DEVICE_TIMEOUT=600
run_setup "$SDK_K"
export DEVICE_TIMEOUT="$DEVICE_TIMEOUT_SAVED"

if [[ $RC -eq 1 ]]; then
  pass "the run exits 1 long before the 600s bound"
else
  fail "expected exit 1 (124 means it waited), got $RC: $OUTPUT"
fi

if grep -qF "ERROR: The emulator exited before a device came online." <<< "$OUTPUT"; then
  pass "the failure names the dead emulator, not a timeout"
else
  fail "no emulator-exited message in the failure: $OUTPUT"
fi

if grep -qF "emulator: PANIC: no KVM" <<< "$OUTPUT"; then
  pass "the emulator log carries the reason"
else
  fail "the emulator log was not printed: $OUTPUT"
fi

# (l) The device comes online but the boot never completes ---------------------
echo ""
echo "=== (l) An emulator that never finishes booting is given up on ==="

# The bound a developer who has just raised DEVICE_TIMEOUT meets next, on a
# machine slow enough to have needed that (issue #1156). Everything up to the
# boot wait succeeds, and then sys.boot_completed never reads 1 while the
# emulator stays alive.
SDK_L="$(new_sdk l)"
make_cmdline_tools "$SDK_L/cmdline-tools/latest/bin" 0
make_adb_stub "$SDK_L/platform-tools/adb" "device" ""
UNBOOTED_EMULATOR_PID="$TMPDIR_TESTS/unbooted-emulator.pid"
make_emulator_stub "$SDK_L/emulator/emulator" \
  "emulator: up, still booting" hang "$UNBOOTED_EMULATOR_PID"
run_setup "$SDK_L"

if [[ $RC -eq 1 ]]; then
  pass "the run gives up and exits 1"
else
  fail "expected exit 1 (124 means it hung), got $RC: $OUTPUT"
fi

if grep -qF "ERROR: Emulator did not finish booting within ${BOOT_TIMEOUT}s." <<< "$OUTPUT"; then
  pass "the failure names the bound it waited out"
else
  fail "no boot-timeout message in the failure: $OUTPUT"
fi

# The bound above is a number, so it tells a developer on a slow machine nothing
# about how to raise it. This is the knob, and the wait before this one names
# its own for the same reason.
if grep -qF "BOOT_TIMEOUT" <<< "$OUTPUT"; then
  pass "the failure points at the override that raises the bound"
else
  fail "the failure does not name BOOT_TIMEOUT: $OUTPUT"
fi

# A boot that stalls says nothing through the property being polled, so the
# emulator's own log is all the failure has to offer.
if grep -qF "emulator: up, still booting" <<< "$OUTPUT"; then
  pass "the emulator log is printed with the failure"
else
  fail "the emulator log was not printed: $OUTPUT"
fi

# The device is still there in this case, and that is what makes raising the
# bound the right advice; case (m) is the same failure with a different answer.
if grep -qE "^[[:space:]]+device$" <<< "$OUTPUT"; then
  pass "the failure reports the device adb still sees"
else
  fail "the failure does not report adb's answer: $OUTPUT"
fi

# Both reads of BOOT_POLL_INTERVAL, covered as case (j) covers the device
# wait's: the counter through the progress line, and the sleep through the
# recording stub, because this bound is the one the issue is about and a loop
# that sleeps five seconds per one-second step gives up after 3000 seconds of a
# 600-second bound.
if grep -qF "...waiting for boot (1 / ${BOOT_TIMEOUT}s)" <<< "$OUTPUT"; then
  pass "the wait counts in the steps BOOT_POLL_INTERVAL set"
else
  fail "BOOT_POLL_INTERVAL did not reach the counter: $OUTPUT"
fi

if [[ -s "$SLEPT" && "$(sort -u "$SLEPT")" == "$BOOT_POLL_INTERVAL" ]]; then
  pass "every sleep the wait took was the interval BOOT_POLL_INTERVAL set"
else
  fail "with BOOT_POLL_INTERVAL=$BOOT_POLL_INTERVAL the wait slept: $(tr '\n' ' ' < "$SLEPT")"
fi

# The emulator outlives this failure as it outlives the device wait's, so the
# failure has to say so: the next run's `avdmanager create avd --force` would
# rewrite the AVD underneath it.
if grep -qE "still running as PID [0-9]+" <<< "$OUTPUT"; then
  pass "the surviving emulator is named, with its PID"
else
  fail "the failure does not say the emulator is still running: $OUTPUT"
fi

# Left running by the script, as a real run leaves it, so the suite reaps it.
# The stub execs its sleep, so this PID is the sleep's own and the kill reaches
# it rather than orphaning a child; case (j) asserts that property of the stub.
UNBOOTED_PID="$(cat "$UNBOOTED_EMULATOR_PID" 2>/dev/null || true)"
if [[ -n "$UNBOOTED_PID" ]]; then
  kill "$UNBOOTED_PID" 2>/dev/null || true
  UNBOOTED_REAPED=false
  for _ in $(seq 1 25); do
    if ! kill -0 "$UNBOOTED_PID" 2>/dev/null; then
      UNBOOTED_REAPED=true
      break
    fi
    sleep 0.2
  done
  if [[ "$UNBOOTED_REAPED" == true ]]; then
    pass "the unbooted emulator is gone once killed"
  else
    fail "the unbooted emulator survived the kill (pid $UNBOOTED_PID)"
  fi
else
  fail "the unbooted emulator recorded no pid to reap"
fi

# (m) The device leaves after coming online -----------------------------------
echo ""
echo "=== (m) A device that goes away mid-boot is not blamed on the bound ==="

# The boot wait is entered on one `device` from get-state and never asks again,
# so a device that goes offline or leaves the list looks exactly like a slow
# boot: the property stays unset, and the emulator process stays up, so the
# liveness check passes too. Raising BOOT_TIMEOUT fixes one and not the other,
# so the failure has to carry what adb now says (issue #1156).
SDK_M="$(new_sdk m)"
make_cmdline_tools "$SDK_M/cmdline-tools/latest/bin" 0
make_adb_stub "$SDK_M/platform-tools/adb" "device" "" "no devices/emulators found"
DEPARTED_EMULATOR_PID="$TMPDIR_TESTS/departed-emulator.pid"
make_emulator_stub "$SDK_M/emulator/emulator" \
  "emulator: up, device gone" hang "$DEPARTED_EMULATOR_PID"
run_setup "$SDK_M"

if [[ $RC -eq 1 ]]; then
  pass "the run gives up and exits 1"
else
  fail "expected exit 1 (124 means it hung), got $RC: $OUTPUT"
fi

# The point of the case: the state printed is the one adb reports now, not the
# `device` the wait above saw on its way in.
if grep -qF "error: no devices/emulators found" <<< "$OUTPUT"; then
  pass "the failure carries what adb says about the device now"
else
  fail "the failure does not ask adb again: $OUTPUT"
fi

# And the advice is conditioned on that answer, rather than telling a developer
# whose device has gone to wait longer for it.
if grep -qF 'If that reads "device"' <<< "$OUTPUT"; then
  pass "raising the bound is offered only for a device that is still there"
else
  fail "the failure advises raising BOOT_TIMEOUT unconditionally: $OUTPUT"
fi

# Left running, as in case (l), so the suite reaps it.
DEPARTED_PID="$(cat "$DEPARTED_EMULATOR_PID" 2>/dev/null || true)"
if [[ -n "$DEPARTED_PID" ]]; then
  kill "$DEPARTED_PID" 2>/dev/null || true
  DEPARTED_REAPED=false
  for _ in $(seq 1 25); do
    if ! kill -0 "$DEPARTED_PID" 2>/dev/null; then
      DEPARTED_REAPED=true
      break
    fi
    sleep 0.2
  done
  if [[ "$DEPARTED_REAPED" == true ]]; then
    pass "the emulator left behind is gone once killed"
  else
    fail "the emulator left behind survived the kill (pid $DEPARTED_PID)"
  fi
else
  fail "the emulator left behind recorded no pid to reap"
fi

# (n) The emulator dies between the device appearing and the boot completing ---
echo ""
echo "=== (n) An emulator that exits while booting is reported without waiting ==="

# The bound raised for a slow machine must not also be charged to a run that has
# nothing left to wait for: the property polled below is set by the emulator,
# and a dead emulator will never set it (issue #1156).
SDK_N="$(new_sdk n)"
make_cmdline_tools "$SDK_N/cmdline-tools/latest/bin" 0
make_adb_stub "$SDK_N/platform-tools/adb" "device" ""
make_emulator_stub "$SDK_N/emulator/emulator" "emulator: PANIC: out of memory" exit

# Far beyond run_setup's own 60s bound, as in case (k), so a run that reaches
# this failure by waiting out the clock cannot pass.
BOOT_TIMEOUT_SAVED="$BOOT_TIMEOUT"
export BOOT_TIMEOUT=600
run_setup "$SDK_N"
export BOOT_TIMEOUT="$BOOT_TIMEOUT_SAVED"

if [[ $RC -eq 1 ]]; then
  pass "the run exits 1 long before the 600s bound"
else
  fail "expected exit 1 (124 means it waited), got $RC: $OUTPUT"
fi

if grep -qF "ERROR: The emulator exited before finishing its boot." <<< "$OUTPUT"; then
  pass "the failure names the dead emulator, not a timeout"
else
  fail "no emulator-exited message in the failure: $OUTPUT"
fi

if grep -qF "emulator: PANIC: out of memory" <<< "$OUTPUT"; then
  pass "the emulator log carries the reason"
else
  fail "the emulator log was not printed: $OUTPUT"
fi

# (o) A run in which everything works -----------------------------------------
echo ""
echo "=== (o) A device that comes online carries the run through to the end ==="

SDK_O="$(new_sdk o)"
make_stub "$SDK_O/cmdline-tools/latest/bin/sdkmanager" 0
# Succeeds, but writes to stderr as avdmanager does even when it works.
make_noisy_stub "$SDK_O/cmdline-tools/latest/bin/avdmanager" \
  "Warning: this package is obsolete." 0
make_adb_stub "$SDK_O/platform-tools/adb" "device"
make_emulator_stub "$SDK_O/emulator/emulator" "emulator: booting" exit
run_setup "$SDK_O"

if [[ $RC -eq 0 ]]; then
  pass "the run completes (exit 0)"
else
  fail "expected exit 0, got $RC: $OUTPUT"
fi

if grep -qF "==> Device online." <<< "$OUTPUT"; then
  pass "the device wait passes rather than bounding a working run"
else
  fail "the device wait did not complete: $OUTPUT"
fi

# The captured stderr is the replacement for `2>/dev/null`, so a successful
# creation must still say nothing.
if grep -qF "Warning: this package is obsolete." <<< "$OUTPUT"; then
  fail "avdmanager's stderr was printed although it succeeded: $OUTPUT"
else
  pass "a successful AVD creation stays quiet"
fi

# Summary ----------------------------------------------------------------------
echo ""
echo "Results: $PASS passed, $FAIL failed."
if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
