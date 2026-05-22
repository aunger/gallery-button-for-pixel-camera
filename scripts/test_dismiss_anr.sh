#!/usr/bin/env bash
# test_dismiss_anr.sh — Shell-based tests for dismiss_anr.sh.
#
# Covers:
#   (a) --adb argument is used for both logcat and shell subcommands
#   (b) ANR-detection branch: logcat fires → KEYCODE_BACK sent → exits 0
#   (c) idle-count exit path: logcat hangs, CPU goes idle → exits 0
#   (d) Absent Launcher process treated as idle → exits 0
#   (e) Persistent ANR: logcat fires but BACK never clears; 30 s timeout → exits 0
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
# It must handle logcat subcommands (used by the new design) as well as shell calls.
ADB_RECORD_FILE="$TMPDIR_TESTS/adb_invoked"
mock_adb_record="$(make_mock_adb "adb_record" "
touch '$ADB_RECORD_FILE'
case \"\$*\" in
  *'logcat -c'*)              exit 0 ;;
  *'logcat'*)                 sleep 60 ;;
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

# The logcat mock emits the ANR line immediately, then exits.
# After KEYCODE_BACK is sent, cpuinfo reports low CPU so idle_count reaches 2.
mock_adb_anr="$(make_mock_adb "adb_anr" "
case \"\$*\" in
  *'logcat -c'*)
    exit 0
    ;;
  *'logcat'*)
    echo 'E/ActivityManager: ANR in com.google.android.apps.nexuslauncher'
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
# Use SLEEP_AFTER_ANR_DETECTED=1 so the test doesn't spend 7 real seconds waiting.
SLEEP_AFTER_ANR_DETECTED=1 bash "$DISMISS_ANR" --adb "$mock_adb_anr"
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

# Logcat hangs silently (no ANR); cpuinfo always reports low CPU.
mock_adb_idle="$(make_mock_adb "adb_idle" "
case \"\$*\" in
  *'logcat -c'*)
    exit 0
    ;;
  *'logcat'*)
    sleep 60
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
  *'logcat -c'*)   exit 0 ;;
  *'logcat'*)      sleep 60 ;;
  *'dumpsys cpuinfo'*) echo '  3% some.other.process' ;;
  *)               : ;;
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

# Logcat fires immediately (ANR detected), cpuinfo always reports high CPU so
# idle_count never reaches 2, and the script must time out after TIMEOUT=30 s.
# We use SLEEP_AFTER_ANR_DETECTED=1 to avoid spending 7 s waiting for the dialog.
PERSISTENT_ANR_ADB="$(make_mock_adb "adb_persistent_anr" "
case \"\$*\" in
  *'logcat -c'*)
    exit 0
    ;;
  *'logcat'*)
    echo 'E/ActivityManager: ANR in com.google.android.apps.nexuslauncher'
    ;;
  *'dumpsys cpuinfo'*)
    echo '  80% com.google.android.apps.nexuslauncher: launcher'
    ;;
  *'input keyevent KEYCODE_BACK'*)
    :
    ;;
  *)
    :
    ;;
esac
")"

start_ts="$(date +%s)"
SLEEP_AFTER_ANR_DETECTED=1 timeout 35 bash "$DISMISS_ANR" --adb "$PERSISTENT_ANR_ADB"
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

# ── (f) Second-ANR scenario ──────────────────────────────────────────────────
echo ""
echo "=== (f) Second ANR after first is dismissed ==="

# The mock logcat emits the first ANR line immediately, then waits long enough
# for the poll loop to process and dismiss it (SLEEP_AFTER_ANR_DETECTED=1 plus
# the 3 s POLL_INTERVAL), before emitting the second ANR line.  A 5 s gap is
# sufficient: it guarantees the first flag has been consumed and cleared before
# the second ANR is written.  The mock then hangs so the consumer while-read
# loop stays open.  The mock cpuinfo returns high CPU until KEYCODE_BACK has
# been sent twice, then returns low CPU so idle_count reaches 2.
# We verify that KEYCODE_BACK is sent at least twice and the script exits 0.

SECOND_ANR_KEYEVENT_COUNT_FILE="$TMPDIR_TESTS/second_anr_keyevent_count"
echo 0 > "$SECOND_ANR_KEYEVENT_COUNT_FILE"

mock_adb_second_anr="$(make_mock_adb "adb_second_anr" "
case \"\$*\" in
  *'logcat -c'*)
    exit 0
    ;;
  *'logcat'*)
    echo 'E/ActivityManager: ANR in com.google.android.apps.nexuslauncher'
    sleep 5
    echo 'E/ActivityManager: ANR in com.google.android.apps.nexuslauncher'
    sleep 60
    ;;
  *'dumpsys cpuinfo'*)
    count=\$(cat '$SECOND_ANR_KEYEVENT_COUNT_FILE')
    if [[ \$count -lt 2 ]]; then
      echo '  80% com.google.android.apps.nexuslauncher: launcher'
    else
      echo '  1% com.google.android.apps.nexuslauncher: launcher'
    fi
    ;;
  *'input keyevent KEYCODE_BACK'*)
    count=\$(cat '$SECOND_ANR_KEYEVENT_COUNT_FILE')
    count=\$((count + 1))
    echo \$count > '$SECOND_ANR_KEYEVENT_COUNT_FILE'
    ;;
  *)
    :
    ;;
esac
")"

SLEEP_AFTER_ANR_DETECTED=1 bash "$DISMISS_ANR" --adb "$mock_adb_second_anr"
second_anr_status=$?

if [[ $second_anr_status -eq 0 ]]; then
  pass "second ANR: script exits 0"
else
  fail "second ANR: script exited $second_anr_status (expected 0)"
fi

keyevent_count="$(cat "$SECOND_ANR_KEYEVENT_COUNT_FILE")"
if [[ $keyevent_count -ge 2 ]]; then
  pass "second ANR: KEYCODE_BACK sent at least twice (got $keyevent_count)"
else
  fail "second ANR: KEYCODE_BACK sent only $keyevent_count time(s) — expected at least 2"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed."
if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
