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
# session and in build.yml's shell-tests job, which installs a pinned version;
# elsewhere that case is skipped. Under GitHub Actions (GITHUB_ACTIONS=true, which
# the runner sets in every step) a missing `claude` is a failure instead, so CI
# cannot drift back to skipping the validate unseen. The runner's own variable is
# used, rather than one a workflow sets, so that dropping an env: entry, or an
# action that stops passing one through, cannot turn the check off (issue #1418).
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
    if [[ "${GITHUB_ACTIONS:-}" == true ]]; then
        fail "the claude CLI is not on PATH, and GITHUB_ACTIONS=true says it must be"
    else
        echo "  SKIP: the claude CLI is not on PATH"
    fi
elif output=$(claude plugin validate "$MARKETPLACE_DIR" 2>&1); then
    pass "claude plugin validate passed"
else
    fail "claude plugin validate failed:
$output"
fi

# Case (c) reruns this script with a PATH that holds no `claude`, so it must not
# recurse: the rerun sets _MARKETPLACE_TEST_RERUN and stops after case (b).
if [[ -z "${_MARKETPLACE_TEST_RERUN:-}" ]]; then
    echo ""
    echo "=== (c) without the claude CLI, case (b) skips, or fails under GITHUB_ACTIONS=true ==="
    no_claude_bin=$(mktemp -d)
    trap 'rm -rf "$no_claude_bin"' EXIT
    for tool in python3 dirname; do
        ln -s "$(command -v "$tool")" "$no_claude_bin/$tool"
    done
    rerun() {
        env -u GITHUB_ACTIONS PATH="$no_claude_bin" _MARKETPLACE_TEST_RERUN=1 "$@" \
            "$BASH" "${BASH_SOURCE[0]}" 2>&1
    }
    if output=$(rerun) && grep -q "SKIP: the claude CLI is not on PATH" <<< "$output"; then
        pass "outside GitHub Actions, case (b) skips and the script exits 0"
    else
        fail "outside GitHub Actions, case (b) did not skip cleanly:
$output"
    fi
    if ! output=$(rerun GITHUB_ACTIONS=true) && grep -q "FAIL: the claude CLI is not on PATH" <<< "$output"; then
        pass "with GITHUB_ACTIONS=true, case (b) fails and the script exits non-zero"
    else
        fail "with GITHUB_ACTIONS=true, case (b) did not fail:
$output"
    fi
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "test_plugin_marketplace.sh: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]] && exit 0
exit 1
