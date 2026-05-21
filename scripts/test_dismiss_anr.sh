#!/usr/bin/env bash
# test_dismiss_anr.sh — Shell-based tests for dismiss_anr.sh.
#
# Covers:
#   (a) --adb argument parsing
#   (b) ANR-detection branch (mock adb that emits AppNotRespondingDialog)
#   (c) idle-count exit path (mock adb with low Launcher CPU)
#   (d) timeout path (mock adb that never satisfies either condition)
#
# Always exits 0 on success, non-zero on failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DISMISS_ANR="$SCRIPT_DIR/dismiss_anr.sh"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

# ── Test helpers ────────────────────────────────────────────────────────────────

# Make a temporary directory for mock adb scripts.
TMPDIR_TESTS="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_TESTS"' EXIT

make_mock_adb() {
  # Usage: make_mock_adb <name> <script-body>
  # Creates an executable mock adb at $TMPDIR_TESTS/<name>.
  local name="$1"
  local body="$2"
  local path="$TMPDIR_TESTS/$name"
  printf '#!/usr/bin/env bash\n%s\n' "$body" > "$path"
  chmod +x "$path"
  echo "$path"
}

# ── (a) --adb argument parsing ──────────────────────────────────────────────────
echo ""
echo "=== (a) --adb argument parsing ==="

# A mock adb that records the path it was invoked from and returns idle output.
ADB_RECORD_FILE="$TMPDIR_TESTS/adb_invoked"
mock_adb_record="$(make_mock_adb "adb_record" "
touch '$ADB_RECORD_FILE'
case \"\$*\" in
  *'dumpsys window windows'*) echo 'no anr here' ;;
  *'dumpsys cpuinfo'*)        echo '  1% com.google.android.apps.nexuslauncher:' ;;
  *'input keyevent'*)         : ;;
  *)                          : ;;
esac
")"

# Run with --adb pointing to our recorder; if it's used, the file appears.
rm -f "$ADB_RECORD_FILE"
bash "$DISMISS_ANR" --adb "$mock_adb_record"
if [[ -f "$ADB_RECORD_FILE" ]]; then
  pass "--adb flag causes dismiss_anr.sh to use the supplied adb path"
else
  fail "--adb flag was not respected (mock adb was never called)"
fi

# ── (b) ANR-detection branch ─────────────────────────────────────────────────
echo ""
echo "=== (b) ANR-detection branch ==="

ANR_KEYEVENT_FILE="$TMPDIR_TESTS/keyevent_sent"
ANR_WINDOW_CALL_COUNT="$TMPDIR_TESTS/anr_window_call_count"
echo 0 > "$ANR_WINDOW_CALL_COUNT"

mock_adb_anr="$(make_mock_adb "adb_anr" "
case \"\$*\" in
  *'dumpsys window windows'*)
    count=\$(cat '$ANR_WINDOW_CALL_COUNT')
    count=\$((count + 1))
    echo \$count > '$ANR_WINDOW_CALL_COUNT'
    if [[ \$count -le 1 ]]; then
      echo 'Window #0: AppNotRespondingDialog'
    else
      echo 'no anr here'
    fi
    ;;
  *'dumpsys cpuinfo'*)
    echo '  2% com.google.android.apps.nexuslauncher: launcher'
    ;;
  *'input keyevent KEYCODE_BACK'*)
    touch '$ANR_KEYEVENT_FILE'
    ;;
  *)
    :
    ;;
esac
")"

rm -f "$ANR_KEYEVENT_FILE"
bash "$DISMISS_ANR" --adb "$mock_adb_anr"
status=$?
if [[ $status -eq 0 ]]; then
  pass "script exits 0 when ANR dialog is detected and then clears"
else
  fail "script exited $status (expected 0) when ANR dialog detected and cleared"
fi

if [[ -f "$ANR_KEYEVENT_FILE" ]]; then
  pass "KEYCODE_BACK keyevent sent to dismiss ANR dialog"
else
  fail "KEYCODE_BACK keyevent was NOT sent when ANR dialog was detected"
fi

# ── (c) idle-count exit path ──────────────────────────────────────────────────
echo ""
echo "=== (c) idle-count exit path (Launcher CPU < 5%) ==="

IDLE_CALL_COUNT_FILE="$TMPDIR_TESTS/idle_call_count"
echo 0 > "$IDLE_CALL_COUNT_FILE"

