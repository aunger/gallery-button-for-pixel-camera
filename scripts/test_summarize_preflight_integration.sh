#!/usr/bin/env bash
# test_summarize_preflight_integration.sh — Integration test for
# summarize_test_results.py exercised through the exact multi-suite CLI
# argument shape that build.yml's "Write test-result summary" step passes.
#
# The Python logic itself is covered by test_summarize_test_results.py; the
# novel value here is invoking the summarizer with the three-suite,
# --outcome-bearing argument vector that CI uses, confirming that a skipped
# test phase (e.g. after a pre-flight failure) renders as SKIPPED rather than
# as a misleading "no test results found" suite (issue #255).
#
# Covers:
#   (A) Direct CLI invocation in the three-suite shape build.yml uses
#   (B) The GITHUB_STEP_SUMMARY write path
#
# Always exits 0 on success, non-zero on failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUMMARIZER="$SCRIPT_DIR/summarize_test_results.py"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

TMPDIR_TESTS="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_TESTS"' EXIT

# ── (A) Direct CLI invocation in the three-suite shape build.yml uses ─────────
echo ""
echo "=== (A) Direct CLI invocation in the three-suite shape build.yml uses ==="

# The Unit Tests suite is passed with no --outcome, exactly as build.yml does;
# with no XML it renders "_No test results found._". The skipped-suite contract
# of issue #255 concerns the suites whose steps were skipped (Instrumented and
# E2E, passed --outcome "skipped"), so the negative assertion below is scoped to
# the SKIPPED-suite portion of the output rather than the whole document.
# Run with GITHUB_STEP_SUMMARY unset so the summarizer prints to stdout. In CI
# that variable is always set, which would otherwise divert the rendered
# markdown to a file and leave $OUTPUT empty.
OUTPUT=$(env -u GITHUB_STEP_SUMMARY python3 "$SUMMARIZER" \
    /nonexistent/unit         --suite-label "Unit Tests" \
    /nonexistent/instrumented --suite-label "Instrumented Tests" \
                              --outcome "skipped" \
    /nonexistent/e2e          --suite-label "E2E Tests" \
                              --outcome "skipped")

# Portion of the output from the first skipped suite onward.
SKIPPED_PORTION=$(echo "$OUTPUT" | sed -n '/### Instrumented Tests/,$p')

if echo "$OUTPUT" | grep -qF "⏭ SKIPPED"; then
  pass "output contains '⏭ SKIPPED'"
else
  fail "output missing '⏭ SKIPPED': '$OUTPUT'"
fi

if echo "$SKIPPED_PORTION" | grep -qF "_No test results found._"; then
  fail "skipped suites contain misleading '_No test results found._': '$OUTPUT'"
else
  pass "skipped suites do not contain '_No test results found._'"
fi

# ── (B) GITHUB_STEP_SUMMARY write path ────────────────────────────────────────
echo ""
echo "=== (B) GITHUB_STEP_SUMMARY write path ==="

SUMMARY_FILE="$TMPDIR_TESTS/step_summary.md"
: > "$SUMMARY_FILE"

GITHUB_STEP_SUMMARY="$SUMMARY_FILE" python3 "$SUMMARIZER" \
    /nonexistent/unit         --suite-label "Unit Tests" \
    /nonexistent/instrumented --suite-label "Instrumented Tests" \
                              --outcome "skipped" \
    /nonexistent/e2e          --suite-label "E2E Tests" \
                              --outcome "skipped"

if [[ -s "$SUMMARY_FILE" ]]; then
  pass "GITHUB_STEP_SUMMARY file exists and is non-empty"
else
  fail "GITHUB_STEP_SUMMARY file missing or empty: '$SUMMARY_FILE'"
fi

if grep -qF "⏭ SKIPPED" "$SUMMARY_FILE"; then
  pass "summary file contains '⏭ SKIPPED'"
else
  fail "summary file missing '⏭ SKIPPED': '$(cat "$SUMMARY_FILE")'"
fi

if sed -n '/### Instrumented Tests/,$p' "$SUMMARY_FILE" | grep -qF "_No test results found._"; then
  fail "skipped suites in summary file contain misleading '_No test results found._'"
else
  pass "skipped suites in summary file do not contain '_No test results found._'"
fi

unset GITHUB_STEP_SUMMARY

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed."
if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
