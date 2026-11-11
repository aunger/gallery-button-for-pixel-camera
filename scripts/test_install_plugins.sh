#!/usr/bin/env bash
# test_install_plugins.sh: unit tests for .claude/install-plugins.sh, which installs
# the Claude Code plugins .claude/gb4pc-tools pins, and for the session-start hook's
# call to it.
#
# The behavioral cases run a copy of the real script beside a fixture marketplace,
# with a stub `claude` first on PATH that records every invocation and answers
# `claude plugin list --json` from a state file. They check the commands the script
# runs and their order, the early exit when the plugins are already at the pin, its
# one line of stdout, and its failure handling, without the network or a real
# Claude Code configuration.
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

PIN=0123456789abcdef0123456789abcdef01234567
OLD_PIN=fedcba9876543210fedcba9876543210fedcba98

# A case directory holds a copy of the script in its own .claude/, a fixture
# marketplace named fixture-tools listing alpha and beta at $PIN, and a stub claude.
# The stub answers `plugin list --json` from a JSON state file, which starts as $3
# (default []). With $2 = "changes", each `plugin install` or `plugin update` of a
# plugin records it in the state at $PIN, enabled, at local scope, for the
# directory the stub ran in, standing in for an install that changed something.
# The stub fails, exit 3, on any invocation that starts with $4 (when given).
make_case() {
    local case_dir="$1" changes="${2:-}" state="${3:-[]}" fail_on="${4:-}"
    mkdir -p "$case_dir/repo/.claude/gb4pc-tools/.claude-plugin" "$case_dir/bin"
    cp "$INSTALL_SH" "$case_dir/repo/.claude/install-plugins.sh"
    cat > "$case_dir/repo/.claude/gb4pc-tools/.claude-plugin/marketplace.json" << EOF
{
  "name": "fixture-tools",
  "owner": { "name": "fixture" },
  "plugins": [
    { "name": "alpha", "source": { "source": "git-subdir", "url": "fixture/tools", "path": "plugins/alpha", "sha": "$PIN" } },
    { "name": "beta", "source": { "source": "git-subdir", "url": "fixture/tools", "path": "plugins/beta", "sha": "$PIN" } }
  ]
}
EOF
    printf '%s\n' "$state" > "$case_dir/state"
    cat > "$case_dir/bin/claude" << EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$case_dir/claude-args.log"
if [[ "\$*" == "plugin list --json" ]]; then
    cat "$case_dir/state"
else
    echo "stub claude output: \$*"
fi
if [[ "$changes" == "changes" && ( "\$1 \$2" == "plugin install" || "\$1 \$2" == "plugin update" ) ]]; then
    python3 - "$case_dir/state" "\$3" "\$PWD" "$PIN" << 'PY2'
import json, sys
path, plugin_id, cwd, pin = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    state = [entry for entry in json.load(handle) if entry["id"] != plugin_id or entry.get("projectPath") != cwd]
state.append({"id": plugin_id, "version": pin[:12] + "-0000", "scope": "local", "enabled": True, "projectPath": cwd})
with open(path, "w", encoding="utf-8") as handle:
    json.dump(state, handle)
PY2
fi
if [[ -n "$fail_on" && "\$*" == "$fail_on"* ]]; then
    exit 3
fi
exit 0
EOF
    chmod +x "$case_dir/bin/claude"
}

# One state entry: plugin $1 at the 12-digit version prefix $2, for the checkout
# at $3, enabled unless $4 is "false".
entry() {
    printf '{"id": "%s@fixture-tools", "version": "%s-0000", "scope": "local", "enabled": %s, "projectPath": "%s"}' \
        "$1" "${2:0:12}" "${4:-true}" "$3"
}

# The state of a checkout at $1 with alpha and beta installed at the 12-digit
# version prefix $2.
installed_state() {
    printf '[%s, %s]' "$(entry alpha "$2" "$1")" "$(entry beta "$2" "$1")"
}

# Turns plugins $2... off for the case at $1, as `claude plugin disable --scope
# local` does: by setting each to false in .claude/settings.local.json.
turn_off() {
    local case_dir="$1" plugin entries=""
    shift
    for plugin in "$@"; do
        entries+="${entries:+, }\"$plugin@fixture-tools\": false"
    done
    printf '{"enabledPlugins": {%s}}\n' "$entries" > "$case_dir/repo/.claude/settings.local.json"
}

