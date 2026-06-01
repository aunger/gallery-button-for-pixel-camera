#!/usr/bin/env bash
# test_orchestrate_guard.sh--Tests for the /orchestrate skill's orchestrate-guard.sh.
#
# Covers:
#   (a) A forbidden tool (Edit) is allowed by default but prints an advisory reminder
#   (b) A forbidden tool blocks (exit 2) when ORCHESTRATE_GUARD_BLOCK=1
#   (c) A permitted tool (Read) passes silently, exit 0
#   (d) Empty stdin is a no-op, exit 0
#   (e) Each forbidden tool (Write, NotebookEdit) is recognized under block mode
#   (f) A Bash command that reads source files prints an advisory but never blocks
#   (g) A Bash command that does not read source files passes silently
#
# Always exits 0 on success, non-zero on failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="$SCRIPT_DIR/../hooks/orchestrate-guard.sh"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

# Run the guard with given JSON on stdin and env; capture exit code and stderr.
# Sets globals: RC, ERR
run_guard() {
    local json="$1"; shift
    ERR="$(printf '%s' "$json" | env "$@" bash "$GUARD" 2>&1 1>/dev/null)"
    RC=$?
}

echo ""
echo "=== (a) Forbidden tool allowed by default with advisory reminder ==="
run_guard '{"tool_name":"Edit","tool_input":{}}'
if [[ $RC -eq 0 ]]; then pass "exit 0 by default"; else fail "expected exit 0, got $RC"; fi
if echo "$ERR" | grep -q "orchestrate-guard"; then pass "advisory printed"; else fail "no advisory: '$ERR'"; fi

echo ""
echo "=== (b) Forbidden tool blocks under ORCHESTRATE_GUARD_BLOCK=1 ==="
run_guard '{"tool_name":"Edit","tool_input":{}}' ORCHESTRATE_GUARD_BLOCK=1
if [[ $RC -eq 2 ]]; then pass "exit 2 in block mode"; else fail "expected exit 2, got $RC"; fi

echo ""
echo "=== (c) Permitted tool passes silently ==="
run_guard '{"tool_name":"Read","tool_input":{}}' ORCHESTRATE_GUARD_BLOCK=1
if [[ $RC -eq 0 ]]; then pass "Read exit 0"; else fail "expected exit 0, got $RC"; fi
if [[ -z "$ERR" ]]; then pass "no stderr for Read"; else fail "unexpected stderr: '$ERR'"; fi

echo ""
echo "=== (d) Empty stdin is a no-op ==="
run_guard '' ORCHESTRATE_GUARD_BLOCK=1
if [[ $RC -eq 0 ]]; then pass "empty stdin exit 0"; else fail "expected exit 0, got $RC"; fi

echo ""
echo "=== (e) Write and NotebookEdit are recognized in block mode ==="
run_guard '{"tool_name":"Write"}' ORCHESTRATE_GUARD_BLOCK=1
if [[ $RC -eq 2 ]]; then pass "Write blocked"; else fail "Write expected exit 2, got $RC"; fi
run_guard '{"tool_name":"NotebookEdit"}' ORCHESTRATE_GUARD_BLOCK=1
if [[ $RC -eq 2 ]]; then pass "NotebookEdit blocked"; else fail "NotebookEdit expected exit 2, got $RC"; fi

echo ""
echo "=== (f) Bash command reading source prints advisory but never blocks ==="
run_guard '{"tool_name":"Bash","tool_input":{"command":"grep -n foo src/main.kt"}}' ORCHESTRATE_GUARD_BLOCK=1
if [[ $RC -eq 0 ]]; then pass "Bash never blocks, exit 0 even in block mode"; else fail "expected exit 0, got $RC"; fi
if echo "$ERR" | grep -q "orchestrate-guard"; then pass "source-read advisory printed"; else fail "no advisory: '$ERR'"; fi
# A source reader appearing later in a pipeline is still caught.
run_guard '{"tool_name":"Bash","tool_input":{"command":"date -u && cat AGENTS.md"}}'
if echo "$ERR" | grep -q "orchestrate-guard"; then pass "pipeline-stage source read advised"; else fail "no advisory for piped reader: '$ERR'"; fi
# A path-prefixed reader is still recognized.
run_guard '{"tool_name":"Bash","tool_input":{"command":"/usr/bin/sed -n 1p file.txt"}}'
if echo "$ERR" | grep -q "orchestrate-guard"; then pass "path-prefixed reader advised"; else fail "no advisory for path-prefixed reader: '$ERR'"; fi

echo ""
echo "=== (g) Bash command not reading source passes silently ==="
run_guard '{"tool_name":"Bash","tool_input":{"command":"date -u"}}' ORCHESTRATE_GUARD_BLOCK=1
if [[ $RC -eq 0 ]]; then pass "non-reading Bash exit 0"; else fail "expected exit 0, got $RC"; fi
if [[ -z "$ERR" ]]; then pass "no stderr for non-reading Bash"; else fail "unexpected stderr: '$ERR'"; fi
run_guard '{"tool_name":"Bash","tool_input":{"command":"git branch --show-current"}}'
if [[ -z "$ERR" ]]; then pass "git branch passes silently"; else fail "unexpected stderr: '$ERR'"; fi

echo ""
echo "Results: $PASS passed, $FAIL failed."
if [[ $FAIL -gt 0 ]]; then exit 1; fi
exit 0
