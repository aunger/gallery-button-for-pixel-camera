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
#   (i) --check fails on a lint-dirty .py, format-dirty .md, and .kt, and
#       passes each with the violation removed (.kt gated on ktlint
#       availability)
#   (j) --check leaves the working tree unmodified
#   (k) --only runs only the named tool family, and that family passes the
#       same file once it is clean
#   (l) a tracked file over 500 KB fails --all (--enforce-all) and the same
#       tree without it passes, while the file-list (hook) path leaves an
#       unlisted large file alone
#   (m) an .md with an unlabeled fenced code block (MD040) blocks the commit,
#       even though mdformat itself has nothing to fix
#   (n) invalid JSON blocks the commit, valid JSON does not
#   (o) invalid XML blocks the commit, valid XML does not
#   (p) a script with a shebang but no executable bit blocks the commit, and the
#       same script marked executable does not; an extensionless script is
#       covered, while a .py is deferred (#887)
#   (q) an em dash blocks the commit and fails --check, while the same text with
#       a comma instead commits and passes --check, and the vendored gradlew is
#       exempt (#964)
#
# The lint tools are resolved from $LINT_BIN_DIR (default $HOME/.local/bin),
# exactly as scripts/lint/lint.sh resolves them; .claude/hooks/session-start.sh
# installs them there. When the Python lint stack is not provisioned (a fresh
# checkout, or CI before the tools are installed) the suite skips cleanly and
# exits 0; with the tools present it exits 0 on success, non-zero on any failed
# assertion.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOKS_DIR="$SCRIPT_DIR/../git-hooks"
LINT_SH="$SCRIPT_DIR/lint.sh"
export LINT_BIN_DIR="${LINT_BIN_DIR:-$HOME/.local/bin}"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

