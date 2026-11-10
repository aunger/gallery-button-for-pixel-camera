#!/usr/bin/env bash
# test_install_plugins.sh: unit tests for .claude/install-plugins.sh, which installs
# the Claude Code plugins .claude/gb4pc-tools pins, and for the session-start hook's
# call to it.
#
# The behavioral cases run a copy of the real script beside a fixture marketplace,
# with a stub `claude` first on PATH that records every invocation and answers
# `claude plugin list --json` from a state file. They check the commands the script
# runs and their order, its one line of stdout, and its failure handling, without
# the network or a real Claude Code configuration.
#
# The wiring case reads the hook's text: the hook does work this test cannot run
# (it downloads the Android SDK), so it checks that the call is inside the
# CLAUDE_CODE_REMOTE guard and that a failure is caught.
#
# Always exits 0 on success, non-zero on failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_SH="$REPO_ROOT/.claude/install-plugins.sh"
HOOK="$REPO_ROOT/.claude/hooks/session-start.sh"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

TMP="$(mktemp -d "${TMPDIR:-/tmp}/install-plugins-test-XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

# A case directory holds a copy of the script in its own .claude/, a fixture
# marketplace named fixture-tools listing alpha and beta, and a stub claude.
# The stub fails, exit 3, on any invocation that starts with $2 (when given), and
# with $3 = "changes" each install adds a line to the state `plugin list --json`
# prints, standing in for an install that changed something.
make_case() {
    local case_dir="$1" fail_on="${2:-}" changes="${3:-}"
    mkdir -p "$case_dir/repo/.claude/gb4pc-tools/.claude-plugin" "$case_dir/bin"
    cp "$INSTALL_SH" "$case_dir/repo/.claude/install-plugins.sh"
    cat > "$case_dir/repo/.claude/gb4pc-tools/.claude-plugin/marketplace.json" << 'EOF'
{
  "name": "fixture-tools",
  "owner": { "name": "fixture" },
  "plugins": [
    { "name": "alpha", "source": "./alpha" },
    { "name": "beta", "source": "./beta" }
  ]
}
EOF
    printf '[]\n' > "$case_dir/state"
    cat > "$case_dir/bin/claude" << EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$case_dir/claude-args.log"
if [[ "\$*" == "plugin list --json" ]]; then
    cat "$case_dir/state"
else
    echo "stub claude output: \$*"
fi
if [[ "$changes" == "changes" && "\$*" == "plugin install "* ]]; then
    echo "\$*" >> "$case_dir/state"
fi
if [[ -n "$fail_on" && "\$*" == "$fail_on"* ]]; then
    exit 3
fi
exit 0
EOF
    chmod +x "$case_dir/bin/claude"
}

# Runs the case's copy of the script from / (it must not depend on the working
# directory), with the stub first on PATH. Stdout and stderr go to separate files.
run_case() {
    local case_dir="$1"
    (cd / && PATH="$case_dir/bin:$PATH" bash "$case_dir/repo/.claude/install-plugins.sh" \
        > "$case_dir/stdout" 2> "$case_dir/stderr")
}

echo "=== (a) installs, then updates, every plugin the marketplace lists ==="
CASE_A="$TMP/a"
make_case "$CASE_A" "" changes
if run_case "$CASE_A"; then
    pass "the script exits 0"
else
    fail "the script should exit 0 when every command succeeds"
fi
MARKETPLACE_DIR_A="$(cd "$CASE_A/repo/.claude" && pwd)/gb4pc-tools"
EXPECTED="plugin list --json
plugin install alpha --marketplace $MARKETPLACE_DIR_A
plugin install beta --marketplace $MARKETPLACE_DIR_A
plugin marketplace update fixture-tools
plugin update alpha@fixture-tools
plugin update beta@fixture-tools
plugin list --json"
ACTUAL="$(cat "$CASE_A/claude-args.log" 2> /dev/null)"
if [[ "$ACTUAL" == "$EXPECTED" ]]; then
    pass "the claude commands, in order, with the marketplace's absolute path"
else
    fail "unexpected claude commands:
--- expected
$EXPECTED
--- actual
$ACTUAL"
fi

echo ""
echo "=== (b) stdout is one line asking for /reload-skills after a change ==="
STDOUT_A="$(cat "$CASE_A/stdout")"
if [[ "$(wc -l < "$CASE_A/stdout")" -eq 1 ]]; then
    pass "stdout is one line"
else
    fail "stdout should be one line, got:
$STDOUT_A"
fi
if [[ "$STDOUT_A" == "[install-plugins] Installed alpha beta "*"ask the user to run /reload-skills." ]]; then
    pass "it names the plugins and asks for /reload-skills"
else
    fail "unexpected stdout: $STDOUT_A"
fi
if grep -q 'stub claude output: plugin install alpha' "$CASE_A/stderr"; then
    pass "the claude commands' own output goes to stderr"
else
    fail "the claude commands' output should go to stderr"
fi

echo ""
echo "=== (c) an install that changed nothing says so ==="
CASE_C="$TMP/c"
make_case "$CASE_C"
run_case "$CASE_C"
STDOUT_C="$(cat "$CASE_C/stdout")"
if [[ "$STDOUT_C" == "[install-plugins] alpha beta were already installed "* ]]; then
    pass "it reports that the plugins were already installed"
else
    fail "unexpected stdout for an unchanged install: $STDOUT_C"
fi

echo ""
echo "=== (d) a failing command stops the script with a non-zero exit ==="
CASE_D="$TMP/d"
make_case "$CASE_D" "plugin marketplace update" changes
if run_case "$CASE_D"; then
    fail "the script should exit non-zero when a claude command fails"
else
    pass "the script exits non-zero"
fi
if grep -q '^plugin update ' "$CASE_D/claude-args.log"; then
    fail "no plugin update should run after the marketplace update failed"
else
    pass "nothing runs after the failed command"
fi
if [[ -s "$CASE_D/stdout" ]]; then
    fail "a failed run should print nothing on stdout, got: $(cat "$CASE_D/stdout")"
else
    pass "a failed run prints nothing on stdout"
fi

echo ""
echo "=== (e) the session-start hook calls the script and catches its failure ==="
if python3 - "$HOOK" << 'PY'
import re
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    lines = [line for line in handle if not line.lstrip().startswith("#")]
text = "".join(lines)
guard = text.find("CLAUDE_CODE_REMOTE")
call = re.search(r'install-plugins\.sh"\s*\\\n\s*\|\||install-plugins\.sh"\s*\|\|', text)
android = text.find("$SDKMANAGER\" --licenses")
sys.exit(0 if guard != -1 and call and guard < call.start() < android else 1)
PY
then
    pass "the hook runs install-plugins.sh after its CLAUDE_CODE_REMOTE guard, with ||, before the Android setup"
else
    fail "the hook should run .claude/install-plugins.sh after its CLAUDE_CODE_REMOTE guard and before the Android setup, followed by || so a failure is reported instead of ending the hook"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "test_install_plugins.sh: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]] && exit 0
exit 1
