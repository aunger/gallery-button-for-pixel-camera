#!/usr/bin/env bash
# test_ci_monitor.sh — Shell-based tests for ci_monitor.sh.
#
# Covers:
#   (a) Signal 1 step parser: all-success build-and-test emits exactly the 3 test-step lines
#   (b) Signal 1 step parser: a genuine failure step is emitted
#   (c) Signal 1 step parser: successful setup steps and skipped conditional steps are suppressed
#   (d) Signal 1 step parser: deduplication across two iterations (same step not re-emitted)
#   (e) Signal 2 artifact parser: FAIL with multi-line trace is emitted with indented trace
#   (f) Signal 2 artifact parser: all-PASS artifact emits nothing
#   (g) Signal 2 artifact parser: deduplication by suite#name across two calls
#
# No network calls required; no GITHUB_TOKEN needed.
# Always exits 0 on success, non-zero on failure.

set -euo pipefail

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

TMPDIR_TESTS="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_TESTS"' EXIT

# ── Parser helpers ────────────────────────────────────────────────────────────
#
# Both parsers are invoked via the hidden subcommands in ci_monitor.sh so that
# tests exercise the real parser code, not a copy.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CI_MONITOR="$SCRIPT_DIR/ci_monitor.sh"

run_step_parser() {
  # Usage: run_step_parser <jobs-json-file> <seen-file>
  local json_file="$1"
  local seen_file="$2"
  bash "$CI_MONITOR" __parse-steps "$seen_file" < "$json_file" 2>/dev/null
}

run_fail_parser() {
  # Usage: run_fail_parser <ndjson-file> <seen-fails-file>
  local ndjson_file="$1"
  local seen_fails_file="$2"
  bash "$CI_MONITOR" __parse-fails "$seen_fails_file" < "$ndjson_file" 2>/dev/null
}

# ── Fixture: all-success build-and-test jobs JSON ────────────────────────────

ALL_SUCCESS_JOBS="$TMPDIR_TESTS/all_success_jobs.json"
cat > "$ALL_SUCCESS_JOBS" << 'EOF'
{
  "jobs": [
    {
      "name": "build-and-test",
      "steps": [
        {"number": 1, "name": "Set up job",                         "status": "completed", "conclusion": "success"},
        {"number": 2, "name": "Checkout",                           "status": "completed", "conclusion": "success"},
        {"number": 3, "name": "Set up JDK",                        "status": "completed", "conclusion": "success"},
        {"number": 4, "name": "Build and run unit tests",          "status": "completed", "conclusion": "success"},
        {"number": 5, "name": "Run PixelCameraOverlayE2ETest",     "status": "completed", "conclusion": "success"},
        {"number": 6, "name": "Run GalleryButtonVisualE2ETest",    "status": "completed", "conclusion": "success"},
        {"number": 7, "name": "Upload test results on failure",    "status": "completed", "conclusion": "skipped"},
        {"number": 8, "name": "Complete job",                      "status": "completed", "conclusion": "success"}
      ]
    }
  ]
}
EOF

# ── (a) All-success: exactly 3 test-step lines emitted ───────────────────────
echo ""
echo "=== (a) Signal 1: all-success build-and-test emits exactly 3 test-step lines ==="

SEEN_A="$TMPDIR_TESTS/seen_a.txt"; touch "$SEEN_A"
output_a="$(run_step_parser "$ALL_SUCCESS_JOBS" "$SEEN_A")"
line_count_a="$(echo "$output_a" | grep -c '^step ' || true)"

if [[ "$line_count_a" -eq 3 ]]; then
  pass "emits exactly 3 step lines (got $line_count_a)"
else
  fail "expected 3 step lines, got $line_count_a; output: '$output_a'"
fi

if echo "$output_a" | grep -qF 'step "Build and run unit tests" -> success'; then
  pass "unit tests step line present"
else
  fail "unit tests step line missing; output: '$output_a'"
fi

if echo "$output_a" | grep -qF 'step "Run PixelCameraOverlayE2ETest" -> success'; then
  pass "PixelCameraOverlayE2ETest step line present"
else
  fail "PixelCameraOverlayE2ETest step line missing; output: '$output_a'"
fi

if echo "$output_a" | grep -qF 'step "Run GalleryButtonVisualE2ETest" -> success'; then
  pass "GalleryButtonVisualE2ETest step line present"
