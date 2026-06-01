#!/usr/bin/env bash
# test_ci_watch.sh--Tests for the /orchestrate skill's ci_watch.sh wrapper.
#
# The wrapper must locate the canonical repo-root poller and forward arguments
# to it. These tests use a fake repo root with a stub poller so no network,
# GITHUB_TOKEN, or real ci_monitor.py run is needed.
#
# Covers:
#   (a) Forwards arguments to <root>/scripts/ci_monitor.py via ORCHESTRATE_REPO_ROOT
#   (b) Exits 1 with a clear message when the poller is absent
#   (c) Default (no override) resolves the real repo root and finds ci_monitor.py
#
# Always exits 0 on success, non-zero on failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WRAPPER="$SCRIPT_DIR/../scripts/ci_watch.sh"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

echo ""
echo "=== (a) Forwards arguments to the stub poller ==="
FAKE_ROOT="$(mktemp -d)"
trap 'rm -rf "$FAKE_ROOT"' EXIT
mkdir -p "$FAKE_ROOT/scripts"
# Stub poller echoes its argv so we can assert forwarding.
cat > "$FAKE_ROOT/scripts/ci_monitor.py" <<'PY'
import sys
print("STUB_ARGS:" + " ".join(sys.argv[1:]))
PY
OUT="$(ORCHESTRATE_REPO_ROOT="$FAKE_ROOT" bash "$WRAPPER" --pr 42 --include-pass Foo)"
if [[ "$OUT" == "STUB_ARGS:--pr 42 --include-pass Foo" ]]; then
    pass "arguments forwarded verbatim"
else
    fail "unexpected output: '$OUT'"
fi

echo ""
echo "=== (b) Missing poller exits 1 with a message ==="
EMPTY_ROOT="$(mktemp -d)"
if ERR="$(ORCHESTRATE_REPO_ROOT="$EMPTY_ROOT" bash "$WRAPPER" --pr 1 2>&1 1>/dev/null)"; then
    fail "expected nonzero exit when poller absent"
else
    pass "nonzero exit when poller absent"
fi
if echo "$ERR" | grep -q "canonical poller not found"; then
    pass "clear message when poller absent"
else
    fail "missing message: '$ERR'"
fi
rm -rf "$EMPTY_ROOT"

echo ""
echo "=== (c) Default resolution finds the real ci_monitor.py ==="
REAL_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
if [[ -f "$REAL_ROOT/scripts/ci_monitor.py" ]]; then
    pass "real ci_monitor.py present at resolved root"
else
    fail "real ci_monitor.py not at '$REAL_ROOT/scripts/ci_monitor.py'"
fi

echo ""
echo "Results: $PASS passed, $FAIL failed."
if [[ $FAIL -gt 0 ]]; then exit 1; fi
exit 0
