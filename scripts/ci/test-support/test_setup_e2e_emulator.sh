#!/usr/bin/env bash
# test_setup_e2e_emulator.sh: Shell-based tests for setup-e2e-emulator.sh's
# resolution of the Android command-line tools.
#
# The script calls sdkmanager and avdmanager by absolute path, out of one
# CMDLINE_TOOLS directory it resolves once. These tests run it against a
# fabricated SDK tree whose command-line tools are stubs that record their own
# path, so each case can assert which directory was used. adb and the emulator
# are stubbed too, but silently: only the tools under test are recorded.
#
# The stub sdkmanager exits 1, which under the script's `set -e` ends the run at
# the install line, the first place the resolved directory is invoked rather
# than merely tested. That keeps the full-setup cases from reaching the emulator
# launch a few lines later, so no case here starts a process or writes an
# emulator log.
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
  RC=0
  # Bounded, because what this suite tests is a script that used to wait for a
  # device forever. A case that regains that behavior should fail the run, not
  # hang the job; `timeout` reports 124, which no assertion here accepts.
  OUTPUT="$(ANDROID_HOME="$sdk" timeout 60 bash "$SETUP" "$@" 2>&1)" || RC=$?
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

# Summary ----------------------------------------------------------------------
echo ""
echo "Results: $PASS passed, $FAIL failed."
if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
