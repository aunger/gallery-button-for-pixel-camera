#!/usr/bin/env bash
# test_ocr_screenshots.sh--Shell-based tests for ocr_screenshots.sh.
#
# Covers:
#   (a) Normal operation: PNGs processed, .ocr.txt companion files created
#   (b) GITHUB_STEP_SUMMARY written with header and one row per screenshot
#   (c) First 80 chars of OCR text appear in the summary row
#   (d) No PNGs in directory: exits 0, no summary header written
#   (e) Directory not found: exits 1
#   (f) Wrong number of arguments: exits 1
#   (g) Tesseract failure on a file is non-fatal; row shows "(OCR failed)"
#
# Always exits 0 on success, non-zero on failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OCR_SCRIPT="$SCRIPT_DIR/ocr_screenshots.sh"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

# ── Shared temp area ────────────────────────────────────────────────────────────

TMPDIR_TESTS="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_TESTS"' EXIT

# Helper: create a minimal 1×1 white PNG without requiring ImageMagick.
# A valid PNG is not needed for OCR tests because tesseract is mocked.
make_png() {
  local path="$1"
  # Smallest valid PNG (1×1 white pixel, base64-encoded).
  printf '\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82' > "$path"
}

# Helper: create a mock tesseract executable in a temp dir and prepend to PATH.
# The mock writes the supplied text to <base>.txt and exits 0.
make_mock_tesseract() {
  local mock_dir="$1"
  local output_text="$2"
  mkdir -p "$mock_dir"
  cat > "$mock_dir/tesseract" <<EOF
#!/usr/bin/env bash
# mock tesseract: write text to the output file
INPUT="\$1"
BASE="\$2"
printf '%s' "$output_text" > "\${BASE}.txt"
exit 0
EOF
  chmod +x "$mock_dir/tesseract"
}

# Helper: create a mock tesseract that always fails (exit 1).
make_failing_tesseract() {
  local mock_dir="$1"
  mkdir -p "$mock_dir"
  cat > "$mock_dir/tesseract" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
  chmod +x "$mock_dir/tesseract"
}

# ── (a) Normal operation ────────────────────────────────────────────────────────
echo ""
echo "=== (a) Normal operation: .ocr.txt companion files created ==="

SCDIR_A="$TMPDIR_TESTS/screenshots_a"
mkdir -p "$SCDIR_A"
make_png "$SCDIR_A/TestClass_testMethod_screen.png"

MOCK_A="$TMPDIR_TESTS/mock_tess_a"
make_mock_tesseract "$MOCK_A" "Hello OCR world"

SUMMARY_A="$TMPDIR_TESTS/summary_a.md"
PATH="$MOCK_A:$PATH" GITHUB_STEP_SUMMARY="$SUMMARY_A" \
  bash "$OCR_SCRIPT" "$SCDIR_A"
status_a=$?

if [[ $status_a -eq 0 ]]; then
  pass "normal operation: script exits 0"
else
  fail "normal operation: script exited $status_a (expected 0)"
fi

if [[ -f "$SCDIR_A/TestClass_testMethod_screen.ocr.txt" ]]; then
  pass "normal operation: .ocr.txt companion file created alongside .png"
else
  fail "normal operation: .ocr.txt companion file not found"
fi

# ── (b) GITHUB_STEP_SUMMARY written ────────────────────────────────────────────
echo ""
echo "=== (b) GITHUB_STEP_SUMMARY written with header and one row per screenshot ==="

if [[ -f "$SUMMARY_A" ]]; then
  pass "GITHUB_STEP_SUMMARY file created"
else
  fail "GITHUB_STEP_SUMMARY file was not created"
fi

if grep -q "## E2E Screenshot OCR" "$SUMMARY_A" 2>/dev/null; then
  pass "GITHUB_STEP_SUMMARY contains the expected heading"
else
  fail "GITHUB_STEP_SUMMARY missing '## E2E Screenshot OCR' heading"
fi

if grep -q "TestClass_testMethod_screen.png" "$SUMMARY_A" 2>/dev/null; then
  pass "GITHUB_STEP_SUMMARY contains a row for the screenshot"
else
  fail "GITHUB_STEP_SUMMARY missing row for TestClass_testMethod_screen.png"
fi

# ── (c) First 80 chars of OCR text appear in summary row ───────────────────────
echo ""
echo "=== (c) First 80 chars of OCR text appear in summary row ==="

if grep -q "Hello OCR world" "$SUMMARY_A" 2>/dev/null; then
  pass "GITHUB_STEP_SUMMARY row contains the OCR text preview"
else
  fail "GITHUB_STEP_SUMMARY row does not contain the OCR text preview"
fi

