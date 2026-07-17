#!/usr/bin/env bash
# scripts/lint.sh -- run this repo's linters and formatters over a set of files.
#
# Usage:
#   scripts/lint.sh FILE [FILE ...]   lint the named files
#   scripts/lint.sh --all             lint every tracked file in the tree
#
# Options (combine with either form above):
#   --check                 run each tool in its check-only invocation and report,
#                           rather than auto-fixing. See "Check mode" below.
#   --only python|markdown|kotlin|hygiene
#                           run only one tool family, skipping the others.
#
# This is the single source of truth for "run the linters". The git pre-commit
# hook (scripts/git-hooks/pre-commit) calls it with the staged file set; the CI
# lint workflow (.github/workflows/lint.yml) calls it with --check --only <fam>
# --all, so the hook and CI share one definition of how each tool runs.
#
# Each tool is resolved by explicit path under $LINT_BIN_DIR (default
# $HOME/.local/bin), where .claude/hooks/session-start.sh installs it at
# session start from a trusted package registry (PyPI or Maven Central). No
# hook repository is git-cloned and nothing is fetched from GitHub Releases at
# run time (issue #667). Point $LINT_BIN_DIR elsewhere to run against tools
# installed in a different location (the test harness does this).
#
# Default (fix) mode: the formatters run as auto-fixers where they support it
# (ruff --fix, ruff format, mdformat, ktlint --format) and rewrite files in
# place. lint.sh exits non-zero if any tool reports a problem (an unfixable
# violation, or a fixer that had to change a file). Detecting which staged files
# a fixer touched, so the commit can be aborted with a re-stage message, is the
# caller's job; see scripts/git-hooks/pre-commit.
#
# Check mode (--check): each fixer runs in its check-only invocation instead:
# `ruff check` (no --fix), `ruff format --check`, `mdformat --check`, and
# `ktlint` (no --format). The read-only hygiene checks (check-yaml, check-toml,
# check-merge-conflict, check-added-large-files) are unchanged. The two
# whitespace fixers (trailing-whitespace-fixer, end-of-file-fixer) have no
# check-only mode, so they run as usual and their non-zero exit on any change
# fails the run, without altering the pass/fail contract. On a clean tree check
# mode writes nothing and exits 0.
#
# scripts/check_md040.py (markdown family) is read-only in both modes: MD040
# (a fenced code block must name a language) has no auto-fix, so it always
# reports rather than rewriting (issue #689).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LINT_BIN_DIR="${LINT_BIN_DIR:-$HOME/.local/bin}"

usage() {
    echo "usage: lint.sh [--check] [--only python|markdown|kotlin|hygiene] FILE [FILE ...] | --all" >&2
    exit 2
}

# ---------------------------------------------------------------------------
# Parse options, then resolve the target file list.
# ---------------------------------------------------------------------------
CHECK=0
ONLY=""
ALL=0
declare -a FILES=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --check) CHECK=1; shift ;;
        --only)
            ONLY="${2:-}"
            case "$ONLY" in
                python | markdown | kotlin | hygiene) ;;
                *) usage ;;
            esac
            shift 2
            ;;
        --all) ALL=1; shift ;;
        --) shift; FILES+=("$@"); break ;;
        -*) usage ;;
        *) FILES+=("$1"); shift ;;
    esac
done

