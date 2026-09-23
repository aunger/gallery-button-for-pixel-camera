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
# the first line that dereferences CMDLINE_TOOLS. That keeps the full-setup
# cases from reaching the emulator launch a few lines later, so no case here
# starts a process or writes an emulator log.
#
# Covers:
#   (a) cmdline-tools/latest/bin holds sdkmanager -> it is the one invoked
#   (b) No cmdline-tools/latest -> cmdline-tools/bin is used instead
#   (c) cmdline-tools/latest present but holding no sdkmanager -> same fallback
#   (d) A failing sdkmanager is not retried at a second path (issue #1133)
#   (e) Neither directory present -> the run fails naming a cmdline-tools path,
#       and the withdrawn tools/bin is never invoked (issues #1127, #1133)
#   (f) --post-boot, the form CI invokes, touches no command-line tool at all
#
# Limits: avdmanager is read out of the same resolved CMDLINE_TOOLS on the line
# after sdkmanager and is not separately exercised, because reaching it means
# letting the run continue into starting the emulator.
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
  # post-boot steps and the emulator once; recording either would drown out the
  # command-line tool invocations these tests assert on.
  local path="$1"
  mkdir -p "$(dirname "$path")"
  {
    echo '#!/usr/bin/env bash'
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
  make_quiet_stub "$sdk/emulator/emulator"
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
  OUTPUT="$(ANDROID_HOME="$sdk" bash "$SETUP" "$@" 2>&1)" || RC=$?
}

invocations() { cat "$INVOKED"; }

# (a) cmdline-tools/latest/bin holds sdkmanager -------------------------------
echo ""
echo "=== (a) sdkmanager in cmdline-tools/latest/bin is the one invoked ==="

SDK_A="$(new_sdk a)"
make_stub "$SDK_A/cmdline-tools/latest/bin/sdkmanager" 1
make_stub "$SDK_A/cmdline-tools/bin/sdkmanager" 1
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
make_stub "$SDK_B/cmdline-tools/bin/sdkmanager" 1
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
make_stub "$SDK_C/cmdline-tools/bin/sdkmanager" 1
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
echo "=== (e) With no command-line tools, tools/bin is never reached ==="

SDK_E="$(new_sdk e)"
run_setup "$SDK_E"

if [[ $RC -ne 0 ]]; then
  pass "the run fails (exit $RC)"
else
  fail "the run succeeded with no command-line tools installed"
fi

if [[ -s "$INVOKED" ]]; then
  fail "tools/bin was invoked: $(invocations)"
else
  pass "the withdrawn tools/bin package was not invoked"
fi

if grep -qF "cmdline-tools" <<< "$OUTPUT"; then
  pass "the failure names a cmdline-tools path"
else
  fail "the failure does not name cmdline-tools: $OUTPUT"
fi

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

# Summary ----------------------------------------------------------------------
echo ""
echo "Results: $PASS passed, $FAIL failed."
if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
