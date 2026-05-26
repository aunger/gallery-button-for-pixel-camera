#!/usr/bin/env bash
# test_filter_logcat.sh — Shell-based tests for filter_logcat.sh.
#
# Covers:
#   (a) Lines without base64 blobs are passed through unchanged (if they match the filter)
#   (b) A base64 blob after ";base64," is replaced with "[elided]"
#   (c) The surrounding context (key name, data URI type, closing brace) is preserved
#   (d) Multiple base64 blobs on a single line are each elided
#   (e) Short tokens after ";base64," (fewer than 8 chars) are NOT elided
#   (f) Partial base64 strings that end at end-of-line are elided
#   (g) Lines matching CI-relevant patterns are kept
#   (h) Lines not matching CI-relevant patterns are dropped
#
# Always exits 0 on success, non-zero on failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILTER="$SCRIPT_DIR/filter_logcat.sh"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

# ── (a) Matching lines without base64 blobs pass through unchanged ───────────
echo ""
echo "=== (a) Matching lines without base64 blobs pass through unchanged ==="

INPUT_A="05-25 14:08:16.131  1204  1204 D com.gb4pc: normal log line with no blobs"
OUTPUT_A="$(echo "$INPUT_A" | bash "$FILTER")"

if [[ "$OUTPUT_A" == "$INPUT_A" ]]; then
  pass "normal matching line unchanged"
else
  fail "normal matching line was modified: got '$OUTPUT_A'"
fi

# ── (b) Base64 blob replaced with [elided] ───────────────────────────────────
echo ""
echo "=== (b) Base64 blob after ';base64,' is replaced with '[elided]' ==="

INPUT_B="05-25 14:08:16.131  1204  1204 D com.gb4pc: data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD="
OUTPUT_B="$(echo "$INPUT_B" | bash "$FILTER")"

if echo "$OUTPUT_B" | grep -qF ";base64,[elided]"; then
  pass "blob replaced with [elided]"
else
  fail "blob was not replaced: got '$OUTPUT_B'"
fi

if echo "$OUTPUT_B" | grep -qF "/9j/4AAQ"; then
  fail "original base64 data still present in output: '$OUTPUT_B'"
else
  pass "original base64 data not present in output"
fi

# ── (c) Surrounding context preserved ────────────────────────────────────────
echo ""
echo "=== (c) Surrounding context (key name, data URI type, closing brace) preserved ==="

INPUT_C='05-25 14:08:16.131  1204  1204 D com.gb4pc: extras=Bundle{bitmap_url=data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wCEAAkGBwgHBgkIBwgKCg==}'
OUTPUT_C="$(echo "$INPUT_C" | bash "$FILTER")"

# The prefix before the blob should be preserved.
if echo "$OUTPUT_C" | grep -qF "bitmap_url=data:image/jpeg;base64,[elided]"; then
  pass "key name and data URI type preserved before [elided]"
else
  fail "expected 'bitmap_url=data:image/jpeg;base64,[elided]' in: '$OUTPUT_C'"
fi

# The closing brace after the blob should be preserved.
if echo "$OUTPUT_C" | grep -qF "[elided]}"; then
  pass "closing brace preserved after [elided]"
else
  fail "expected '}' after '[elided]' in: '$OUTPUT_C'"
fi

# ── (d) Multiple blobs on a single line ──────────────────────────────────────
echo ""
echo "=== (d) Multiple base64 blobs on a single line are each elided ==="

INPUT_D='D com.gb4pc: {a=data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAE=, b=data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD=}'
OUTPUT_D="$(echo "$INPUT_D" | bash "$FILTER")"

ELIDED_COUNT="$(echo "$OUTPUT_D" | grep -oF "[elided]" | wc -l)"
if [[ "$ELIDED_COUNT" -eq 2 ]]; then
  pass "two blobs both elided (found [elided] twice)"
else
  fail "expected 2 occurrences of [elided], got $ELIDED_COUNT in: '$OUTPUT_D'"
fi

# ── (e) Short tokens after ";base64," are NOT elided ─────────────────────────
echo ""
echo "=== (e) Short tokens after ';base64,' (fewer than 8 chars) are NOT elided ==="

# A ";base64," followed by fewer than 8 base64 chars should not be treated as a blob.
INPUT_E="D com.gb4pc: ;base64,abc1234"
OUTPUT_E="$(echo "$INPUT_E" | bash "$FILTER")"

if echo "$OUTPUT_E" | grep -qF "[elided]"; then
  fail "short token was incorrectly elided: got '$OUTPUT_E'"
else
  pass "short token (< 8 base64 chars) not elided"
fi

# ── (f) Blob at end of line (no closing delimiter) is elided ─────────────────
echo ""
echo "=== (f) Partial base64 string ending at end-of-line is elided ==="

INPUT_F="D com.gb4pc: data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD"
OUTPUT_F="$(echo "$INPUT_F" | bash "$FILTER")"

if echo "$OUTPUT_F" | grep -qF ";base64,[elided]"; then
  pass "blob at end-of-line replaced with [elided]"
else
  fail "end-of-line blob not elided: got '$OUTPUT_F'"
fi

if echo "$OUTPUT_F" | grep -qF "/9j/4AAQ"; then
  fail "original base64 data still present in output: '$OUTPUT_F'"
else
  pass "original base64 data not present when blob at end-of-line"
fi

# ── (g) Lines matching CI-relevant patterns are kept ─────────────────────────
echo ""
echo "=== (g) Lines matching CI-relevant patterns are kept ==="

declare -A KEPT_CASES
KEPT_CASES["AndroidRuntime"]="05-25 14:08:16.131  1204  1204 E AndroidRuntime: FATAL EXCEPTION: main"
KEPT_CASES["FATAL"]="05-25 14:08:16.131  1204  1204 E SomeTag: FATAL: something went wrong"
KEPT_CASES["Process crashed"]="05-25 14:08:16.131  1204  1204 I ActivityManager: Process crashed"
KEPT_CASES["com.gb4pc"]="05-25 14:08:16.131  1204  1204 D com.gb4pc.app: some message"
KEPT_CASES["OverlayService"]="05-25 14:08:16.131  1204  1204 D OverlayService: some message"
KEPT_CASES["MockCamera"]="05-25 14:08:16.131  1204  1204 D MockCamera: some message"
KEPT_CASES["CameraService"]="05-25 14:08:16.131  1204  1204 D CameraService: some message"
KEPT_CASES["E/<tag>"]="05-25 14:08:16.131  1204  1204 E/SomeTag: some error"

for LABEL in "${!KEPT_CASES[@]}"; do
  LINE="${KEPT_CASES[$LABEL]}"
  OUTPUT="$(echo "$LINE" | bash "$FILTER")"
  if [[ "$OUTPUT" == "$LINE" ]]; then
    pass "line with '$LABEL' kept"
  else
    fail "line with '$LABEL' was dropped or modified: got '$OUTPUT'"
  fi
done

# ── (h) Lines not matching CI-relevant patterns are dropped ───────────────────
echo ""
echo "=== (h) Lines not matching CI-relevant patterns are dropped ==="

IRRELEVANT="05-25 14:08:16.131  1204  1204 D SomeRandomTag: unrelated log message"
OUTPUT_H="$(echo "$IRRELEVANT" | bash "$FILTER")"

if [[ -z "$OUTPUT_H" ]]; then
  pass "irrelevant line dropped"
else
  fail "irrelevant line was not dropped: got '$OUTPUT_H'"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed."
if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
