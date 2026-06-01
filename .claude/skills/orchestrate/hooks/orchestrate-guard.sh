#!/usr/bin/env bash
# orchestrate-guard.sh--PreToolUse guard for the /orchestrate workflow.
#
# Purpose: enforce the Orchestrator boundary just in time. The Orchestrator
# may dispatch and relay, but may not read source, edit files, commit, or push
# (see agents/dev_orchestration.md, "What Orchestrators may and may not do").
# This hook surfaces a reminder when such a tool is about to run while the
# orchestrate workflow is active. It is advisory by default: it prints guidance
# to stderr and exits 0 so the human-in-the-loop decides. Set
# ORCHESTRATE_GUARD_BLOCK=1 to turn the reminder into a hard block (exit 2).
#
# Input: a PreToolUse hook JSON object on stdin, e.g.
#   {"tool_name":"Edit","tool_input":{...}}
#
# Output:
#   exit 0 --allow (default), optionally with an advisory reminder on stderr.
#   exit 2 --block, only when ORCHESTRATE_GUARD_BLOCK=1 and the tool is forbidden.
#
# The set of forbidden tools is the editing/committing surface an Orchestrator
# must delegate. Read-only inspection tools are intentionally not listed,
# because reading an issue or PR is permitted.

set -u

# Tools an Orchestrator must delegate rather than perform itself.
FORBIDDEN='Edit Write NotebookEdit'

read_stdin() {
    # Slurp stdin without requiring it to be present (hooks may pass empty).
    cat 2>/dev/null || true
}

extract_tool_name() {
    # Pull "tool_name" out of the JSON without a JSON dependency.
    # Matches: "tool_name" : "Value"
    sed -n 's/.*"tool_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1
}

main() {
    local payload tool
    payload="$(read_stdin)"
    tool="$(printf '%s' "$payload" | extract_tool_name)"

    if [ -z "$tool" ]; then
        # No recognizable tool name; nothing to guard.
        exit 0
    fi

    case " $FORBIDDEN " in
        *" $tool "*)
            printf '[orchestrate-guard] Orchestrator boundary: "%s" edits or writes files.\n' "$tool" >&2
            printf '[orchestrate-guard] Per agents/dev_orchestration.md, dispatch an Author to do this; do not edit, commit, or push yourself.\n' >&2
            if [ "${ORCHESTRATE_GUARD_BLOCK:-0}" = "1" ]; then
                exit 2
            fi
            exit 0
            ;;
        *)
            exit 0
            ;;
    esac
}

main