else
  fail "GalleryButtonVisualE2ETest step line missing; output: '$output_a'"
fi

# ── (b) Genuine failure step is emitted ──────────────────────────────────────
echo ""
echo "=== (b) Signal 1: a genuine failure step is emitted ==="

FAILURE_JOBS="$TMPDIR_TESTS/failure_jobs.json"
cat > "$FAILURE_JOBS" << 'EOF'
{
  "jobs": [
    {
      "name": "build-and-test",
      "steps": [
        {"number": 1, "name": "Set up job",          "status": "completed", "conclusion": "success"},
        {"number": 2, "name": "Download AVD",         "status": "completed", "conclusion": "failure"}
      ]
    }
  ]
}
EOF

SEEN_B="$TMPDIR_TESTS/seen_b.txt"; touch "$SEEN_B"
output_b="$(run_step_parser "$FAILURE_JOBS" "$SEEN_B")"

if echo "$output_b" | grep -qF 'step "Download AVD" -> failure'; then
  pass "failed step 'Download AVD' is emitted"
else
  fail "failed step not emitted; output: '$output_b'"
fi

if echo "$output_b" | grep -q 'Set up job'; then
  fail "successful setup step 'Set up job' should NOT be emitted; output: '$output_b'"
else
  pass "successful setup step 'Set up job' correctly suppressed"
fi

# ── (c) Setup and skipped conditional steps are suppressed ───────────────────
echo ""
echo "=== (c) Signal 1: successful setup steps and skipped conditional steps are suppressed ==="

SEEN_C="$TMPDIR_TESTS/seen_c.txt"; touch "$SEEN_C"
output_c="$(run_step_parser "$ALL_SUCCESS_JOBS" "$SEEN_C")"

if echo "$output_c" | grep -q '"Set up job"'; then
  fail "'Set up job' should be suppressed; output: '$output_c'"
else
  pass "'Set up job' suppressed"
fi

if echo "$output_c" | grep -q '"Checkout"'; then
  fail "'Checkout' should be suppressed; output: '$output_c'"
else
  pass "'Checkout' suppressed"
fi

if echo "$output_c" | grep -q '"Set up JDK"'; then
  fail "'Set up JDK' should be suppressed; output: '$output_c'"
else
  pass "'Set up JDK' suppressed"
fi

if echo "$output_c" | grep -q '"Upload test results on failure"'; then
  fail "'Upload test results on failure' (skipped) should be suppressed; output: '$output_c'"
else
  pass "'Upload test results on failure' (skipped) suppressed"
fi

if echo "$output_c" | grep -q '"Complete job"'; then
  fail "'Complete job' should be suppressed; output: '$output_c'"
else
  pass "'Complete job' suppressed"
fi

# ── (d) Deduplication across two iterations ───────────────────────────────────
echo ""
echo "=== (d) Signal 1: deduplication — same steps not re-emitted on second iteration ==="

# First iteration: populate the seen file with all steps.
SEEN_D="$TMPDIR_TESTS/seen_d.txt"; touch "$SEEN_D"
output_d1="$(run_step_parser "$ALL_SUCCESS_JOBS" "$SEEN_D")"

# Second iteration: same fixture — should produce no output.
output_d2="$(run_step_parser "$ALL_SUCCESS_JOBS" "$SEEN_D")"

if [[ -z "$output_d2" ]]; then
  pass "second iteration emits nothing (all steps already seen)"
else
  fail "second iteration re-emitted steps: '$output_d2'"
fi

# First iteration must still have produced the 3 test lines.
line_count_d1="$(echo "$output_d1" | grep -c '^step ' || true)"
if [[ "$line_count_d1" -eq 3 ]]; then
  pass "first iteration still emitted 3 step lines before dedup kicks in"
else
  fail "first iteration emitted $line_count_d1 step lines (expected 3)"
fi

# ── Fixture: ndjson with one FAIL and several PASS lines ─────────────────────

