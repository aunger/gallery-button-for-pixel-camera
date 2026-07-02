#!/usr/bin/env bash
# test_dismiss_anr.sh--Shell-based tests for dismiss_anr.sh.
#
# Covers:
#   (a) --adb argument is used for both logcat and shell subcommands
#   (b) ANR-detection branch: logcat fires → KEYCODE_BACK sent → exits 0
#   (c) idle-count exit path: logcat hangs, CPU goes idle → exits 0
#   (d) Absent Launcher process treated as idle → exits 0
#   (e) Persistent ANR: logcat fires but BACK never clears; timeout → exits 0
#   (j) Pattern fallback exercises the nexuslauncher arm of nexuslauncher|launcher3
#
# Always exits 0 on success, non-zero on failure.
#
# dismiss_anr.sh's poll loop performs real wall-clock sleeps against a mock
# adb, and its elapsed-time bookkeeping is bash integer arithmetic, so
# POLL_INTERVAL and TIMEOUT must stay whole seconds. They are exported here
# at compressed values (real default: 3 s / 30 s) to keep this suite fast
# without changing the number of polls, or the pass/fail outcome, any
# scenario exercises. TIMEOUT=8 leaves headroom for the scenarios below that
# need several poll iterations (g, h) or a deliberate timeout (e).
export POLL_INTERVAL=1
export TIMEOUT=8

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
  *'cmd package resolve-activity'*)
    echo 'com.google.android.apps.nexuslauncher/.NexusLauncherActivity'
    ;;
  *'logcat -c'*)              exit 0 ;;
  *'logcat -d'*)              exit 0 ;;
  *'logcat'*)                 exec sleep 60 ;;
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
# After KEYCODE_ENTER is sent, cpuinfo reports low CPU so idle_count reaches 2.
mock_adb_anr="$(make_mock_adb "adb_anr" "
case \"\$*\" in
  *'cmd package resolve-activity'*)
    echo 'com.google.android.apps.nexuslauncher/.NexusLauncherActivity'
    ;;
  *'logcat -c'*)
    exit 0
    ;;
  *'logcat -d'*)
    exit 0
    ;;
  *'logcat'*)
    echo 'E/ActivityManager: ANR in com.google.android.apps.nexuslauncher'
    ;;
  *'dumpsys cpuinfo'*)
    echo '  2% com.google.android.apps.nexuslauncher: launcher'
    ;;
  *'input keyevent KEYCODE_ENTER'*)
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
  pass "KEYCODE_ENTER keyevent sent to dismiss ANR dialog"
else
  fail "KEYCODE_ENTER keyevent was NOT sent when ANR dialog was detected"
fi

# ── (c) idle-count exit path ──────────────────────────────────────────────────
echo ""
echo "=== (c) idle-count exit path (Launcher CPU < 5%) ==="

IDLE_CALL_COUNT_FILE="$TMPDIR_TESTS/idle_call_count"
echo 0 > "$IDLE_CALL_COUNT_FILE"

# Logcat hangs silently (no ANR); cpuinfo always reports low CPU.
mock_adb_idle="$(make_mock_adb "adb_idle" "
case \"\$*\" in
  *'cmd package resolve-activity'*)
    echo 'com.google.android.apps.nexuslauncher/.NexusLauncherActivity'
    ;;
  *'logcat -c'*)
    exit 0
    ;;
  *'logcat -d'*)
    exit 0
    ;;
  *'logcat'*)
    exec sleep 60
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
  fail "cpuinfo polled only $call_count time(s); idle_count never reached 2"
fi

# ── (d) Launcher absent treated as idle ──────────────────────────────────────
echo ""
echo "=== (d) Absent Launcher process treated as idle ==="

mock_adb_absent="$(make_mock_adb "adb_absent" "
case \"\$*\" in
  *'cmd package resolve-activity'*)
    echo 'com.google.android.apps.nexuslauncher/.NexusLauncherActivity'
    ;;
  *'logcat -c'*)   exit 0 ;;
  *'logcat -d'*)   exit 0 ;;
  *'logcat'*)      exec sleep 60 ;;
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

