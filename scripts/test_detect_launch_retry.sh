#!/usr/bin/env bash
# test_detect_launch_retry.sh — Shell-based tests for detect_launch_retry.sh.
#
# Covers:
#   (a) Attempt-1-only logcat (the steady state across 26+ runs) -> no signal, exit 0
#   (b) A retry "am start attempt 2/3" line -> signal, exit 10
#   (c) A "overlay active on attempt 2" recovery line -> signal, exit 10
#   (d) A "overlay still inactive after ... attempts" exhaustion Log.w -> signal, exit 10
#   (e) A "first-launch teardown race" Log.w -> signal, exit 10
#   (f) Reads from stdin when no path argument is given
#   (g) "attempt 1/3" and "overlay active on attempt 1" alone never match
#
# Always exits 0 on success, non-zero on failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECT="$SCRIPT_DIR/detect_launch_retry.sh"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

# Run DETECT on the given input file, capturing exit code into RC.
run_file() {
  bash "$DETECT" "$1" > /dev/null 2>&1
  RC=$?
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ── (a) Attempt-1-only logcat -> no signal, exit 0 ───────────────────────────
echo ""
echo "=== (a) Attempt-1-only logcat -> no signal, exit 0 ==="
cat > "$TMP/a.txt" <<'EOF'
07:28:58.933  TestRunner: started: overlayAppearsWhenViewfinderOpens
07:29:01.315  GB4PC_E2E: launchPixelCamera: am start attempt 1/3
07:29:03.287  GB4PC_E2E: launchPixelCamera: overlay active on attempt 1
07:29:03.306  TestRunner: finished: overlayAppearsWhenViewfinderOpens
EOF
run_file "$TMP/a.txt"
if [[ "$RC" -eq 0 ]]; then pass "attempt-1-only exits 0"; else fail "expected exit 0, got $RC"; fi

# ── (b) "am start attempt 2/3" -> signal, exit 10 ────────────────────────────
echo ""
echo "=== (b) am start attempt 2/3 -> signal, exit 10 ==="
cat > "$TMP/b.txt" <<'EOF'
07:29:01.315  GB4PC_E2E: launchPixelCamera: am start attempt 1/3
07:29:05.000  GB4PC_E2E: launchPixelCamera: am start attempt 2/3
07:29:06.000  GB4PC_E2E: launchPixelCamera: overlay active on attempt 2
EOF
run_file "$TMP/b.txt"
if [[ "$RC" -eq 10 ]]; then pass "attempt 2/3 exits 10"; else fail "expected exit 10, got $RC"; fi

# ── (c) "overlay active on attempt 2" recovery -> signal, exit 10 ────────────
echo ""
echo "=== (c) overlay active on attempt 2 -> signal, exit 10 ==="
cat > "$TMP/c.txt" <<'EOF'
07:29:06.000  GB4PC_E2E: launchPixelCamera: overlay active on attempt 2
EOF
run_file "$TMP/c.txt"
if [[ "$RC" -eq 10 ]]; then pass "recovery line exits 10"; else fail "expected exit 10, got $RC"; fi

# ── (d) exhaustion Log.w -> signal, exit 10 ──────────────────────────────────
echo ""
echo "=== (d) overlay still inactive after N attempts -> signal, exit 10 ==="
cat > "$TMP/d.txt" <<'EOF'
07:29:10.000  GB4PC_E2E: launchPixelCamera: overlay still inactive after 3 attempts; letting the caller's own assertion fail
EOF
run_file "$TMP/d.txt"
if [[ "$RC" -eq 10 ]]; then pass "exhaustion line exits 10"; else fail "expected exit 10, got $RC"; fi

# ── (e) "first-launch teardown race" Log.w -> signal, exit 10 ────────────────
echo ""
echo "=== (e) first-launch teardown race -> signal, exit 10 ==="
cat > "$TMP/e.txt" <<'EOF'
07:29:05.000  GB4PC_E2E: launchPixelCamera: overlay still inactive after 30000 ms on attempt 1 (first-launch teardown race); re-issuing am start
EOF
run_file "$TMP/e.txt"
if [[ "$RC" -eq 10 ]]; then pass "teardown-race line exits 10"; else fail "expected exit 10, got $RC"; fi

# ── (f) reads from stdin when no path argument is given ──────────────────────
echo ""
echo "=== (f) reads from stdin when no argument ==="
OUT="$(printf '%s\n' 'GB4PC_E2E: launchPixelCamera: am start attempt 2/3' | bash "$DETECT" 2>&1)"
RC=$?
if [[ "$RC" -eq 10 ]] && echo "$OUT" | grep -q "attempt 2/3"; then
  pass "stdin path detected and exits 10"
else
  fail "stdin path: expected exit 10 with match, got rc=$RC out='$OUT'"
fi

# ── (g) attempt 1 lines alone never match ────────────────────────────────────
echo ""
echo "=== (g) attempt 1 lines alone never match ==="
OUT="$(printf '%s\n%s\n' \
  'GB4PC_E2E: launchPixelCamera: am start attempt 1/3' \
  'GB4PC_E2E: launchPixelCamera: overlay active on attempt 1' | bash "$DETECT" 2>&1)"
RC=$?
if [[ "$RC" -eq 0 ]]; then pass "attempt 1 lines do not trigger"; else fail "expected exit 0, got $RC out='$OUT'"; fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed."
if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
