#!/usr/bin/env bash
# test_surface_phase.sh--Tests for the /orchestrate skill's surface-phase.sh.
#
# Covers:
#   (a) Each known phase maps to its resource path under the skill root
#   (b) An unknown phase exits 1 with usage on stderr
#   (c) Missing argument exits 1 with usage on stderr
#   (d) ORCHESTRATE_SKILL_ROOT override changes the printed prefix
#   (e) Every printed resource path exists on disk
#
# Always exits 0 on success, non-zero on failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SURFACE="$SCRIPT_DIR/../hooks/surface-phase.sh"
SKILL_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

echo ""
echo "=== (a) Known phases map to expected resource paths ==="
declare -A EXPECTED=(
    [intake]="resources/intake.md"
    [author]="resources/dispatch-author.md"
    [reviewer]="resources/dispatch-reviewer.md"
    [ci]="resources/ci-watch.md"
    [converge]="resources/convergence.md"
    [model]="resources/model-selection.md"
)
for phase in "${!EXPECTED[@]}"; do
    OUT="$(ORCHESTRATE_SKILL_ROOT="$SKILL_ROOT" bash "$SURFACE" "$phase")"
    if [[ "$OUT" == "$SKILL_ROOT/${EXPECTED[$phase]}" ]]; then
        pass "$phase -> ${EXPECTED[$phase]}"
    else
        fail "$phase -> '$OUT' (expected '$SKILL_ROOT/${EXPECTED[$phase]}')"
    fi
done

echo ""
echo "=== (b) Unknown phase exits 1 ==="
if ERR="$(bash "$SURFACE" bogus 2>&1 1>/dev/null)"; then
    fail "unknown phase did not exit nonzero"
else
    pass "unknown phase exit nonzero"
fi
if echo "$ERR" | grep -q "usage:"; then pass "usage printed for unknown phase"; else fail "no usage: '$ERR'"; fi

echo ""
echo "=== (c) Missing argument exits 1 ==="
if bash "$SURFACE" >/dev/null 2>&1; then
    fail "missing argument did not exit nonzero"
else
    pass "missing argument exit nonzero"
fi

echo ""
echo "=== (d) ORCHESTRATE_SKILL_ROOT override changes the prefix ==="
OUT="$(ORCHESTRATE_SKILL_ROOT="/tmp/custom-root" bash "$SURFACE" intake)"
if [[ "$OUT" == "/tmp/custom-root/resources/intake.md" ]]; then
    pass "override honored"
else
    fail "override ignored: '$OUT'"
fi

echo ""
echo "=== (e) Printed resource paths exist on disk ==="
for phase in "${!EXPECTED[@]}"; do
    OUT="$(ORCHESTRATE_SKILL_ROOT="$SKILL_ROOT" bash "$SURFACE" "$phase")"
    if [[ -f "$OUT" ]]; then
        pass "$phase resource exists"
    else
        fail "$phase resource missing on disk: '$OUT'"
    fi
done

echo ""
echo "Results: $PASS passed, $FAIL failed."
if [[ $FAIL -gt 0 ]]; then exit 1; fi
exit 0