# Runs the case's copy of the script from / (it must not depend on the working
# directory), with the stub first on PATH. Stdout and stderr go to separate files.
run_case() {
    local case_dir="$1"
    (cd / && PATH="$case_dir/bin:$PATH" bash "$case_dir/repo/.claude/install-plugins.sh" \
        > "$case_dir/stdout" 2> "$case_dir/stderr")
}

# The full sequence of claude commands for a case's repository.
full_sequence() {
    local marketplace_dir="$1/repo/.claude/gb4pc-tools"
    printf '%s\n' \
        "plugin list --json" \
        "plugin marketplace add $marketplace_dir --scope local" \
        "plugin install alpha@fixture-tools --scope local" \
        "plugin install beta@fixture-tools --scope local" \
        "plugin marketplace update fixture-tools" \
        "plugin update alpha@fixture-tools --scope local" \
        "plugin update beta@fixture-tools --scope local" \
        "plugin list --json"
}

check_commands() {
    local case_dir="$1" expected="$2" what="$3"
    local actual
    actual="$(cat "$case_dir/claude-args.log" 2> /dev/null)"
    if [[ "$actual" == "${expected%$'\n'}" ]]; then
        pass "$what"
    else
        fail "unexpected claude commands:
--- expected
$expected
--- actual
$actual"
    fi
}

echo "=== (a) installs, then updates, every plugin the marketplace lists ==="
CASE_A="$TMP/a"
make_case "$CASE_A" changes
if run_case "$CASE_A"; then
    pass "the script exits 0"
else
    fail "the script should exit 0 when every command succeeds"
fi
CASE_A="$(cd "$CASE_A" && pwd -P)"
check_commands "$CASE_A" "$(full_sequence "$CASE_A")" \
    "the claude commands, in order, at local scope, adding the marketplace by its absolute path"
if python3 -c '
import json, sys
state = json.load(open(sys.argv[1]))
sys.exit(0 if state and all(entry["projectPath"] == sys.argv[2] for entry in state) else 1)
' "$CASE_A/state" "$CASE_A/repo"; then
    pass "the commands run from the repository root, so the installs are recorded for it"
else
    fail "the installs should run from the repository root, got state: $(cat "$CASE_A/state")"
fi

echo ""
echo "=== (b) stdout is one line asking for /reload-skills after an install ==="
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
echo "=== (c) plugins already at the pin: one list, nothing else ==="
CASE_C="$TMP/c"
mkdir -p "$CASE_C"
CASE_C="$(cd "$CASE_C" && pwd -P)"
make_case "$CASE_C" "" "$(installed_state "$CASE_C/repo" "$PIN")"
if run_case "$CASE_C"; then
    pass "the script exits 0"
else
    fail "the script should exit 0 when the plugins are already at the pin"
fi
check_commands "$CASE_C" "plugin list --json" "only plugin list --json runs"
STDOUT_C="$(cat "$CASE_C/stdout")"
if [[ "$STDOUT_C" == "[install-plugins] Already installed at the commit .claude/gb4pc-tools pins: alpha beta. "* ]]; then
    pass "it reports that the plugins were already installed"
else
    fail "unexpected stdout for an unchanged install: $STDOUT_C"
fi

echo ""
echo "=== (c2) another checkout's install at the pin does not count ==="
CASE_C2="$TMP/c2"
make_case "$CASE_C2" changes "$(installed_state "$TMP/elsewhere" "$PIN")"
run_case "$CASE_C2"
CASE_C2="$(cd "$CASE_C2" && pwd -P)"
check_commands "$CASE_C2" "$(full_sequence "$CASE_C2")" "the full sequence runs"

echo ""
echo "=== (c3) plugins at an earlier pin are moved, and stdout asks for a restart ==="
CASE_C3="$TMP/c3"
mkdir -p "$CASE_C3"
CASE_C3="$(cd "$CASE_C3" && pwd -P)"
make_case "$CASE_C3" changes "$(installed_state "$CASE_C3/repo" "$OLD_PIN")"
if run_case "$CASE_C3"; then
    pass "the script exits 0"
else
    fail "the script should exit 0 when the update succeeds"
fi
check_commands "$CASE_C3" "$(full_sequence "$CASE_C3")" "the full sequence runs"
STDOUT_C3="$(cat "$CASE_C3/stdout")"
if [[ "$STDOUT_C3" == "[install-plugins] Moved alpha beta "*"until Claude Code restarts"* ]]; then
    pass "it says the moved plugins take effect after a restart"
