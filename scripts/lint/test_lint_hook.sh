#!/usr/bin/env bash
# test_lint_hook.sh: end-to-end tests for scripts/lint/lint.sh and the first-party
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
#   (e) a format-dirty .kt blocks the commit (skipped when ktlint is absent)
#   (f) invalid YAML blocks the commit
#   (g) lint.sh --all runs over the whole tree
#   (h) --check passes on a clean tree
#   (i) --check fails on a lint-dirty .py, format-dirty .md, and .kt (.kt gated
#       on ktlint availability)
#   (j) --check leaves the working tree unmodified
#   (k) --only runs only the named tool family
#   (l) a tracked file over 500 KB fails --all (--enforce-all), while the
#       file-list (hook) path leaves an unlisted large file alone
#   (m) an .md with an unlabeled fenced code block (MD040) blocks the commit,
#       even though mdformat itself has nothing to fix
#
# The lint tools are resolved from $LINT_BIN_DIR (default $HOME/.local/bin),
# exactly as scripts/lint/lint.sh resolves them; .claude/hooks/session-start.sh
# installs them there. When the Python lint stack is not provisioned (a fresh
# checkout, or CI before the tools are installed) the suite skips cleanly and
# exits 0; with the tools present it exits 0 on success, non-zero on any failed
# assertion.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS_DIR="$SCRIPT_DIR/git-hooks"
LINT_SH="$SCRIPT_DIR/lint.sh"
export LINT_BIN_DIR="${LINT_BIN_DIR:-$HOME/.local/bin}"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

# The Python lint tools (the six pre-commit-hooks checks, ruff, and mdformat)
# all come from scripts/lint/requirements-lint.txt as a unit, so they are present or
# absent together. Their absence means a genuinely unprovisioned environment (a
# fresh checkout where session-start has not run) rather than a broken hook, so
# skip the whole suite cleanly and exit 0 instead of reporting a failure. A
# configured session, or CI's "Install lint tools for shell tests" step,
# installs them and the suite runs for real.
PYTHON_TOOLS=(
    trailing-whitespace-fixer end-of-file-fixer check-yaml check-toml
    check-merge-conflict check-added-large-files ruff mdformat
)
for t in "${PYTHON_TOOLS[@]}"; do
    if [[ ! -x "$LINT_BIN_DIR/$t" ]]; then
        echo "SKIP: lint stack not provisioned under \$LINT_BIN_DIR ($LINT_BIN_DIR): $t is missing."
        echo "This is expected in an unprovisioned environment; run session-start.sh to install the tools."
        exit 0
    fi
done

# ktlint (a Maven Central JAR run via java) is provisioned separately from the
# PyPI tools and is legitimately absent where there is no JDK, such as CI's early
# shell-test phase. Gate only the Kotlin case on it; every other case stages
# non-.kt files, so lint.sh never invokes ktlint for them.
KTLINT_AVAILABLE=0
[[ -x "$LINT_BIN_DIR/ktlint" ]] && KTLINT_AVAILABLE=1

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
if [[ $KTLINT_AVAILABLE -eq 1 ]]; then
    REPO="$(new_repo)"
    printf 'fun main( ){println( "hi" )}\n' > "$REPO/Main.kt"
    RC="$(attempt_commit "$REPO")"
    if [[ "$RC" -ne 0 ]]; then pass "dirty .kt -> commit blocked"; else fail "dirty .kt should block"; fi
else
    echo "  SKIP: ktlint not available under \$LINT_BIN_DIR ($LINT_BIN_DIR)"
fi

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

# ── (h) --check passes on a clean tree ───────────────────────────────────────
echo ""
echo "=== (h) --check passes on a clean tree ==="
REPO="$(new_repo)"
printf 'x = 1\n' > "$REPO/clean.py"
printf '# Title\n\nSome text.\n' > "$REPO/clean.md"
# Normalise the fixtures through fix mode so they are fixed points, then assert
# check mode is happy with them.
( cd "$REPO" && "$LINT_SH" clean.py clean.md >/dev/null 2>&1 || true )
( cd "$REPO" && "$LINT_SH" --check clean.py clean.md >/dev/null 2>&1 )
RC=$?
if [[ "$RC" -eq 0 ]]; then pass "--check on clean fixtures passes"; else fail "--check should pass on clean fixtures, rc=$RC"; fi

# ── (i) --check fails on dirty fixtures ──────────────────────────────────────
echo ""
echo "=== (i) --check fails on dirty fixtures ==="
REPO="$(new_repo)"
printf 'import os\nx=1\n' > "$REPO/bad.py"   # unused import (ruff F401) + spacing
( cd "$REPO" && "$LINT_SH" --check --only python bad.py >/dev/null 2>&1 )
RC=$?
if [[ "$RC" -ne 0 ]]; then pass "--check flags a lint-dirty .py"; else fail "--check should flag a lint-dirty .py"; fi
if grep -q 'import os' "$REPO/bad.py"; then pass "--check did not fix bad.py"; else fail "--check must not fix bad.py"; fi

REPO="$(new_repo)"
printf '#    Title\n\n\n\nsome   text\n' > "$REPO/doc.md"
( cd "$REPO" && "$LINT_SH" --check --only markdown doc.md >/dev/null 2>&1 )
RC=$?
if [[ "$RC" -ne 0 ]]; then pass "--check flags a format-dirty .md"; else fail "--check should flag a format-dirty .md"; fi