# The Python lint tools (the pre-commit-hooks checks, ruff, and mdformat)
# all come from scripts/lint/requirements-lint.txt as a unit, so they are present or
# absent together. Their absence means a genuinely unprovisioned environment (a
# fresh checkout where session-start has not run) rather than a broken hook, so
# skip the whole suite cleanly and exit 0 instead of reporting a failure. A
# configured session, or CI's "Install lint tools for shell tests" step,
# installs them and the suite runs for real.
PYTHON_TOOLS=(
    trailing-whitespace-fixer end-of-file-fixer check-yaml check-toml
    check-json check-xml check-merge-conflict check-added-large-files
    check-shebang-scripts-are-executable ruff mdformat
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
#
# The repo sits outside this checkout, so ruff would not find this repo's
# ruff.toml and would lint with its own built-in defaults, which change between
# ruff releases (0.16 enables EXE001, which fails case (p)). Copying the config in
# makes every case lint with the rules this repo actually runs.
new_repo() {
    local repo
    repo="$(mktemp -d "$TMP/repo.XXXXXX")"
    git -C "$repo" init -q -b main
    git -C "$repo" config core.hooksPath "$HOOKS_DIR"
    cp "$SCRIPT_DIR/../../ruff.toml" "$repo/"
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
# The same bad.py without the unused import and with the spacing fixed is not a
# violation. Without this control, a fault that fails every --check run (a
# missing tool, a bad flag) would still report the dirty .py as flagged.
REPO="$(new_repo)"
printf 'x = 1\n' > "$REPO/bad.py"
( cd "$REPO" && "$LINT_SH" --check --only python bad.py >/dev/null 2>&1 )
RC=$?
if [[ "$RC" -eq 0 ]]; then pass "--check passes a lint-clean .py"; else fail "--check should pass a lint-clean .py, rc=$RC"; fi

REPO="$(new_repo)"
printf '#    Title\n\n\n\nsome   text\n' > "$REPO/doc.md"
( cd "$REPO" && "$LINT_SH" --check --only markdown doc.md >/dev/null 2>&1 )
RC=$?
if [[ "$RC" -ne 0 ]]; then pass "--check flags a format-dirty .md"; else fail "--check should flag a format-dirty .md"; fi
# The same doc.md, formatted, is not a violation.
REPO="$(new_repo)"
printf '# Title\n\nsome text\n' > "$REPO/doc.md"
( cd "$REPO" && "$LINT_SH" --check --only markdown doc.md >/dev/null 2>&1 )
RC=$?
if [[ "$RC" -eq 0 ]]; then pass "--check passes a format-clean .md"; else fail "--check should pass a format-clean .md, rc=$RC"; fi

if [[ $KTLINT_AVAILABLE -eq 1 ]]; then
    REPO="$(new_repo)"
    printf 'fun main( ){println( "hi" )}\n' > "$REPO/Main.kt"
    ( cd "$REPO" && "$LINT_SH" --check --only kotlin Main.kt >/dev/null 2>&1 )
    RC=$?
    if [[ "$RC" -ne 0 ]]; then pass "--check flags a format-dirty .kt"; else fail "--check should flag a format-dirty .kt"; fi
    # The same Main.kt, formatted, is not a violation.
    REPO="$(new_repo)"
    printf 'fun main() {\n    println("hi")\n}\n' > "$REPO/Main.kt"
    ( cd "$REPO" && "$LINT_SH" --check --only kotlin Main.kt >/dev/null 2>&1 )
    RC=$?
    if [[ "$RC" -eq 0 ]]; then pass "--check passes a format-clean .kt"; else fail "--check should pass a format-clean .kt, rc=$RC"; fi
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
# The same only.md, formatted, passes the markdown family. RC_PY above does not
# control RC_MD: it runs the python family, which a missing mdformat leaves at 0.
REPO="$(new_repo)"
printf '# Title\n\nsome text\n' > "$REPO/only.md"
( cd "$REPO" && "$LINT_SH" --check --only markdown only.md >/dev/null 2>&1 )
RC=$?
if [[ "$RC" -eq 0 ]]; then pass "--only markdown passes a clean .md"; else fail "--only markdown should pass a clean .md, rc=$RC"; fi

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
# The same tree without big.txt passes the same invocation. RC_HOOK below does
# not control RC_ALL: it drops both --check and --all.
REPO="$(new_repo)"
printf 'x = 1\n' > "$REPO/small.py"
git -C "$REPO" add -A
git -C "$REPO" commit -q -m "seed" --no-verify >/dev/null 2>&1
( cd "$REPO" && "$LINT_SH" --check --only hygiene --all >/dev/null 2>&1 )
RC=$?
if [[ "$RC" -eq 0 ]]; then pass "--all passes a tree without the 600 KB file"; else fail "--all should pass a tree without the 600 KB file, rc=$RC"; fi
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

# ── (n) invalid JSON blocks the commit ───────────────────────────────────────
echo ""
echo "=== (n) invalid JSON blocks ==="
REPO="$(new_repo)"
printf '{"a": 1,}\n' > "$REPO/broken.json"
RC="$(attempt_commit "$REPO")"
if [[ "$RC" -ne 0 ]]; then pass "invalid JSON -> commit blocked"; else fail "invalid JSON should block"; fi
# Valid JSON is not a violation.
REPO="$(new_repo)"
printf '{"a": 1}\n' > "$REPO/ok.json"
RC="$(attempt_commit "$REPO")"
if [[ "$RC" -eq 0 ]]; then pass "valid JSON -> commit allowed"; else fail "valid JSON should commit, rc=$RC"; fi

# ── (o) invalid XML blocks the commit ────────────────────────────────────────
echo ""
echo "=== (o) invalid XML blocks ==="
REPO="$(new_repo)"
printf '<a><b></a>\n' > "$REPO/broken.xml"
RC="$(attempt_commit "$REPO")"
if [[ "$RC" -ne 0 ]]; then pass "invalid XML -> commit blocked"; else fail "invalid XML should block"; fi
# Valid XML is not a violation.
REPO="$(new_repo)"
printf '<a><b/></a>\n' > "$REPO/ok.xml"
RC="$(attempt_commit "$REPO")"
if [[ "$RC" -eq 0 ]]; then pass "valid XML -> commit allowed"; else fail "valid XML should commit, rc=$RC"; fi

# ── (p) a shebang script without the executable bit blocks the commit ────────
#
# This is the case that was live in this repository: .claude/settings.json wires
# .claude/hooks/session-start.sh as a bare command, and the file was recorded
# 100644, so the hook failed with "Permission denied" and never ran. The check
# reads git's filemode, so the fixture must be staged at mode 100644 rather than
# merely chmod'd in the working tree.
echo ""
echo "=== (p) shebang script not marked executable blocks ==="
REPO="$(new_repo)"
printf '#!/usr/bin/env bash\necho hi\n' > "$REPO/tool.sh"
chmod -x "$REPO/tool.sh"
RC="$(attempt_commit "$REPO")"
if [[ "$RC" -ne 0 ]]; then pass "non-executable shebang script -> commit blocked"; else fail "non-executable shebang script should block"; fi
# The same script with the executable bit set is not a violation.
REPO="$(new_repo)"
printf '#!/usr/bin/env bash\necho hi\n' > "$REPO/tool.sh"
chmod +x "$REPO/tool.sh"
RC="$(attempt_commit "$REPO")"
if [[ "$RC" -eq 0 ]]; then pass "executable shebang script -> commit allowed"; else fail "executable shebang script should commit, rc=$RC"; fi
# An extensionless script is covered too. This is the case that matters most:
# scripts/git-hooks/pre-commit and gradlew both carry a shebang and no
# extension, and git does not error on a non-executable hook, it prints an
# advice.ignoredHook hint and commits anyway, silently skipping every linter.
REPO="$(new_repo)"
printf '#!/usr/bin/env bash\necho hi\n' > "$REPO/extensionless-tool"
chmod -x "$REPO/extensionless-tool"
RC="$(attempt_commit "$REPO")"
if [[ "$RC" -ne 0 ]]; then pass "non-executable extensionless script -> commit blocked"; else fail "non-executable extensionless script should block"; fi
# A .py file is out of scope for now (see the hygiene block in lint.sh, #887).
REPO="$(new_repo)"
printf '#!/usr/bin/env python3\nprint(1)\n' > "$REPO/tool.py"
chmod -x "$REPO/tool.py"
RC="$(attempt_commit "$REPO")"
if [[ "$RC" -eq 0 ]]; then pass "non-executable .py -> commit allowed (deferred)"; else fail "non-executable .py should not block yet, rc=$RC"; fi

# ── (q) a character the prose-style rule bans blocks the commit ──────────────
echo ""
echo "=== (q) banned prose character blocks ==="
REPO="$(new_repo)"
printf '// Debug signing is fine \342\200\224 installed only on emulators.\n' > "$REPO/notes.txt"
RC="$(attempt_commit "$REPO")"
if [[ "$RC" -ne 0 ]]; then pass "em dash -> commit blocked"; else fail "em dash should block"; fi
( cd "$REPO" && "$LINT_SH" --check --only hygiene notes.txt >/dev/null 2>&1 )
RC=$?
if [[ "$RC" -ne 0 ]]; then pass "--check flags an em dash"; else fail "--check should flag an em dash"; fi
# The same notes.txt with the em dash replaced by a comma is not a violation.
# Without this control, a setup fault that blocked every commit in a scratch repo
# would still report the em dash as blocked.
REPO="$(new_repo)"
printf '// Debug signing is fine, installed only on emulators.\n' > "$REPO/notes.txt"
RC="$(attempt_commit "$REPO")"
if [[ "$RC" -eq 0 ]]; then pass "same text without the em dash -> commit allowed"; else fail "em-dash-free notes.txt should commit, rc=$RC"; fi
( cd "$REPO" && "$LINT_SH" --check --only hygiene notes.txt >/dev/null 2>&1 )
RC=$?
if [[ "$RC" -eq 0 ]]; then pass "--check passes the same text without the em dash"; else fail "--check should pass the same text without the em dash, rc=$RC"; fi
# The vendored Gradle wrapper script is exempt.
REPO="$(new_repo)"
printf '#!/bin/sh\n#   \302\253case\302\273\n' > "$REPO/gradlew"
chmod +x "$REPO/gradlew"
RC="$(attempt_commit "$REPO")"
if [[ "$RC" -eq 0 ]]; then pass "angle quotes in gradlew -> commit allowed (vendored)"; else fail "gradlew should be exempt, rc=$RC"; fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed."
[[ $FAIL -gt 0 ]] && exit 1
exit 0
