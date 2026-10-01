#!/usr/bin/env bash
# GB4PC: Claude Code PostToolUse hook: warn a worktree sub-agent that touched
# the shared scratchpad outside its own subdirectory (issue #1187).
#
# Configured in .claude/settings.json under PostToolUse with the matcher
# "Bash|Write".  Every sub-agent in a session gets the same scratchpad
# directory, so a helper one agent leaves at its top level can be overwritten
# or run by another, which then reports a result from a tree it never touched.
# AGENTS.md asks each agent to keep scratch files under a subdirectory named
# after its worktree directory; this hook warns when a call strays from that.
#
# What it checks: the Bash command, or the Write file_path, for every path that
# has a component named "scratchpad".  Each one must continue into a
# subdirectory named after the agent's worktree.  A single path that does not
# draws the warning, even when the same command names the worktree elsewhere
# (a "cd <worktree> && bash <scratchpad>/sweep.sh" is the incident in #1187).
#
# Who it checks: only an agent whose cwd (from the hook's input) lies under
# .claude/worktrees/.  The worktree name comes from that cwd rather than from
# this script's own path, because $CLAUDE_PROJECT_DIR can point a worktree
# agent at the main checkout's copy of this script.
#
# What it misses: a path held in a variable set by an earlier command, and a
# script that writes to the scratchpad internally.  It is a warning, not a
# guard; the rule's second point (print the worktree and HEAD a result came
# from) is what catches the failure.
#
# Exit codes:
#   0  nothing to say, or the input could not be read
#   2  a warning on stderr, which Claude Code puts in front of the model; the
#      tool has already run, so nothing is blocked

set -uo pipefail

INPUT="$(cat)"

read_field() {
    jq -r "$1 // empty" <<< "$INPUT" 2> /dev/null
}

CWD="$(read_field '.cwd')" || exit 0
case "$CWD" in
    */.claude/worktrees/*) ;;
    *) exit 0 ;;
esac
WORKTREE="${CWD#*/.claude/worktrees/}"
WORKTREE="${WORKTREE%%/*}"
[[ -n "$WORKTREE" ]] || exit 0

case "$(read_field '.tool_name')" in
    Bash) TEXT="$(read_field '.tool_input.command')" ;;
    Write) TEXT="$(read_field '.tool_input.file_path')" ;;
    *) exit 0 ;;
esac
[[ -n "$TEXT" ]] || exit 0

# Each match is one whole path token with a "/scratchpad" in it.
TOKEN="[^[:space:]\"';|&()<>]*"
STRAYED=()
while IFS= read -r MATCH; do
    REST="${MATCH#*/scratchpad}"
    case "$REST" in
        "") ;;           # the scratchpad itself
        /*) ;;           # something inside it
        *) continue ;;   # "/scratchpad2" and the like are other names
    esac
    REST="${REST#/}"
    if [[ "${REST%%/*}" != "$WORKTREE" ]]; then
        STRAYED+=("$MATCH")
    fi
done < <(grep -oE "$TOKEN/scratchpad$TOKEN" <<< "$TEXT")

[[ ${#STRAYED[@]} -gt 0 ]] || exit 0

{
    echo "[scratchpad-isolation] warning: this call used the shared scratchpad" \
         "outside the subdirectory for your worktree, '$WORKTREE':"
    printf '  %s\n' "${STRAYED[@]}"
    echo "Other sub-agents share that directory and may have written or run" \
         "anything there.  Keep your files under scratchpad/$WORKTREE/, and" \
         "before reporting a helper's result, confirm it printed this" \
         "worktree ($CWD) and its HEAD.  See AGENTS.md."
} >&2
exit 2
