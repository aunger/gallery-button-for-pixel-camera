#!/usr/bin/env bash
# orchestrate-guard.sh--PreToolUse guard for the /orchestrate workflow.
#
# Purpose: enforce the Orchestrator boundary just in time. The Orchestrator
# may dispatch and relay, but may not read source, edit files, commit, or push
# (see rules/orchestration.md, "What Orchestrators may and may not do").
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
#
# Bash is handled separately. The rules forbid the Orchestrator from reading
# source files even via the shell ("Read source files (Read, Bash cat/grep,
# etc.)"), but the Orchestrator legitimately runs other Bash commands (date -u,
# dispatch_timer.py, ci_monitor.py, git branch). We therefore cannot treat Bash
# itself as forbidden. Instead we inspect the command string and emit an
# advisory only when it looks like source-file reading. Because that heuristic
# can have false positives, a Bash advisory NEVER hard-blocks, even when
# ORCHESTRATE_GUARD_BLOCK=1.

set -u

# Tools an Orchestrator must delegate rather than perform itself.
FORBIDDEN='Edit Write NotebookEdit'

# Commands that read source-file contents. Matched as the program word of a
# Bash command (optionally after a leading path); advisory-only.
SOURCE_READERS='cat grep egrep fgrep rg head tail less more sed awk'

read_stdin() {
    # Slurp stdin without requiring it to be present (hooks may pass empty).
    cat 2>/dev/null || true
}

extract_tool_name() {
    # Pull "tool_name" out of the JSON without a JSON dependency.
    # Matches: "tool_name" : "Value"
    sed -n 's/.*"tool_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1
}

extract_bash_command() {
    # Pull tool_input.command out of the JSON without a JSON dependency.
    # Matches: "command" : "Value" (the first such field).
    sed -n 's/.*"command"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1
}

# Does a Bash command line invoke a source-reading program? Inspect each
# pipeline/list segment's leading program word (after an optional path prefix
# and ignoring leading env-style assignments) against SOURCE_READERS.
bash_reads_source() {
    local cmd="$1" segment word base
    # Split on shell separators so we examine every stage, not just the first.
    local IFS='|&;'
    for segment in $cmd; do
        # Trim leading whitespace, then take the first whitespace-delimited word.
        segment="${segment#"${segment%%[![:space:]]*}"}"
        # Skip leading VAR=value assignments to find the actual program word.
        while :; do
            word="${segment%%[[:space:]]*}"
            case "$word" in
                *=*) segment="${segment#"$word"}"
                     segment="${segment#"${segment%%[![:space:]]*}"}" ;;
                *) break ;;
            esac
        done
        word="${segment%%[[:space:]]*}"
        base="${word##*/}"   # strip any path prefix, e.g. /usr/bin/grep -> grep
        case " $SOURCE_READERS " in
            *" $base "*) return 0 ;;
        esac
    done
    return 1
}

main() {
    local payload tool command
    payload="$(read_stdin)"
    tool="$(printf '%s' "$payload" | extract_tool_name)"

    if [ -z "$tool" ]; then
        # No recognizable tool name; nothing to guard.
        exit 0
    fi

    case " $FORBIDDEN " in
        *" $tool "*)
            printf '[orchestrate-guard] Orchestrator boundary: "%s" edits or writes files.\n' "$tool" >&2
            printf '[orchestrate-guard] Per rules/orchestration.md, dispatch an Author to do this; do not edit, commit, or push yourself.\n' >&2
            if [ "${ORCHESTRATE_GUARD_BLOCK:-0}" = "1" ]; then
                exit 2
            fi
            exit 0
            ;;
    esac

    # Bash is permitted in general, but reading source files through it is not.
    # Advisory only, and never a hard block (the heuristic can have false
    # positives), even under ORCHESTRATE_GUARD_BLOCK=1.
    if [ "$tool" = "Bash" ]; then
        command="$(printf '%s' "$payload" | extract_bash_command)"
        if [ -n "$command" ] && bash_reads_source "$command"; then
            printf '[orchestrate-guard] Orchestrator boundary: this Bash command looks like it reads source files.\n' >&2
            printf '[orchestrate-guard] Per rules/orchestration.md, the Orchestrator does not read source (Read, Bash cat/grep, etc.); dispatch an Author or Reviewer instead.\n' >&2
        fi
        exit 0
    fi

    exit 0
}

main