# (e) Persistent ANR: timeout exits 0 within wall-clock budget----------------
echo ""
echo "=== (e) Persistent ANR: script exits 0 within timeout (≤ 10 s) ==="

# Logcat fires immediately (ANR detected), cpuinfo always reports high CPU so
# idle_count never reaches 2, and the script must time out.  This scenario
# overrides TIMEOUT down to 3 s (below the suite-wide 8 s default) since it
# deliberately pays the full timeout and doesn't need the extra headroom (g)
# and (h) require for their longer poll sequences.
# We use SLEEP_AFTER_ANR_DETECTED=1 to avoid spending 7 s waiting for the dialog.
PERSISTENT_ANR_ADB="$(make_mock_adb "adb_persistent_anr" "
case \"\$*\" in
  *'cmd package resolve-activity'*)
    echo 'com.google.android.apps.nexuslauncher/.NexusLauncherActivity'
    ;;
  *'logcat -c'*)
    exit 0
    ;;
  *'logcat -d'*)
    exit 0
    ;;
  *'logcat'*)
    echo 'E/ActivityManager: ANR in com.google.android.apps.nexuslauncher'
    ;;
  *'dumpsys cpuinfo'*)
    echo '  80% com.google.android.apps.nexuslauncher: launcher'
    ;;
  *'input keyevent KEYCODE_ENTER'*)
    :
    ;;
  *)
    :
    ;;
esac
")"

start_ts="$(date +%s)"
SLEEP_AFTER_ANR_DETECTED=1 TIMEOUT=3 timeout 10 bash "$DISMISS_ANR" --adb "$PERSISTENT_ANR_ADB"
persistent_status=$?
end_ts="$(date +%s)"
elapsed_wall=$((end_ts - start_ts))

if [[ $persistent_status -eq 0 ]]; then
  pass "persistent ANR: script exits 0 (not hanging); wall time ${elapsed_wall}s"
else
  fail "persistent ANR: script exited $persistent_status (expected 0); wall time ${elapsed_wall}s"
fi

if [[ $elapsed_wall -le 10 ]]; then
  pass "persistent ANR: completed within 10 s wall-clock budget (${elapsed_wall}s)"
else
  fail "persistent ANR: took ${elapsed_wall}s; exceeded 10 s budget"
fi

# ── (f) Second-ANR scenario ──────────────────────────────────────────────────
echo ""
echo "=== (f) Second ANR after first is dismissed ==="

# The mock logcat emits the first ANR line immediately, then waits long enough
# for the poll loop to process and dismiss it (SLEEP_AFTER_ANR_DETECTED=1 plus
# the compressed 1 s POLL_INTERVAL exported above), before emitting the second
# ANR line.  A 3 s gap is sufficient: it guarantees the first flag has been
# consumed and cleared before the second ANR is written, while staying well
# under the compressed TIMEOUT=8 s budget once the post-second-ANR dismiss and
# idle polls are added.  The mock then hangs so the consumer while-read loop
# stays open.  The mock cpuinfo returns high CPU until KEYCODE_ENTER has been
# sent twice, then returns low CPU so idle_count reaches 2.
# We verify that KEYCODE_ENTER is sent at least twice and the script exits 0.

SECOND_ANR_KEYEVENT_COUNT_FILE="$TMPDIR_TESTS/second_anr_keyevent_count"
echo 0 > "$SECOND_ANR_KEYEVENT_COUNT_FILE"

mock_adb_second_anr="$(make_mock_adb "adb_second_anr" "
case \"\$*\" in
  *'cmd package resolve-activity'*)
    echo 'com.google.android.apps.nexuslauncher/.NexusLauncherActivity'
    ;;
  *'logcat -c'*)
    exit 0
    ;;
  *'logcat -d'*)
    exit 0
    ;;
  *'logcat'*)
    echo 'E/ActivityManager: ANR in com.google.android.apps.nexuslauncher'
    sleep 3
    echo 'E/ActivityManager: ANR in com.google.android.apps.nexuslauncher'
    exec sleep 60
    ;;
  *'dumpsys cpuinfo'*)
    count=\$(cat '$SECOND_ANR_KEYEVENT_COUNT_FILE')
    if [[ \$count -lt 2 ]]; then
      echo '  80% com.google.android.apps.nexuslauncher: launcher'
    else
      echo '  1% com.google.android.apps.nexuslauncher: launcher'
    fi
    ;;
  *'input keyevent KEYCODE_ENTER'*)
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
  pass "second ANR: KEYCODE_ENTER sent at least twice (got $keyevent_count)"
