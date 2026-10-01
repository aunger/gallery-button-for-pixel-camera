#!/usr/bin/env bash
# test_post_tool_use_scratchpad_isolation.sh: Shell-based tests for
# .claude/hooks/post-tool-use-scratchpad-isolation.sh.
#
# The hook reads a PostToolUse payload on stdin and needs nothing else, so
# these tests feed it payloads and check the exit code and stderr.
#
# Covers:
#   (a) An agent outside .claude/worktrees/ is exempt
#   (b) A Bash command naming a top-level scratchpad file warns, naming the
#       worktree and the stray path
#   (c) A command naming the worktree elsewhere still warns for a stray path
#       (the incident in issue #1187)
#   (d) Another worktree's subdirectory warns
#   (e) The scratchpad directory itself warns
#   (f) Paths inside the worktree's own subdirectory are silent
#   (g) One good path does not excuse a stray one in the same command
#   (h) A Write to a stray file_path warns; to the subdirectory, is silent
#   (i) A command that does not touch the scratchpad is silent
#   (j) A component that only begins with "scratchpad" is not the scratchpad
#   (k) Other tools, and unreadable input, are silent and exit 0
#   (l) Paths joined by "," or ":" in one token are checked one by one
#
# Modeled on scripts/test_post_tool_use_fetch.sh.
#
# Always exits 0 on success, non-zero on failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="$SCRIPT_DIR/../.claude/hooks/post-tool-use-scratchpad-isolation.sh"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

SP="/tmp/claude-0/-home-user-repo/0000-session/scratchpad"
WT="agent-a1b2c3"
WT_CWD="/home/user/repo/.claude/worktrees/$WT"
TOP_CWD="/home/user/repo"

# payload <cwd> <tool_name> <tool_input as JSON>
payload() {
    jq -cn --arg cwd "$1" --arg tool "$2" --argjson input "$3" \
        '{cwd: $cwd, tool_name: $tool, tool_input: $input}'
}

bash_payload() {
    payload "$1" Bash "$(jq -cn --arg c "$2" '{command: $c}')"
}

write_payload() {
    payload "$1" Write "$(jq -cn --arg p "$2" '{file_path: $p, content: "x"}')"
}

# run_hook <payload>: sets OUTPUT (stderr and stdout) and EXIT_CODE
run_hook() {
    OUTPUT=$(printf '%s' "$1" | bash "$HOOK" 2>&1)
    EXIT_CODE=$?
}

expect_silent() {
    local label="$1"
    if [[ $EXIT_CODE -eq 0 && -z "$OUTPUT" ]]; then
        pass "$label"
    else
        fail "$label: expected silent exit 0, got $EXIT_CODE with '$OUTPUT'"
    fi
}

# expect_warning <label> <text the warning must contain>...
expect_warning() {
    local label="$1"
    shift
    if [[ $EXIT_CODE -ne 2 ]]; then
        fail "$label: expected exit 2, got $EXIT_CODE with '$OUTPUT'"
        return
    fi
    local needle
    for needle in "$@"; do
        if [[ "$OUTPUT" != *"$needle"* ]]; then
            fail "$label: warning lacks '$needle': '$OUTPUT'"
            return
        fi
    done
    pass "$label"
}

echo ""
echo "=== (a) the top-level agent is exempt ==="
run_hook "$(bash_payload "$TOP_CWD" "bash $SP/sweep.sh")"
expect_silent "(a) top-level cwd, top-level scratchpad file"

echo ""
echo "=== (b)-(e) stray scratchpad paths warn ==="
run_hook "$(bash_payload "$WT_CWD" "bash $SP/sweep.sh")"
expect_warning "(b) top-level helper" "'$WT'" "$SP/sweep.sh" "$WT_CWD" "HEAD"

run_hook "$(bash_payload "$WT_CWD" "cd $WT_CWD && bash \"$SP/sweep.sh\"")"
expect_warning "(c) worktree named elsewhere in the command" "$SP/sweep.sh"

run_hook "$(bash_payload "$WT_CWD" "cat $SP/agent-zzz999/results.txt")"
expect_warning "(d) another worktree's subdirectory" "$SP/agent-zzz999/results.txt"

run_hook "$(bash_payload "$WT_CWD" "ls $SP")"
expect_warning "(e) the scratchpad directory itself" "$SP"

echo ""
echo "=== (f)-(g) the worktree's own subdirectory ==="
run_hook "$(bash_payload "$WT_CWD" "mkdir -p $SP/$WT && bash '$SP/$WT/sweep.sh' > $SP/$WT/out.txt")"
expect_silent "(f) every path inside the worktree's subdirectory"

run_hook "$(bash_payload "$WT_CWD" "cp $SP/$WT/sweep.sh $SP/sweep.sh")"
expect_warning "(g) good and stray paths together" "  $SP/sweep.sh"
if [[ "$OUTPUT" == *"  $SP/$WT/sweep.sh"* ]]; then
    fail "(g) the good path was listed as stray"
else
    pass "(g) only the stray path is listed"
fi

echo ""
echo "=== (h) Write file_path ==="
run_hook "$(write_payload "$WT_CWD" "$SP/sweep.sh")"
expect_warning "(h) Write at the top level" "$SP/sweep.sh"

run_hook "$(write_payload "$WT_CWD" "$SP/$WT/sweep.sh")"
expect_silent "(h) Write in the worktree's subdirectory"

echo "=== (l) paths joined in one token are checked one by one ==="
run_hook "$(bash_payload "$WT_CWD" "bash $SP/$WT/a.sh,$SP/b")"
expect_warning "(l) comma-joined: the stray second path" "  $SP/b"

run_hook "$(bash_payload "$WT_CWD" "PATH=$SP/$WT:\$PATH PYTHONPATH=$SP/$WT/lib foo")"
expect_silent "(l) PATH= and colon-joined paths inside the subdirectory"

run_hook "$(bash_payload "$WT_CWD" "PYTHONPATH=/opt/lib:$SP/lib foo")"
expect_warning "(l) colon-joined: the stray second path" "  $SP/lib"

echo ""
echo "=== (i)-(k) unrelated calls are silent ==="
run_hook "$(bash_payload "$WT_CWD" "git status")"
expect_silent "(i) no scratchpad path"

run_hook "$(bash_payload "$WT_CWD" "ls /tmp/scratchpad2/x /tmp/scratchpads")"
expect_silent "(j) scratchpad2 and scratchpads are other names"

run_hook "$(payload "$WT_CWD" Read "$(jq -cn --arg p "$SP/sweep.sh" '{file_path: $p}')")"
expect_silent "(k) a tool the hook does not check"

run_hook "not json"
expect_silent "(k) unreadable input"

run_hook ""
expect_silent "(k) empty input"

# Summary--------------------------------------------------------------------
echo ""
echo "Results: $PASS passed, $FAIL failed."
if [[ $FAIL -gt 0 ]]; then
    exit 1
fi
exit 0
