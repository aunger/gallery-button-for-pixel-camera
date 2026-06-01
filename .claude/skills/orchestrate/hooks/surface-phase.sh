#!/usr/bin/env bash
# surface-phase.sh--Just-in-time resource pointer for the /orchestrate workflow.
#
# Purpose: surface the right progressive-revelation resource at the right phase
# without the Orchestrator pre-reading every document. Given a phase name, it
# prints the single resource path to read next. This keeps agent contexts
# uncluttered (issue #164: "progressive revelation architecture").
#
# Usage:
#   surface-phase.sh <phase>
#
# Phases:
#   intake     -> resources/intake.md
#   author     -> resources/dispatch-author.md
#   reviewer   -> resources/dispatch-reviewer.md
#   ci         -> resources/ci-watch.md
#   converge   -> resources/convergence.md
#   model      -> resources/model-selection.md
#
# Output: the resource path (relative to the skill root) on stdout, exit 0.
# Unknown phase: usage on stderr, exit 1.

set -u

# Resolve the plugin root. Precedence: explicit test override, then the
# CLAUDE_PLUGIN_ROOT the harness exports when this runs as a plugin hook, then
# the script's own location (hooks/.. = plugin root).
if [ -n "${ORCHESTRATE_SKILL_ROOT:-}" ]; then
    SKILL_ROOT="$ORCHESTRATE_SKILL_ROOT"
elif [ -n "${CLAUDE_PLUGIN_ROOT:-}" ]; then
    SKILL_ROOT="$CLAUDE_PLUGIN_ROOT"
else
    SKILL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
fi

phase_resource() {
    case "$1" in
        intake)   echo "resources/intake.md" ;;
        author)   echo "resources/dispatch-author.md" ;;
        reviewer) echo "resources/dispatch-reviewer.md" ;;
        ci)       echo "resources/ci-watch.md" ;;
        converge) echo "resources/convergence.md" ;;
        model)    echo "resources/model-selection.md" ;;
        *)        return 1 ;;
    esac
}

main() {
    if [ "$#" -ne 1 ]; then
        echo "usage: surface-phase.sh <intake|author|reviewer|ci|converge|model>" >&2
        exit 1
    fi

    local rel
    if ! rel="$(phase_resource "$1")"; then
        echo "surface-phase.sh: unknown phase '$1'" >&2
        echo "usage: surface-phase.sh <intake|author|reviewer|ci|converge|model>" >&2
        exit 1
    fi

    echo "$SKILL_ROOT/$rel"
}

main "$@"
