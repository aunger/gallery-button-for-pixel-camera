#!/usr/bin/env bash
# test_lint_hook.sh: end-to-end tests for scripts/lint.sh and the first-party
# git pre-commit hook (scripts/git-hooks/pre-commit) that replaced the
# pre-commit framework (issue #667).
#
# Each test builds a throwaway git repo whose core.hooksPath points at this
# repo's scripts/git-hooks, stages a fixture, and runs a real `git commit` to
# observe whether the hook blocks it. Covers:
#   (a) a clean tree commits successfully
#   (b) trailing whitespace is fixed and blocks the commit (re-stage contract)
#   (c) a lint/format-dirty .py blocks the commit
#   (d) a format-dirty .md blocks the commit
#   (e) a format-dirty .kt blocks the commit
#   (f) invalid YAML blocks the commit
#   (g) lint.sh --all runs over the whole tree
#
# The lint tools are resolved from $LINT_BIN_DIR (default $HOME/.local/bin),
# exactly as scripts/lint.sh resolves them; .claude/hooks/session-start.sh
# installs them there. Always exits 0 on success, non-zero on failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS_DIR="$SCRIPT_DIR/git-hooks"
LINT_SH="$SCRIPT_DIR/lint.sh"
export LINT_BIN_DIR="${LINT_BIN_DIR:-$HOME/.local/bin}"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

# Fail loudly if a lint tool is missing: in a configured session every tool
# below is installed under $LINT_BIN_DIR, so absence is a real setup problem,
# not a reason to silently skip coverage.
REQUIRED=(
    trailing-whitespace-fixer end-of-file-fixer check-yaml check-toml
    check-merge-conflict check-added-large-files ruff mdformat ktlint
)
missing=0
for t in "${REQUIRED[@]}"; do
    [[ -x "$LINT_BIN_DIR/$t" ]] || { echo "  MISSING TOOL: $LINT_BIN_DIR/$t"; missing=1; }
done
if [[ $missing -ne 0 ]]; then
    echo "Required lint tools are not installed under \$LINT_BIN_DIR ($LINT_BIN_DIR)."
    echo "Run the session-start hook, or set LINT_BIN_DIR to where they live."
    exit 1
fi

export GIT_AUTHOR_NAME="Test" GIT_AUTHOR_EMAIL="test@example.com"
export GIT_COMMITTER_NAME="Test" GIT_COMMITTER_EMAIL="test@example.com"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Create a fresh repo wired to the real hook, and print its path.
new_repo() {
    local repo
    repo="$(mktemp -d "$TMP/repo.XXXXXX")"
    git -C "$repo" init -q -b main
    git -C "$repo" config core.hooksPath "$HOOKS_DIR"
    echo "$repo"
}

# Stage everything and attempt a commit; echo the commit's exit code.
attempt_commit() {
    local repo="$1"
    git -C "$repo" add -A
    ( cd "$repo" && git commit -q -m "test" >/dev/null 2>&1 )
    echo $?
}

# ── (a) clean tree commits successfully ──────────────────────────────────────
echo ""
echo "=== (a) clean tree commits ==="
REPO="$(new_repo)"
printf 'hello\n' > "$REPO/notes.txt"
printf 'x = 1\n' > "$REPO/mod.py"
# Normalise the .py through the real tools so the fixture is a fixed point.
( cd "$REPO" && "$LINT_SH" mod.py >/dev/null 2>&1 || true )
RC="$(attempt_commit "$REPO")"
if [[ "$RC" -eq 0 ]]; then pass "clean tree -> commit succeeds"; else fail "clean tree should commit, rc=$RC"; fi

# ── (b) trailing whitespace blocks the commit ────────────────────────────────
echo ""
echo "=== (b) trailing whitespace blocks ==="
REPO="$(new_repo)"
printf 'line with trailing space   \n' > "$REPO/ws.txt"
RC="$(attempt_commit "$REPO")"
if [[ "$RC" -ne 0 ]]; then pass "trailing whitespace -> commit blocked"; else fail "trailing whitespace should block"; fi
if ! grep -q ' $' "$REPO/ws.txt"; then pass "trailing whitespace was fixed in place"; else fail "trailing whitespace not fixed"; fi

# ── (c) lint/format-dirty .py blocks the commit ──────────────────────────────
echo ""
echo "=== (c) dirty .py blocks ==="
REPO="$(new_repo)"
printf 'import os\nx=1\n' > "$REPO/bad.py"   # unused import (ruff F401) + spacing
RC="$(attempt_commit "$REPO")"
if [[ "$RC" -ne 0 ]]; then pass "dirty .py -> commit blocked"; else fail "dirty .py should block"; fi

# ── (d) format-dirty .md blocks the commit ───────────────────────────────────
echo ""
echo "=== (d) dirty .md blocks ==="
REPO="$(new_repo)"
printf '#    Title\n\n\n\nsome   text\n' > "$REPO/doc.md"
RC="$(attempt_commit "$REPO")"
if [[ "$RC" -ne 0 ]]; then pass "dirty .md -> commit blocked"; else fail "dirty .md should block"; fi

# ── (e) format-dirty .kt blocks the commit ───────────────────────────────────
echo ""
echo "=== (e) dirty .kt blocks ==="
REPO="$(new_repo)"
printf 'fun main( ){println( "hi" )}\n' > "$REPO/Main.kt"
RC="$(attempt_commit "$REPO")"
if [[ "$RC" -ne 0 ]]; then pass "dirty .kt -> commit blocked"; else fail "dirty .kt should block"; fi

# ── (f) invalid YAML blocks the commit ───────────────────────────────────────
echo ""
echo "=== (f) invalid YAML blocks ==="
REPO="$(new_repo)"
printf 'key: value\n  bad: : indentation\n:::\n' > "$REPO/broken.yaml"
RC="$(attempt_commit "$REPO")"
if [[ "$RC" -ne 0 ]]; then pass "invalid YAML -> commit blocked"; else fail "invalid YAML should block"; fi

# ── (g) lint.sh --all runs over the whole tree ───────────────────────────────
echo ""
echo "=== (g) lint.sh --all over a clean tree ==="
REPO="$(new_repo)"
printf 'hello\n' > "$REPO/a.txt"
printf 'x = 1\n' > "$REPO/b.py"
git -C "$REPO" add -A
( cd "$REPO" && "$LINT_SH" --all >/dev/null 2>&1 )
RC=$?
if [[ "$RC" -eq 0 ]]; then pass "lint.sh --all -> clean tree passes"; else fail "lint.sh --all should pass on a clean tree, rc=$RC"; fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed."
[[ $FAIL -gt 0 ]] && exit 1
exit 0