if [[ $KTLINT_AVAILABLE -eq 1 ]]; then
    REPO="$(new_repo)"
    printf 'fun main( ){println( "hi" )}\n' > "$REPO/Main.kt"
    ( cd "$REPO" && "$LINT_SH" --check --only kotlin Main.kt >/dev/null 2>&1 )
    RC=$?
    if [[ "$RC" -ne 0 ]]; then pass "--check flags a format-dirty .kt"; else fail "--check should flag a format-dirty .kt"; fi
else
    echo "  SKIP: ktlint not available under \$LINT_BIN_DIR ($LINT_BIN_DIR)"
fi

# ── (j) --check leaves the working tree unmodified ───────────────────────────
echo ""
echo "=== (j) --check leaves the tree unmodified ==="
REPO="$(new_repo)"
printf 'x = 1\n' > "$REPO/keep.py"
printf '# Title\n\nText.\n' > "$REPO/keep.md"
( cd "$REPO" && "$LINT_SH" keep.py keep.md >/dev/null 2>&1 || true )
BEFORE="$( cd "$REPO" && sha256sum keep.py keep.md )"
( cd "$REPO" && "$LINT_SH" --check keep.py keep.md >/dev/null 2>&1 )
AFTER="$( cd "$REPO" && sha256sum keep.py keep.md )"
if [[ "$BEFORE" == "$AFTER" ]]; then pass "--check did not modify the fixtures"; else fail "--check modified the fixtures"; fi

# ── (k) --only isolates a tool family ────────────────────────────────────────
echo ""
echo "=== (k) --only isolates a tool family ==="
REPO="$(new_repo)"
printf '#    Title\n\n\n\nsome   text\n' > "$REPO/only.md"
# The python family sees no .py, so a format-dirty .md is invisible to it...
( cd "$REPO" && "$LINT_SH" --check --only python only.md >/dev/null 2>&1 )
RC_PY=$?
# ...but the markdown family flags exactly this file.
( cd "$REPO" && "$LINT_SH" --check --only markdown only.md >/dev/null 2>&1 )
RC_MD=$?
if [[ "$RC_PY" -eq 0 ]]; then pass "--only python ignores a dirty .md"; else fail "--only python should ignore a dirty .md, rc=$RC_PY"; fi
if [[ "$RC_MD" -ne 0 ]]; then pass "--only markdown flags a dirty .md"; else fail "--only markdown should flag a dirty .md"; fi

# ── (l) large file fails --all, hook path unaffected ─────────────────────────
echo ""
echo "=== (l) large file fails --all (--enforce-all) ==="
REPO="$(new_repo)"
printf 'x = 1\n' > "$REPO/small.py"
# A clean 600 KB text file: a single long line plus a trailing newline, so the
# whitespace/eof fixers have nothing to change and only the size check can flag
# it. Committed with --no-verify so the hook (which would block it) is bypassed
# and it becomes a pre-existing tracked file.
head -c 599999 /dev/zero | tr '\0' 'a' > "$REPO/big.txt"
printf '\n' >> "$REPO/big.txt"
git -C "$REPO" add -A
git -C "$REPO" commit -q -m "seed" --no-verify >/dev/null 2>&1
( cd "$REPO" && "$LINT_SH" --check --only hygiene --all >/dev/null 2>&1 )
RC_ALL=$?
if [[ "$RC_ALL" -ne 0 ]]; then pass "--all flags a tracked 600 KB file"; else fail "--all should flag a tracked 600 KB file"; fi
# The hook path lints only the files it is handed, without --enforce-all, so a
# pre-existing large file it was not given must not be flagged.
( cd "$REPO" && "$LINT_SH" --only hygiene small.py >/dev/null 2>&1 )
RC_HOOK=$?
if [[ "$RC_HOOK" -eq 0 ]]; then pass "file-list path ignores an unlisted large file"; else fail "file-list path should not flag an unlisted large file, rc=$RC_HOOK"; fi

# ── (m) unlabeled fenced code block (MD040) blocks the commit ───────────────
echo ""
echo "=== (m) MD040 (unlabeled fence) blocks ==="
REPO="$(new_repo)"
printf '# Title\n\n```\nno language here\n```\n' > "$REPO/md040.md"
RC="$(attempt_commit "$REPO")"
if [[ "$RC" -ne 0 ]]; then pass "MD040 (unlabeled fence) -> commit blocked"; else fail "MD040 (unlabeled fence) should block"; fi
# mdformat has no fix for a missing language, so --check should flag it too,
# and leave the fixture untouched (nothing to auto-fix).
REPO="$(new_repo)"
printf '# Title\n\n```\nno language here\n```\n' > "$REPO/md040.md"
( cd "$REPO" && "$LINT_SH" --check --only markdown md040.md >/dev/null 2>&1 )
RC=$?
if [[ "$RC" -ne 0 ]]; then pass "--check flags MD040 (unlabeled fence)"; else fail "--check should flag MD040 (unlabeled fence)"; fi
if grep -q '^```$' "$REPO/md040.md"; then pass "MD040 fixture left unmodified (no auto-fix exists)"; else fail "MD040 fixture should be left unmodified"; fi
# A labeled fence is not a violation.
REPO="$(new_repo)"
printf '# Title\n\n```python\nprint(1)\n```\n' > "$REPO/labeled.md"
( cd "$REPO" && "$LINT_SH" --check --only markdown labeled.md >/dev/null 2>&1 )
RC=$?
if [[ "$RC" -eq 0 ]]; then pass "--check passes a labeled fence"; else fail "--check should pass a labeled fence, rc=$RC"; fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed."
[[ $FAIL -gt 0 ]] && exit 1
exit 0