FAIL_NDJSON="$TMPDIR_TESTS/testresults.ndjson"
cat > "$FAIL_NDJSON" << 'EOF'
some prefix ##GB4PC_TEST## {"suite":"com.gb4pc.e2e.GalleryButtonVisualE2ETest","name":"test1a","outcome":"FAIL","ms":0,"msg":"java.lang.AssertionError: expected button visible","trace":"java.lang.AssertionError: expected button visible\n\tat org.junit.Assert.fail(Assert.java:89)\n\tat com.gb4pc.e2e.GalleryButtonVisualE2ETest.test1a(GalleryButtonVisualE2ETest.kt:42)"}
##GB4PC_TEST## {"suite":"com.gb4pc.e2e.GalleryButtonVisualE2ETest","name":"test1b","outcome":"PASS","ms":120,"msg":"","trace":""}
##GB4PC_TEST## {"suite":"com.gb4pc.e2e.PixelCameraOverlayE2ETest","name":"test2a","outcome":"PASS","ms":200,"msg":"","trace":""}
##GB4PC_TEST## {"suite":"com.gb4pc.e2e.PixelCameraOverlayE2ETest","name":"test2b","outcome":"PASS","ms":150,"msg":"","trace":""}
EOF

PASS_ONLY_NDJSON="$TMPDIR_TESTS/testresults_pass.ndjson"
cat > "$PASS_ONLY_NDJSON" << 'EOF'
##GB4PC_TEST## {"suite":"com.gb4pc.e2e.GalleryButtonVisualE2ETest","name":"test3a","outcome":"PASS","ms":100,"msg":"","trace":""}
##GB4PC_TEST## {"suite":"com.gb4pc.e2e.GalleryButtonVisualE2ETest","name":"test3b","outcome":"PASS","ms":110,"msg":"","trace":""}
EOF

# ── (e) FAIL with multi-line trace emitted with indented trace ────────────────
echo ""
echo "=== (e) Signal 2: FAIL with multi-line trace is emitted with indented trace ==="

SEEN_FAILS_E="$TMPDIR_TESTS/seen_fails_e.txt"; touch "$SEEN_FAILS_E"
output_e="$(run_fail_parser "$FAIL_NDJSON" "$SEEN_FAILS_E")"

if echo "$output_e" | grep -qF 'FAIL [com.gb4pc.e2e.GalleryButtonVisualE2ETest] test1a:'; then
  pass "FAIL line for test1a emitted"
else
  fail "FAIL line for test1a not found; output: '$output_e'"
fi

if echo "$output_e" | grep -qF 'java.lang.AssertionError: expected button visible'; then
  pass "failure message present in output"
else
  fail "failure message missing; output: '$output_e'"
fi

# Trace line should be indented with at least 2 spaces.
if echo "$output_e" | grep -qE '^ {2}'; then
  pass "trace lines are indented"
else
  fail "trace lines are not indented; output: '$output_e'"
fi

# ── (f) All-PASS artifact emits nothing ──────────────────────────────────────
echo ""
echo "=== (f) Signal 2: all-PASS artifact emits nothing ==="

SEEN_FAILS_F="$TMPDIR_TESTS/seen_fails_f.txt"; touch "$SEEN_FAILS_F"
output_f="$(run_fail_parser "$PASS_ONLY_NDJSON" "$SEEN_FAILS_F")"

if [[ -z "$output_f" ]]; then
  pass "all-PASS artifact produces no output"
else
  fail "all-PASS artifact unexpectedly produced output: '$output_f'"
fi

# ── (g) Deduplication by suite#name across two calls ─────────────────────────
echo ""
echo "=== (g) Signal 2: deduplication by suite#name across two calls ==="

SEEN_FAILS_G="$TMPDIR_TESTS/seen_fails_g.txt"; touch "$SEEN_FAILS_G"

# First call: should emit the FAIL.
output_g1="$(run_fail_parser "$FAIL_NDJSON" "$SEEN_FAILS_G")"
if echo "$output_g1" | grep -qF 'FAIL [com.gb4pc.e2e.GalleryButtonVisualE2ETest] test1a:'; then
  pass "first call emits FAIL"
else
  fail "first call did not emit FAIL; output: '$output_g1'"
fi

# Second call with same fixture: FAIL already seen — should produce no output.
output_g2="$(run_fail_parser "$FAIL_NDJSON" "$SEEN_FAILS_G")"
if [[ -z "$output_g2" ]]; then
  pass "second call produces no output (FAIL already seen)"
else
  fail "second call re-emitted already-seen FAIL: '$output_g2'"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed."
if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
