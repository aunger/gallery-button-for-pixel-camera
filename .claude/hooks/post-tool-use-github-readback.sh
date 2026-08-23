#!/usr/bin/env bash
# GB4PC: Claude Code PostToolUse hook: read back GitHub writes and diff them.
#
# Configured in .claude/settings.json under PostToolUse with the matcher
# "mcp__github__.*".  After every GitHub MCP tool call, the checker fetches the
# object that was just written and compares the stored text with the text that
# was sent, so silent alteration is reported at the moment it happens rather
# than surviving review (issue #909).
#
# This wrapper carries the never-wedge guarantee; scripts/agents/verify_github_write.py
# carries the thinking.  Exit codes from the checker:
#
#   0  nothing to say (clean, or the call was not a text write)
#   2  a finding on stderr, which Claude Code puts in front of the model
#   *  anything else is a fault in the checker itself: print a warning and
#      exit 0, so a broken checker never blocks a session
#
# The two reported codes are forwarded unchanged.  Every other outcome,
# including a missing interpreter or a crash, becomes a warning and exit 0.

set -uo pipefail

# Resolve the repo root from the script's own path so this hook works in
# worktrees as well as the main checkout.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHECKER="$REPO_ROOT/scripts/agents/verify_github_write.py"

python3 "$CHECKER"
STATUS=$?

case "$STATUS" in
    0 | 2)
        exit "$STATUS"
        ;;
    *)
        echo "[post-tool-use] warning: verify_github_write.py exited $STATUS;" \
             "this GitHub write was NOT verified" >&2
        exit 0
        ;;
esac