if [[ $ALL -eq 1 ]]; then
    # --all lints the whole tree and takes no explicit file arguments.
    [[ ${#FILES[@]} -eq 0 ]] || usage
    mapfile -d '' -t FILES < <(git ls-files -z)
elif [[ ${#FILES[@]} -eq 0 ]]; then
    usage
fi

# want FAMILY -> true if that tool family should run, honoring --only. With no
# --only, every family runs.
want() {
    [[ -z "$ONLY" || "$ONLY" == "$1" ]]
}

# Keep only files that still exist as regular files. The pre-commit hook passes
# the ACM (added/copied/modified) set, so deletions never reach here, but a
# caller could pass a stale path; skip it rather than error.
declare -a EXISTING=()
for f in "${FILES[@]}"; do
    [[ -f "$f" ]] && EXISTING+=("$f")
done
FILES=("${EXISTING[@]}")
[[ ${#FILES[@]} -eq 0 ]] && exit 0

# ---------------------------------------------------------------------------
# Bucket the files by the tool that handles them.
#
# TEXT excludes binary files (a file with a NUL byte in its first 8 KiB). The
# trailing-whitespace and end-of-file fixers open files in binary mode and
# would corrupt a real binary; pre-commit avoids this with `types: [text]`, and
# TEXT reproduces that filter. The read-only/size-only checks are safe on
# binaries and run over the whole set.
# ---------------------------------------------------------------------------
is_binary() {
    # True if the first 8 KiB contains a NUL byte. tr -d strips NULs; if that
    # shortens the sample, a NUL was present.
    local sample stripped
    sample=$(head -c 8192 -- "$1" | wc -c)
    stripped=$(head -c 8192 -- "$1" | tr -d '\0' | wc -c)
    [[ "$sample" -ne "$stripped" ]]
}

declare -a TEXT=() PY=() MD=() KT=() YAML=() TOML=()
for f in "${FILES[@]}"; do
    is_binary "$f" || TEXT+=("$f")
    case "$f" in
        *.py) PY+=("$f") ;;
        *.md) MD+=("$f") ;;
        *.kt | *.kts) KT+=("$f") ;;
        *.yaml | *.yml) YAML+=("$f") ;;
        *.toml) TOML+=("$f") ;;
    esac
done

# ---------------------------------------------------------------------------
# Run each tool, resolved by explicit path. status becomes 1 if any tool fails,
# but every selected tool still runs so a single pass applies all available
# fixes (fix mode) or reports every violation (check mode).
# ---------------------------------------------------------------------------
status=0
run() {
    "$@" || status=1
}

# check-added-large-files intersects its file-list argument with the staged
# index unless --enforce-all is passed. Over a whole-tree (--all) run nothing is
# staged, so without the flag it would check nothing; pass it there. On the
# file-list (hook) path it must be omitted, or the check would flag pre-existing
# large files the commit is not actually adding.
declare -a LARGE_FILES_ARGS=()
[[ $ALL -eq 1 ]] && LARGE_FILES_ARGS+=(--enforce-all)

# hygiene family: the six generic pre-commit-hooks checks. These behave
# identically in fix and check mode -- the read-only checks never write, and the
# two whitespace fixers have no check-only mode, so they run as usual and fail
# the run via their exit status if they change anything.
if want hygiene; then
    if [[ ${#TEXT[@]} -gt 0 ]]; then
        run "$LINT_BIN_DIR/trailing-whitespace-fixer" "${TEXT[@]}"
        run "$LINT_BIN_DIR/end-of-file-fixer" "${TEXT[@]}"
    fi
    run "$LINT_BIN_DIR/check-merge-conflict" "${FILES[@]}"
    run "$LINT_BIN_DIR/check-added-large-files" "${LARGE_FILES_ARGS[@]}" "${FILES[@]}"
    [[ ${#YAML[@]} -gt 0 ]] && run "$LINT_BIN_DIR/check-yaml" "${YAML[@]}"
    [[ ${#TOML[@]} -gt 0 ]] && run "$LINT_BIN_DIR/check-toml" "${TOML[@]}"
fi

# python family: lint + format Python.
if want python && [[ ${#PY[@]} -gt 0 ]]; then
    if [[ $CHECK -eq 1 ]]; then
        run "$LINT_BIN_DIR/ruff" check "${PY[@]}"
        run "$LINT_BIN_DIR/ruff" format --check "${PY[@]}"
    else
        run "$LINT_BIN_DIR/ruff" check --fix "${PY[@]}"
        run "$LINT_BIN_DIR/ruff" format "${PY[@]}"
    fi
fi

# markdown family: format Markdown.
# --wrap keep preserves this repo's one-sentence-per-line prose (see
# .claude/rules/prose-style.md); --number keeps ordered-list numbering
# sequential. These match the retired .pre-commit-config.yaml mdformat args.
#
# mdformat does not enforce MD040 (fenced code blocks must name a language);
# markdownlint-cli2 did, until issue #688 replaced it with mdformat. Run
# check_md040.py alongside mdformat, in both fix and check mode, so a new
# offender is still caught even though there is nothing to auto-fix (issue
# #689). It is a first-party script, not a registry-installed tool, so it runs
# under the ambient python3 rather than through $LINT_BIN_DIR.
if want markdown && [[ ${#MD[@]} -gt 0 ]]; then
    if [[ $CHECK -eq 1 ]]; then
        run "$LINT_BIN_DIR/mdformat" --check --wrap keep --number "${MD[@]}"
    else
        run "$LINT_BIN_DIR/mdformat" --wrap keep --number "${MD[@]}"
    fi
    run python3 "$SCRIPT_DIR/check_md040.py" "${MD[@]}"
fi

# kotlin family: format Kotlin.
if want kotlin && [[ ${#KT[@]} -gt 0 ]]; then
    if [[ $CHECK -eq 1 ]]; then
        run "$LINT_BIN_DIR/ktlint" "${KT[@]}"
    else
        run "$LINT_BIN_DIR/ktlint" --format "${KT[@]}"
    fi
fi

exit "$status"
