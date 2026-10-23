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
#   (d) An adb that exits 255 partway through leaves the caller's `bash -e`,
#       `pipefail` shell running, and its partial output is kept (issue #1280)
#   (e) An adb that cannot be run at all also exits 0
#   (f) A usage error is not swallowed: it still exits 2
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

# ── (d) A failed adb still exits 0, under the E2E steps' shell options ──────
echo ""
echo "=== (d) A failed adb exits 0, even under 'bash -e' with pipefail ==="

# The E2E steps call the script from a `bash -e` shell with pipefail set, so
# drive it from one too; 255 is the status of the dump in issue #1280.
OUT_D="$WORK/out-d.txt"
STATUS_D=0
OUTPUT_D="$(FAKE_ADB_STATUS=255 bash -e -o pipefail -c \
  'bash "$1" "$2" "$3" "$4"; echo "after-dump"' _ "$DUMP" "$FAKE_ADB" "$SINCE" "$OUT_D")" \
  || STATUS_D=$?
if [[ "$STATUS_D" -eq 0 ]]; then
  pass "caller exits 0 when adb exits 255"
else
  fail "caller exited $STATUS_D when adb exited 255"
fi
if [[ "$OUTPUT_D" == "$KEPT"$'\n'"after-dump" ]]; then
  pass "caller's next command still runs, after the partial dump"
else
  fail "caller's output was: '$OUTPUT_D'"
fi
if [[ "$(cat "$OUT_D" 2>/dev/null)" == "$KEPT" ]]; then
  pass "out-file keeps what adb printed before failing"
else
  fail "out-file was: '$(cat "$OUT_D" 2>/dev/null)'"
fi

# ── (e) A missing adb still exits 0 ─────────────────────────────────────────
echo ""
echo "=== (e) An adb that cannot be run at all exits 0 ==="

STATUS_E=0
bash "$DUMP" "$WORK/no-such-adb" "$SINCE" > /dev/null 2>&1 || STATUS_E=$?
if [[ "$STATUS_E" -eq 0 ]]; then
  pass "exits 0 when adb is missing"
else
  fail "exited $STATUS_E when adb is missing"
fi

# ── (f) A usage error is not swallowed ───────────────────────────────────────
echo ""
echo "=== (f) A call with too few arguments still fails ==="

STATUS_F=0
bash "$DUMP" "$FAKE_ADB" > /dev/null 2>&1 || STATUS_F=$?
if [[ "$STATUS_F" -eq 2 ]]; then
  pass "exits 2 on a usage error"
else
  fail "exited $STATUS_F on a usage error, expected 2"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