else
    fail "unexpected stdout for a moved install: $STDOUT_C3"
fi

if [[ "$STDOUT_C3" != *" and installed "* && "$STDOUT_C3" != *"Warning"* ]]; then
    pass "it names no plugin as installed or re-enabled when every plugin was moved"
else
    fail "a run that only moved plugins should report nothing else: $STDOUT_C3"
fi
if [[ "$STDOUT_C3" == *"/orchestrate or another plugin skill is missing"* ]]; then
    pass "its /reload-skills advice covers any missing skill, not only /orchestrate"
else
    fail "the /reload-skills advice should cover any missing skill: $STDOUT_C3"
fi

echo ""
echo "=== (c4) a plugin newly listed beside one at an earlier pin is reported as installed ==="
CASE_C4="$TMP/c4"
mkdir -p "$CASE_C4"
CASE_C4="$(cd "$CASE_C4" && pwd -P)"
make_case "$CASE_C4" changes "[$(entry alpha "$OLD_PIN" "$CASE_C4/repo")]"
if run_case "$CASE_C4"; then
    pass "the script exits 0"
else
    fail "the script should exit 0 when the install and update succeed"
fi
check_commands "$CASE_C4" "$(full_sequence "$CASE_C4")" "the full sequence runs"
STDOUT_C4="$(cat "$CASE_C4/stdout")"
if [[ "$STDOUT_C4" == "[install-plugins] Moved alpha to the commit .claude/gb4pc-tools pins and installed beta there. "*"until Claude Code restarts"* ]]; then
    pass "it names alpha as moved and beta as installed"
else
    fail "unexpected stdout for a moved and a new plugin: $STDOUT_C4"
fi

echo ""
echo "=== (c5) one plugin newly listed beside one at the pin: stdout names it alone, singular ==="
CASE_C5="$TMP/c5"
mkdir -p "$CASE_C5"
CASE_C5="$(cd "$CASE_C5" && pwd -P)"
make_case "$CASE_C5" changes "[$(entry alpha "$PIN" "$CASE_C5/repo")]"
run_case "$CASE_C5"
check_commands "$CASE_C5" "$(printf '%s\n' "plugin list --json" "plugin marketplace add $CASE_C5/repo/.claude/gb4pc-tools --scope local" "plugin install beta@fixture-tools --scope local" "plugin marketplace update fixture-tools" "plugin update beta@fixture-tools --scope local" "plugin list --json")" "only beta is installed and updated, not alpha at the pin"
STDOUT_C5="$(cat "$CASE_C5/stdout")"
if [[ "$STDOUT_C5" == "[install-plugins] Installed beta at the commit .claude/gb4pc-tools pins. It loads in this session only after /reload-skills: ask the user to run /reload-skills." ]]; then
    pass "it names only beta as installed, and says it loads after /reload-skills"
else
    fail "unexpected stdout for one new plugin: $STDOUT_C5"
fi

echo ""
echo "=== (c6) a plugin turned off is left alone, and stdout warns with the command to turn it on ==="
CASE_C6="$TMP/c6"
mkdir -p "$CASE_C6"
CASE_C6="$(cd "$CASE_C6" && pwd -P)"
make_case "$CASE_C6" changes "[$(entry alpha "$PIN" "$CASE_C6/repo"), $(entry beta "$PIN" "$CASE_C6/repo" false)]"
turn_off "$CASE_C6" beta
if run_case "$CASE_C6"; then
    pass "the script exits 0"
else
    fail "the script should exit 0 when the only other plugin is turned off"
fi
check_commands "$CASE_C6" "plugin list --json" "only plugin list --json runs"
STDOUT_C6="$(cat "$CASE_C6/stdout")"
if [[ "$STDOUT_C6" == "[install-plugins] Already installed at the commit .claude/gb4pc-tools pins: alpha. "*"Warning: left alone because turned off for this checkout: beta. Tell the user, who can turn each back on in $CASE_C6/repo with \`claude plugin enable beta@fixture-tools --scope local\`, then rerun .claude/install-plugins.sh." ]]; then
    pass "it warns that beta was left off, with the command that turns it on"
else
    fail "unexpected stdout for a disabled plugin: $STDOUT_C6"
fi

