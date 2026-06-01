#!/usr/bin/env bash
# ci_watch.sh--Skill-local entry point for the CI Monitor loop.
#
# The canonical, unit-tested poller is scripts/ci_monitor.py at the repo root.
# Both the standalone agents/ workflow and this /orchestrate skill share that
# one implementation, so this wrapper does not reimplement polling; it just
# resolves the repo root and forwards arguments. Keeping a single poller avoids
# the duplicate-logic trap and keeps CI's test discovery pointed at one file.
#
# Usage:
#   ci_watch.sh --pr <PR_NUMBER> [ci_monitor.py flags...]
#
# Environment:
#   GITHUB_TOKEN          Forwarded to ci_monitor.py (required by it).
#   ORCHESTRATE_REPO_ROOT Override repo-root detection (used by tests).

set -u

resolve_repo_root() {
    if [ -n "${ORCHESTRATE_REPO_ROOT:-}" ]; then
        printf '%s\n' "$ORCHESTRATE_REPO_ROOT"
        return 0
    fi
    # Walk up from this script: hooks/.. = skill, ../../.. = repo root.
    local here
    here="$(cd "$(dirname "$0")" && pwd)"
    # here = <repo>/.claude/skills/orchestrate/scripts
    printf '%s\n' "$(cd "$here/../../../.." && pwd)"
}

main() {
    local root monitor
    root="$(resolve_repo_root)"
    monitor="$root/scripts/ci_monitor.py"

    if [ ! -f "$monitor" ]; then
        echo "ci_watch.sh: canonical poller not found at $monitor" >&2
        exit 1
    fi

    exec python3 "$monitor" "$@"
}

main "$@"
