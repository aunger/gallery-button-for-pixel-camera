#!/usr/bin/env bash
# GB4PC: Claude Code PostToolUse hook: git fetch after Agent tool calls.
#
# Configured in .claude/settings.json under PostToolUse with a matcher on
# tool_name == "Agent".  Runs once each time a sub-agent finishes, so the
# parent session sees any commits the sub-agent pushed before it exited.
#
# Failures are non-fatal: a warning is printed but the exit code is always 0
# so the session continues even if the network is temporarily unavailable.

set -uo pipefail

# Resolve the repo root from the script's own path so this hook works in
# worktrees as well as the main checkout.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

git -C "$REPO_ROOT" fetch --prune --quiet \
    && echo "[post-tool-use] git fetch complete" \
    || echo "[post-tool-use] warning: git fetch failed"

exit 0
