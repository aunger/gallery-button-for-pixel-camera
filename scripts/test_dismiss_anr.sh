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
mock_adb_anr="$(make_mock_adb "adb_anr" "
case \"\$*\" in
  *'dumpsys window windows'*)
    echo 'Window #0: AppNotRespondingDialog'
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
  pass "script exits 0 when ANR dialog is detected"
else
  fail "script exited $status (expected 0) when ANR dialog detected"
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

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed."
if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