else
  fail "second ANR: KEYCODE_ENTER sent only $keyevent_count time(s); expected at least 2"
fi

# ── (g) idle_count=2 but ANR dialog still present (dumpsys window fallback) ───
echo ""
echo "=== (g) idle_count=2 but ANR dialog still present: dumpsys window fallback ==="

# Scenario: logcat never fires (CPU settled without logcat trigger), but the ANR
# dialog is still on screen.  dumpsys window returns the ANR window title on the
# first idle-exit check, KEYCODE_BACK is sent, idle_count resets to 0.  On the
# second idle-exit check dumpsys window is clean, so the script exits 0.
# We verify that KEYCODE_BACK was sent exactly once and the script exits 0.

DUMPSYS_WINDOW_CALL_COUNT_FILE="$TMPDIR_TESTS/dumpsys_window_call_count"
echo 0 > "$DUMPSYS_WINDOW_CALL_COUNT_FILE"
DUMPSYS_WINDOW_KEYEVENT_FILE="$TMPDIR_TESTS/dumpsys_window_keyevent"

mock_adb_dumpsys_anr="$(make_mock_adb "adb_dumpsys_anr" "
case \"\$*\" in
  *'cmd package resolve-activity'*)
    echo 'com.google.android.apps.nexuslauncher/.NexusLauncherActivity'
    ;;
  *'logcat -c'*)
    exit 0
    ;;
  *'logcat -d'*)
    exit 0
    ;;
  *'logcat'*)
    # Hang silently; logcat never fires.
    exec sleep 60
    ;;
  *'dumpsys cpuinfo'*)
    # Always report low CPU so idle_count increments every iteration.
    echo '  1% com.google.android.apps.nexuslauncher: launcher'
    ;;
  *'dumpsys window'*)
    count=\$(cat '$DUMPSYS_WINDOW_CALL_COUNT_FILE')
    count=\$((count + 1))
    echo \$count > '$DUMPSYS_WINDOW_CALL_COUNT_FILE'
    if [[ \$count -le 1 ]]; then
      # First check: dialog still present.
      echo '  mCurrentFocus=Window{... u0 Application Not Responding: com.google.android.apps.nexuslauncher}'
    else
      # Second check: dialog gone.
      echo 'WindowState idle'
    fi
    ;;
  *'input keyevent KEYCODE_ENTER'*)
    touch '$DUMPSYS_WINDOW_KEYEVENT_FILE'
    ;;
  *)
    :
    ;;
esac
")"

bash "$DISMISS_ANR" --adb "$mock_adb_dumpsys_anr"
dumpsys_status=$?

if [[ $dumpsys_status -eq 0 ]]; then
  pass "dumpsys window fallback: script exits 0"
else
  fail "dumpsys window fallback: script exited $dumpsys_status (expected 0)"
fi

if [[ -f "$DUMPSYS_WINDOW_KEYEVENT_FILE" ]]; then
  pass "dumpsys window fallback: KEYCODE_ENTER sent when ANR dialog detected via dumpsys window"
else
  fail "dumpsys window fallback: KEYCODE_ENTER was NOT sent when ANR dialog was found via dumpsys window"
fi

dumpsys_call_count="$(cat "$DUMPSYS_WINDOW_CALL_COUNT_FILE")"
if [[ $dumpsys_call_count -ge 2 ]]; then
  pass "dumpsys window fallback: dumpsys window was checked more than once (got $dumpsys_call_count); loop continued after first dialog detection"
else
  fail "dumpsys window fallback: dumpsys window checked only $dumpsys_call_count time(s); loop should have continued"
fi

# ── (h) Unknown CPU reading does not increment idle_count ────────────────────
echo ""
echo "=== (h) Unknown CPU reading does not increment idle_count ==="

