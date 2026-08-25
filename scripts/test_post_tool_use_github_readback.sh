#!/usr/bin/env bash
# test_post_tool_use_github_readback.sh: Shell-based tests for
# .claude/hooks/post-tool-use-github-readback.sh.
#
# The hook is a thin wrapper: it pipes the PostToolUse payload to
# scripts/agents/verify_github_write.py and decides what to do with the exit
# code.  These tests stub "python3" on PATH, so they verify the wrapper's own
# contract without a network, a token, or the real checker.
#
# Covers:
#   (a) The payload on stdin reaches the child process unchanged
#   (b) Exit 0 (nothing to report) is forwarded as 0, silently
#   (c) Exit 2 (a finding) is forwarded as 2, with the child's stderr intact
#   (d) Any other exit code becomes exit 0 plus a warning, so a broken checker
#       never wedges a session
#   (e) A missing interpreter is also exit 0 plus a warning
#   (f) The warning says the write was not verified, so the gap is visible
#
# Modeled on scripts/test_post_tool_use_fetch.sh.
#
# Always exits 0 on success, non-zero on failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="$SCRIPT_DIR/../.claude/hooks/post-tool-use-github-readback.sh"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

STDIN_FILE="$TMP_DIR/child_stdin"
export STDIN_FILE

PAYLOAD='{"tool_name":"mcp__github__add_issue_comment","tool_input":{"body":"hello"}}'

# Write a fake python3 that records its stdin and exits with $1.
stub_python3() {
    local exit_code="$1"
    local stderr_text="${2:-}"
    cat > "$TMP_DIR/python3" << EOF
#!/usr/bin/env bash
cat > "\$STDIN_FILE"
if [[ -n "$stderr_text" ]]; then
    echo "$stderr_text" >&2
fi
exit $exit_code
EOF
    chmod +x "$TMP_DIR/python3"
}

# (a)+(b) A clean check is forwarded as 0, and stdin reaches the child--------
echo ""
echo "=== (a)+(b) stdin reaches the checker; exit 0 is forwarded ==="

stub_python3 0
rm -f "$STDIN_FILE"
OUTPUT=$(printf '%s' "$PAYLOAD" | PATH="$TMP_DIR:$PATH" bash "$HOOK" 2>&1)
EXIT_CODE=$?

if [[ "$(cat "$STDIN_FILE" 2>/dev/null)" == "$PAYLOAD" ]]; then
    pass "(a) payload reached the checker unchanged"
else
    fail "(a) checker saw '$(cat "$STDIN_FILE" 2>/dev/null)'"
fi

if [[ $EXIT_CODE -eq 0 ]]; then
    pass "(b) exit 0 forwarded"
else
    fail "(b) expected exit 0, got $EXIT_CODE"
fi

if [[ -z "$OUTPUT" ]]; then
    pass "(b) clean path printed nothing"
else
    fail "(b) clean path printed: '$OUTPUT'"
fi

# (c) A finding is forwarded as 2, with its message------------------------
echo ""
echo "=== (c) exit 2 and the finding are forwarded ==="

stub_python3 2 "GitHub write altered in storage"
OUTPUT=$(printf '%s' "$PAYLOAD" | PATH="$TMP_DIR:$PATH" bash "$HOOK" 2>&1)
EXIT_CODE=$?

if [[ $EXIT_CODE -eq 2 ]]; then
    pass "(c) exit 2 forwarded"
else
    fail "(c) expected exit 2, got $EXIT_CODE"
fi

if echo "$OUTPUT" | grep -q "altered in storage"; then
    pass "(c) the checker's message survived"
else
    fail "(c) message lost; output was '$OUTPUT'"
fi

# (d)+(f) Any other code becomes a warning and exit 0-----------------------
echo ""
echo "=== (d)+(f) a checker fault warns and exits 0 ==="

for CODE in 1 3 127; do
    stub_python3 "$CODE" "traceback noise"
    OUTPUT=$(printf '%s' "$PAYLOAD" | PATH="$TMP_DIR:$PATH" bash "$HOOK" 2>&1)
    EXIT_CODE=$?

    if [[ $EXIT_CODE -eq 0 ]]; then
        pass "(d) checker exit $CODE became exit 0"
    else
        fail "(d) checker exit $CODE gave $EXIT_CODE, expected 0"
    fi

    if echo "$OUTPUT" | grep -qi "warning"; then
        pass "(d) checker exit $CODE printed a warning"
    else
        fail "(d) checker exit $CODE printed no warning: '$OUTPUT'"
    fi

    if echo "$OUTPUT" | grep -q "NOT verified"; then
        pass "(f) checker exit $CODE said the write was not verified"
    else
        fail "(f) checker exit $CODE hid the gap: '$OUTPUT'"
    fi
done

# (e) A missing interpreter is survivable-----------------------------------
echo ""
echo "=== (e) a missing python3 warns and exits 0 ==="

# A PATH holding only what the wrapper itself needs, so python3 is genuinely
# absent rather than the test harness losing its own shell.
BARE_DIR="$TMP_DIR/bare"
mkdir -p "$BARE_DIR"
for TOOL in bash dirname; do
    ln -sf "$(command -v "$TOOL")" "$BARE_DIR/$TOOL"
done

OUTPUT=$(printf '%s' "$PAYLOAD" | PATH="$BARE_DIR" "$BASH" "$HOOK" 2>&1)
EXIT_CODE=$?

if [[ $EXIT_CODE -eq 0 ]]; then
    pass "(e) missing python3 gave exit 0"
else
    fail "(e) missing python3 gave $EXIT_CODE, expected 0"
fi

if echo "$OUTPUT" | grep -qi "warning"; then
    pass "(e) missing python3 printed a warning"
else
    fail "(e) missing python3 printed no warning: '$OUTPUT'"
fi

# Summary--------------------------------------------------------------------
echo ""
echo "Results: $PASS passed, $FAIL failed."
if [[ $FAIL -gt 0 ]]; then
    exit 1
fi
exit 0
