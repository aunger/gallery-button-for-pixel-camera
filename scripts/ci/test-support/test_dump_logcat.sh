#!/usr/bin/env bash
# test_dump_logcat.sh: Shell-based tests for dump_logcat.sh.
#
# Drives the script against a fake adb that prints a fixed logcat and exits
# with a status the test chooses.
#
# Covers:
#   (a) adb is asked for `logcat -d -T <since>`, with <since> as one argument
#   (b) The dump is filtered through filter_logcat.sh and printed to stdout
#   (c) With <out-file>, the same filtered dump is also written to that file
#
# Always exits 0 on success, non-zero on failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DUMP="$SCRIPT_DIR/dump_logcat.sh"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# The fake adb records its arguments, one per line, prints two logcat lines
# (one that filter_logcat.sh keeps and one it drops), and exits $FAKE_ADB_STATUS.
FAKE_ADB="$WORK/adb"
cat > "$FAKE_ADB" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$FAKE_ADB_ARGS"
echo "10-09 02:11:03.100  1204  1204 I GB4PC_E2E: kept line"
echo "10-09 02:11:03.200   900   900 I Launcher: dropped line"
exit "${FAKE_ADB_STATUS:-0}"
EOF
chmod +x "$FAKE_ADB"
export FAKE_ADB_ARGS="$WORK/adb-args"

SINCE="10-09 02:10:21.000"
KEPT="10-09 02:11:03.100  1204  1204 I GB4PC_E2E: kept line"

# ── (a) adb is asked for `logcat -d -T <since>` ─────────────────────────────
echo ""
echo "=== (a) adb is asked for 'logcat -d -T <since>' ==="

bash "$DUMP" "$FAKE_ADB" "$SINCE" > /dev/null
EXPECTED_ARGS="$(printf '%s\n' logcat -d -T "$SINCE")"
ACTUAL_ARGS="$(cat "$FAKE_ADB_ARGS")"
if [[ "$ACTUAL_ARGS" == "$EXPECTED_ARGS" ]]; then
  pass "adb arguments are logcat -d -T '<since>'"
else
  fail "adb arguments were: $(echo "$ACTUAL_ARGS" | tr '\n' ' ')"
fi

# ── (b) Filtered dump on stdout ──────────────────────────────────────────────
echo ""
echo "=== (b) The dump is filtered and printed to stdout ==="

OUTPUT_B="$(bash "$DUMP" "$FAKE_ADB" "$SINCE")"
if [[ "$OUTPUT_B" == "$KEPT" ]]; then
  pass "stdout holds exactly the line filter_logcat.sh keeps"
else
  fail "stdout was: '$OUTPUT_B'"
fi

# ── (c) Filtered dump also written to <out-file> ─────────────────────────────
echo ""
echo "=== (c) With <out-file>, the filtered dump is also written to it ==="

OUT_C="$WORK/out-c.txt"
OUTPUT_C="$(bash "$DUMP" "$FAKE_ADB" "$SINCE" "$OUT_C")"
if [[ "$OUTPUT_C" == "$KEPT" ]]; then
  pass "stdout holds the filtered dump"
else
  fail "stdout was: '$OUTPUT_C'"
fi
if [[ "$(cat "$OUT_C" 2>/dev/null)" == "$KEPT" ]]; then
  pass "out-file holds the filtered dump"
else
  fail "out-file was: '$(cat "$OUT_C" 2>/dev/null)'"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