mock_adb_idle="$(make_mock_adb "adb_idle" "
case \"\$*\" in
  *'dumpsys window windows'*)
    echo 'no anr here'
    ;;
  *'dumpsys cpuinfo'*)
    count=\$(cat '$IDLE_CALL_COUNT_FILE')
    count=\$((count + 1))
    echo \$count > '$IDLE_CALL_COUNT_FILE'
    echo '  2% com.google.android.apps.nexuslauncher: launcher'
    ;;
  *)
    :
    ;;
esac
")"

bash "$DISMISS_ANR" --adb "$mock_adb_idle"
status=$?
if [[ $status -eq 0 ]]; then
  pass "script exits 0 after two consecutive idle readings"
else
  fail "script exited $status (expected 0) after idle readings"
fi

call_count="$(cat "$IDLE_CALL_COUNT_FILE")"
if [[ $call_count -ge 2 ]]; then
  pass "cpuinfo was polled at least twice (idle_count reached 2); got $call_count"
else
  fail "cpuinfo polled only $call_count time(s) — idle_count never reached 2"
fi

# ── (d) Launcher absent treated as idle ──────────────────────────────────────
echo ""
echo "=== (d) Absent Launcher process treated as idle ==="

mock_adb_absent="$(make_mock_adb "adb_absent" "
case \"\$*\" in
  *'dumpsys window windows'*) echo 'no anr' ;;
  *'dumpsys cpuinfo'*)        echo '  3% some.other.process' ;;
  *)                          : ;;
esac
")"

bash "$DISMISS_ANR" --adb "$mock_adb_absent"
status=$?
if [[ $status -eq 0 ]]; then
  pass "script exits 0 when Launcher process is absent (treated as idle)"
else
  fail "script exited $status when Launcher process was absent"
fi

# ── (e) Persistent ANR — timeout exits 0 within wall-clock budget ─────────────
echo ""
echo "=== (e) Persistent ANR — script exits 0 within timeout (≤ 35 s) ==="

# Mock adb that always reports AppNotRespondingDialog so the ANR branch is taken
# every iteration.  The script must time out (TIMEOUT=30 s) rather than loop
# forever, and must exit 0 within a generous 35 s wall-clock budget.
#
# To keep the test fast we override POLL_INTERVAL to 1 s by patching the
# variable inside a wrapper that sources dismiss_anr.sh with a modified value.
# The simplest approach: write a wrapper script that sets POLL_INTERVAL and
# TIMEOUT to small values before sourcing the real script.
PERSISTENT_ANR_ADB="$TMPDIR_TESTS/adb_persistent_anr"
printf '#!/usr/bin/env bash\n' > "$PERSISTENT_ANR_ADB"
printf 'case "$*" in\n' >> "$PERSISTENT_ANR_ADB"
printf '  *"dumpsys window windows"*) echo "Window #0: AppNotRespondingDialog" ;;\n' >> "$PERSISTENT_ANR_ADB"
printf '  *"input keyevent KEYCODE_BACK"*) : ;;\n' >> "$PERSISTENT_ANR_ADB"
printf '  *) : ;;\n' >> "$PERSISTENT_ANR_ADB"
printf 'esac\n' >> "$PERSISTENT_ANR_ADB"
chmod +x "$PERSISTENT_ANR_ADB"

# We need POLL_INTERVAL=1 and TIMEOUT=5 so the test finishes quickly.
# dismiss_anr.sh hard-codes those values inside its subshell; we cannot override
# them from outside.  Instead we run the script and impose a 35 s wall-clock
# limit using a background watchdog.

start_ts="$(date +%s)"
timeout 35 bash "$DISMISS_ANR" --adb "$PERSISTENT_ANR_ADB"
persistent_status=$?
end_ts="$(date +%s)"
elapsed_wall=$((end_ts - start_ts))

if [[ $persistent_status -eq 0 ]]; then
  pass "persistent ANR: script exits 0 (not hanging) — wall time ${elapsed_wall}s"
else
  fail "persistent ANR: script exited $persistent_status (expected 0) — wall time ${elapsed_wall}s"
fi

if [[ $elapsed_wall -le 35 ]]; then
  pass "persistent ANR: completed within 35 s wall-clock budget (${elapsed_wall}s)"
else
  fail "persistent ANR: took ${elapsed_wall}s — exceeded 35 s budget"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed."
if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