# Scenario: cpuinfo returns a nexuslauncher line that has no leading integer
# (the percentage is unparseable, e.g. the line begins with a non-digit).
# The script should NOT treat this as idle and should NOT exit after two such
# readings.  Instead it skips those iterations, waiting for a known reading.
# After two unparseable readings followed by two known-low readings the script
# exits 0; confirming that idle_count was not incremented during the unknown
# readings.
#
# To verify the "skip" behaviour we count how many times cpuinfo is polled:
# if idle_count had been incremented on the unknown readings, the script would
# exit after 2 polls (2 unknown readings → idle_count=2 → exit).  With the
# fix, it needs at least 4 polls (2 unknown + 2 known-low) before exiting.

UNKNOWN_CPU_CALL_COUNT_FILE="$TMPDIR_TESTS/unknown_cpu_call_count"
echo 0 > "$UNKNOWN_CPU_CALL_COUNT_FILE"

mock_adb_unknown_cpu="$(make_mock_adb "adb_unknown_cpu" "
case \"\$*\" in
  *'cmd package resolve-activity'*)
    echo 'com.google.android.apps.nexuslauncher/.NexusLauncherActivity'
    ;;
  *'logcat -c'*)
    exit 0
    ;;
  *'logcat -d'*)
    exit 0
    ;;
  *'logcat'*)
    exec sleep 60
    ;;
  *'dumpsys cpuinfo'*)
    count=\$(cat '$UNKNOWN_CPU_CALL_COUNT_FILE')
    count=\$((count + 1))
    echo \$count > '$UNKNOWN_CPU_CALL_COUNT_FILE'
    if [[ \$count -le 2 ]]; then
      # First two polls: line exists but percentage is not a leading integer.
      echo '  (unknown) com.google.android.apps.nexuslauncher: launcher'
    else
      # Subsequent polls: known low CPU so idle_count increments.
      echo '  2% com.google.android.apps.nexuslauncher: launcher'
    fi
    ;;
  *)
    :
    ;;
esac
")"

bash "$DISMISS_ANR" --adb "$mock_adb_unknown_cpu"
unknown_status=$?

if [[ $unknown_status -eq 0 ]]; then
  pass "unknown CPU: script exits 0"
else
  fail "unknown CPU: script exited $unknown_status (expected 0)"
fi

unknown_call_count="$(cat "$UNKNOWN_CPU_CALL_COUNT_FILE")"
if [[ $unknown_call_count -ge 4 ]]; then
  pass "unknown CPU: cpuinfo polled at least 4 times (unknown readings did not increment idle_count); got $unknown_call_count"
else
  fail "unknown CPU: cpuinfo polled only $unknown_call_count time(s); unknown readings must have incorrectly incremented idle_count (expected >= 4)"
fi

# ── (i) Fallback: cmd package resolve-activity returns nothing ────────────────
echo ""
echo "=== (i) Fallback: launcher detection fails → pattern fallback ==="

# When cmd package resolve-activity returns no output, the script should fall
# back to grep -iE "nexuslauncher|launcher3" for CPU checks and match ANR lines
# by pattern in logcat.  We simulate both: logcat emits an ANR for
# com.android.launcher3, and cpuinfo reports it as busy then idle.
FALLBACK_KEYEVENT_FILE="$TMPDIR_TESTS/fallback_keyevent"
FALLBACK_CPUINFO_COUNT_FILE="$TMPDIR_TESTS/fallback_cpu_count"
echo 0 > "$FALLBACK_CPUINFO_COUNT_FILE"

mock_adb_fallback="$(make_mock_adb "adb_fallback" "
case \"\$*\" in
  *'cmd package resolve-activity'*)
    # Return nothing; forces pattern fallback.
    exit 0
    ;;
  *'logcat -c'*)
    exit 0
    ;;
  *'logcat -d'*)
    exit 0
    ;;
  *'logcat'*)
    echo 'E/ActivityManager: ANR in com.android.launcher3'
    exec sleep 60
    ;;
  *'dumpsys cpuinfo'*)
    count=\$(cat '$FALLBACK_CPUINFO_COUNT_FILE')
    count=\$((count + 1))
    echo \$count > '$FALLBACK_CPUINFO_COUNT_FILE'
    if [[ \$count -le 2 ]]; then
      echo '  60% com.android.launcher3: launcher'
    else
      echo '  1% com.android.launcher3: launcher'
    fi
    ;;
  *'input keyevent KEYCODE_ENTER'*)
    touch '$FALLBACK_KEYEVENT_FILE'
    ;;
  *)
    :
    ;;
