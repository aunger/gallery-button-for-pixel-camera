#!/usr/bin/env bash
# test_plugin_marketplace.sh: checks .claude/gb4pc-tools/.claude-plugin/marketplace.json,
# the marketplace .claude/install-plugins.sh installs this repository's Claude Code
# plugins from.
#
# Every entry must be a git-subdir source in aunger/dot-and-tilde, at
# plugins/<entry name>, pinned by a full 40-character commit SHA, and every entry
# must carry the same SHA: the plugins are tested together at one dot-and-tilde
# commit, and orchestrate's dependency on github-supplement resolves in this
# marketplace, so two pins would install a pair that was never tested together.
#
# `claude plugin validate` checks the file against Claude Code's own schema.
# It runs only where the `claude` CLI is on PATH, as it is in every Claude Code
# session; CI does not install Claude Code, so there that case is skipped.
#
# Always exits 0 on success, non-zero on failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MARKETPLACE_DIR="$REPO_ROOT/.claude/gb4pc-tools"
MARKETPLACE_JSON="$MARKETPLACE_DIR/.claude-plugin/marketplace.json"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

echo "=== (a) every entry is a git-subdir source pinned to the same full SHA ==="
problems=$(python3 - "$MARKETPLACE_JSON" 2>&1 << 'PY'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    marketplace = json.load(handle)
plugins = marketplace.get("plugins") or []
if not plugins:
    print("the marketplace lists no plugins")
shas = set()
for entry in plugins:
    name = entry.get("name")
    source = entry.get("source")
    if not isinstance(source, dict):
        print(f"{name}: source is not an object")
        continue
    expected = {
        "source": "git-subdir",
        "url": "aunger/dot-and-tilde",
        "path": f"plugins/{name}",
    }
    for key, value in expected.items():
        if source.get(key) != value:
            print(f"{name}: source.{key} is {source.get(key)!r}, expected {value!r}")
    sha = source.get("sha")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        print(f"{name}: source.sha {sha!r} is not a full 40-character commit SHA")
    shas.add(sha)
if len(shas) > 1:
    print(f"the entries pin different commits: {sorted(map(str, shas))}")
PY
)
checker_rc=$?
if [[ "$checker_rc" -eq 0 && -z "$problems" ]]; then
    pass "every entry is pinned to the same dot-and-tilde commit"
else
    fail "marketplace entries:
$problems"
fi

echo ""
echo "=== (b) claude plugin validate accepts the marketplace ==="
if ! command -v claude > /dev/null 2>&1; then
    echo "  SKIP: the claude CLI is not on PATH"
elif output=$(claude plugin validate "$MARKETPLACE_DIR" 2>&1); then
    pass "claude plugin validate passed"
else
    fail "claude plugin validate failed:
$output"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "test_plugin_marketplace.sh: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]] && exit 0
exit 1