# Also verify truncation: use a text that is longer than 80 chars.
SCDIR_C="$TMPDIR_TESTS/screenshots_c"
mkdir -p "$SCDIR_C"
make_png "$SCDIR_C/long.png"

LONG_TEXT="$(python3 -c "print('X' * 100)")"
MOCK_C="$TMPDIR_TESTS/mock_tess_c"
make_mock_tesseract "$MOCK_C" "$LONG_TEXT"

SUMMARY_C="$TMPDIR_TESTS/summary_c.md"
PATH="$MOCK_C:$PATH" GITHUB_STEP_SUMMARY="$SUMMARY_C" \
  bash "$OCR_SCRIPT" "$SCDIR_C"

OCR_ROW_C="$(grep 'long\.png' "$SUMMARY_C" || true)"
# The preview should be truncated to exactly 80 chars of the OCR text.
# Input is 100 X's; after cut -c1-80 the summary row must contain exactly 80 X's.
PREVIEW_LEN="$(echo "$OCR_ROW_C" | grep -oE 'X+' | tr -d '\n' | wc -c | tr -d ' ')"

if [[ "$PREVIEW_LEN" -eq 80 ]]; then
  pass "OCR preview is truncated to exactly 80 chars (got ${PREVIEW_LEN})"
else
  fail "OCR preview was not truncated to 80 chars: got ${PREVIEW_LEN} X chars (expected 80)"
fi

# ── (d) No PNGs in directory ────────────────────────────────────────────────────
echo ""
echo "=== (d) No PNGs in directory: exits 0, no summary header written ==="

SCDIR_D="$TMPDIR_TESTS/screenshots_d"
mkdir -p "$SCDIR_D"

SUMMARY_D="$TMPDIR_TESTS/summary_d.md"
bash "$OCR_SCRIPT" "$SCDIR_D"
status_d=$?

if [[ $status_d -eq 0 ]]; then
  pass "no-PNG directory: script exits 0"
else
  fail "no-PNG directory: script exited $status_d (expected 0)"
fi

if [[ ! -f "$SUMMARY_D" ]]; then
  pass "no-PNG directory: GITHUB_STEP_SUMMARY not written (no images to report)"
else
  if ! grep -q "## E2E Screenshot OCR" "$SUMMARY_D" 2>/dev/null; then
    pass "no-PNG directory: summary header not written when there are no images"
  else
    fail "no-PNG directory: summary header was written even though there were no images"
  fi
fi

# ── (e) Directory not found: exits 1 ──────────────────────────────────────────
echo ""
echo "=== (e) Directory not found: exits 1 ==="

set +e
bash "$OCR_SCRIPT" "$TMPDIR_TESTS/does_not_exist"
status_e=$?
set -e

if [[ $status_e -ne 0 ]]; then
  pass "missing directory: script exits non-zero (got $status_e)"
else
  fail "missing directory: script exited 0 (expected non-zero)"
fi

# ── (f) Wrong number of arguments: exits 1 ────────────────────────────────────
echo ""
echo "=== (f) Wrong number of arguments: exits 1 ==="

set +e
bash "$OCR_SCRIPT" 2>/dev/null
status_f1=$?
bash "$OCR_SCRIPT" a b 2>/dev/null
status_f2=$?
set -e

if [[ $status_f1 -ne 0 ]]; then
  pass "no-args: script exits non-zero (got $status_f1)"
else
  fail "no-args: script exited 0 (expected non-zero)"
fi

if [[ $status_f2 -ne 0 ]]; then
  pass "two-args: script exits non-zero (got $status_f2)"
else
  fail "two-args: script exited 0 (expected non-zero)"
fi

# ── (g) Tesseract failure is non-fatal; row shows "(OCR failed)" ───────────────
echo ""
echo "=== (g) Tesseract failure is non-fatal; summary row shows '(OCR failed)' ==="

SCDIR_G="$TMPDIR_TESTS/screenshots_g"
mkdir -p "$SCDIR_G"
make_png "$SCDIR_G/fail_test.png"

MOCK_G="$TMPDIR_TESTS/mock_tess_g"
make_failing_tesseract "$MOCK_G"

SUMMARY_G="$TMPDIR_TESTS/summary_g.md"
PATH="$MOCK_G:$PATH" GITHUB_STEP_SUMMARY="$SUMMARY_G" \
  bash "$OCR_SCRIPT" "$SCDIR_G"
status_g=$?

if [[ $status_g -eq 0 ]]; then
  pass "tesseract failure: script exits 0 (non-fatal)"
else
  fail "tesseract failure: script exited $status_g (expected 0)"
fi

if grep -q "(OCR failed)" "$SUMMARY_G" 2>/dev/null; then
  pass "tesseract failure: summary row contains '(OCR failed)'"
else
  fail "tesseract failure: summary row does not contain '(OCR failed)'"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed."
if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