esac
")"

SLEEP_AFTER_ANR_DETECTED=1 bash "$DISMISS_ANR" --adb "$mock_adb_fallback"
fallback_status=$?

if [[ $fallback_status -eq 0 ]]; then
  pass "pattern fallback: script exits 0"
else
  fail "pattern fallback: script exited $fallback_status (expected 0)"
fi

if [[ -f "$FALLBACK_KEYEVENT_FILE" ]]; then
  pass "pattern fallback: KEYCODE_ENTER sent when launcher3 ANR detected via pattern"
else
  fail "pattern fallback: KEYCODE_ENTER was NOT sent for launcher3 ANR; pattern fallback may be broken"
fi

# ── (j) Fallback: nexuslauncher arm of pattern ────────────────────────────────
echo ""
echo "=== (j) Fallback: nexuslauncher arm exercised via pattern ==="

# Companion to section (i): that test exercises only the launcher3 arm of the
# "nexuslauncher|launcher3" fallback pattern, so a typo in the nexuslauncher arm
# (e.g. "pixellauncher|launcher3") would go undetected.  Here we force the
# pattern fallback again (resolve-activity returns nothing) but emit a
# nexuslauncher ANR line and report nexuslauncher in cpuinfo.  This MUST fail if
# the fallback pattern is "pixellauncher|launcher3" and pass if it is
# "nexuslauncher|launcher3".
FALLBACK_NEXUS_KEYEVENT_FILE="$TMPDIR_TESTS/fallback_nexus_keyevent"
FALLBACK_NEXUS_CPUINFO_COUNT_FILE="$TMPDIR_TESTS/fallback_nexus_cpu_count"
echo 0 > "$FALLBACK_NEXUS_CPUINFO_COUNT_FILE"

mock_adb_fallback_nexus="$(make_mock_adb "adb_fallback_nexus" "
case \"\$*\" in
  *'cmd package resolve-activity'*)
    # Return nothing; forces pattern fallback.
    exit 0
    ;;
  *'logcat -c'*)
    exit 0
    ;;
  *'logcat -d'*)
    exit 0
    ;;
  *'logcat'*)
    echo 'E/ActivityManager: ANR in com.google.android.apps.nexuslauncher'
    exec sleep 60
    ;;
  *'dumpsys cpuinfo'*)
    count=\$(cat '$FALLBACK_NEXUS_CPUINFO_COUNT_FILE')
    count=\$((count + 1))
    echo \$count > '$FALLBACK_NEXUS_CPUINFO_COUNT_FILE'
    if [[ \$count -le 2 ]]; then
      echo '  60% com.google.android.apps.nexuslauncher: launcher'
    else
      echo '  1% com.google.android.apps.nexuslauncher: launcher'
    fi
    ;;
  *'input keyevent KEYCODE_ENTER'*)
    touch '$FALLBACK_NEXUS_KEYEVENT_FILE'
    ;;
  *)
    :
    ;;
esac
")"

SLEEP_AFTER_ANR_DETECTED=1 bash "$DISMISS_ANR" --adb "$mock_adb_fallback_nexus"
fallback_nexus_status=$?

if [[ $fallback_nexus_status -eq 0 ]]; then
  pass "nexuslauncher fallback: script exits 0"
else
  fail "nexuslauncher fallback: script exited $fallback_nexus_status (expected 0)"
fi

if [[ -f "$FALLBACK_NEXUS_KEYEVENT_FILE" ]]; then
  pass "nexuslauncher fallback: KEYCODE_ENTER sent when nexuslauncher ANR detected via pattern"
else
  fail "nexuslauncher fallback: KEYCODE_ENTER was NOT sent for nexuslauncher ANR; fallback pattern may be wrong (e.g. pixellauncher typo)"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed."
if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