echo ""
echo "=== (c7) a plugin turned off is neither installed nor updated beside one that is ==="
CASE_C7="$TMP/c7"
mkdir -p "$CASE_C7"
CASE_C7="$(cd "$CASE_C7" && pwd -P)"
make_case "$CASE_C7" changes "[$(entry beta "$PIN" "$CASE_C7/repo" false)]"
turn_off "$CASE_C7" beta
if run_case "$CASE_C7"; then
    pass "the script exits 0"
else
    fail "the script should exit 0 when alpha installs and beta is left off"
fi
check_commands "$CASE_C7" "$(printf '%s\n' "plugin list --json" "plugin marketplace add $CASE_C7/repo/.claude/gb4pc-tools --scope local" "plugin install alpha@fixture-tools --scope local" "plugin marketplace update fixture-tools" "plugin update alpha@fixture-tools --scope local" "plugin list --json")" "only alpha is installed and updated"
if [[ "$(cat "$CASE_C7/stdout")" == "[install-plugins] Installed alpha at the commit "*"Warning: left alone because turned off for this checkout: beta."* ]]; then
    pass "it reports alpha as installed and warns that beta was left off"
else
    fail "unexpected stdout: $(cat "$CASE_C7/stdout")"
fi

echo ""
echo "=== (c8) an install whose settings entry is gone counts as missing, not turned off ==="
CASE_C8="$TMP/c8"
mkdir -p "$CASE_C8"
CASE_C8="$(cd "$CASE_C8" && pwd -P)"
make_case "$CASE_C8" changes "[$(entry alpha "$PIN" "$CASE_C8/repo"), $(entry beta "$PIN" "$CASE_C8/repo" false)]"
run_case "$CASE_C8"
check_commands "$CASE_C8" "$(printf '%s\n' "plugin list --json" "plugin marketplace add $CASE_C8/repo/.claude/gb4pc-tools --scope local" "plugin install beta@fixture-tools --scope local" "plugin marketplace update fixture-tools" "plugin update beta@fixture-tools --scope local" "plugin list --json")" "beta is installed again"
if [[ "$(cat "$CASE_C8/stdout")" == "[install-plugins] Installed beta at the commit "* && "$(cat "$CASE_C8/stdout")" != *"Warning"* ]]; then
    pass "it reports beta as installed, with no warning"
else
    fail "unexpected stdout: $(cat "$CASE_C8/stdout")"
fi

echo ""
echo "=== (c9) every plugin turned off: only the list runs, and the warning names both ==="
CASE_C9="$TMP/c9"
mkdir -p "$CASE_C9"
CASE_C9="$(cd "$CASE_C9" && pwd -P)"
make_case "$CASE_C9" changes "[$(entry alpha "$PIN" "$CASE_C9/repo" false), $(entry beta "$OLD_PIN" "$CASE_C9/repo" false)]"
turn_off "$CASE_C9" alpha beta
run_case "$CASE_C9"
check_commands "$CASE_C9" "plugin list --json" "only plugin list --json runs, even for beta at an earlier pin"
if [[ "$(cat "$CASE_C9/stdout")" == "[install-plugins] Warning: left alone because turned off for this checkout: alpha beta. Tell the user, who can turn each back on in $CASE_C9/repo with \`claude plugin enable alpha@fixture-tools --scope local\`; \`claude plugin enable beta@fixture-tools --scope local\`, then rerun .claude/install-plugins.sh." ]]; then
    pass "it warns that both are off, with a command for each"
else
    fail "unexpected stdout: $(cat "$CASE_C9/stdout")"
fi

echo ""
echo "=== (d) a failing command stops the script with a non-zero exit ==="
CASE_D="$TMP/d"
make_case "$CASE_D" changes "[]" "plugin marketplace update"
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
echo "=== (d2) commands that succeed but leave a plugin off the pin fail the run ==="
CASE_D2="$TMP/d2"
make_case "$CASE_D2"
if run_case "$CASE_D2"; then
    fail "the script should exit non-zero when a plugin is still missing afterwards"
else
    pass "the script exits non-zero"
fi
if [[ -s "$CASE_D2/stdout" ]]; then
    fail "that run should print nothing on stdout, got: $(cat "$CASE_D2/stdout")"
else
    pass "that run prints nothing on stdout"
fi
if grep -q 'missing:alpha' "$CASE_D2/stderr"; then
    pass "stderr names the missing plugin"
else
    fail "stderr should name the missing plugin, got: $(cat "$CASE_D2/stderr")"
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
