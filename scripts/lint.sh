#!/usr/bin/env bash
# scripts/lint.sh -- run this repo's linters and formatters over a set of files.
#
# Usage:
#   scripts/lint.sh FILE [FILE ...]   lint the named files
#   scripts/lint.sh --all             lint every tracked file in the tree
#
# This is the single source of truth for "run the linters". The git pre-commit
# hook (scripts/git-hooks/pre-commit) calls it with the staged file set; a
# future CI job could call it with --all and reuse the exact same invocations.
#
# Each tool is resolved by explicit path under $LINT_BIN_DIR (default
# $HOME/.local/bin), where .claude/hooks/session-start.sh installs it at
# session start from a trusted package registry (PyPI or Maven Central). No
# hook repository is git-cloned and nothing is fetched from GitHub Releases at
# run time (issue #667). Point $LINT_BIN_DIR elsewhere to run against tools
# installed in a different location (the test harness does this).
#
# The formatters run as auto-fixers where they support it (ruff --fix, ruff
# format, mdformat, ktlint --format) and rewrite files in place. lint.sh exits
# non-zero if any tool reports a problem (an unfixable violation, or a fixer
# that had to change a file). Detecting which staged files a fixer touched, so
# the commit can be aborted with a re-stage message, is the caller's job; see
# scripts/git-hooks/pre-commit.

set -uo pipefail

LINT_BIN_DIR="${LINT_BIN_DIR:-$HOME/.local/bin}"

usage() {
    echo "usage: lint.sh FILE [FILE ...] | --all" >&2
    exit 2
}

# ---------------------------------------------------------------------------
# Resolve the target file list.
# ---------------------------------------------------------------------------
declare -a FILES=()
if [[ "${1:-}" == "--all" ]]; then
    [[ $# -eq 1 ]] || usage
    mapfile -d '' -t FILES < <(git ls-files -z)
elif [[ $# -ge 1 ]]; then
    FILES=("$@")
else
    usage
fi

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
# but every tool still runs so a single pass applies all available fixes.
# ---------------------------------------------------------------------------
status=0
run() {
    "$@" || status=1
}

if [[ ${#TEXT[@]} -gt 0 ]]; then
    run "$LINT_BIN_DIR/trailing-whitespace-fixer" "${TEXT[@]}"
    run "$LINT_BIN_DIR/end-of-file-fixer" "${TEXT[@]}"
fi
run "$LINT_BIN_DIR/check-merge-conflict" "${FILES[@]}"
run "$LINT_BIN_DIR/check-added-large-files" "${FILES[@]}"
[[ ${#YAML[@]} -gt 0 ]] && run "$LINT_BIN_DIR/check-yaml" "${YAML[@]}"
[[ ${#TOML[@]} -gt 0 ]] && run "$LINT_BIN_DIR/check-toml" "${TOML[@]}"

if [[ ${#PY[@]} -gt 0 ]]; then
    run "$LINT_BIN_DIR/ruff" check --fix "${PY[@]}"
    run "$LINT_BIN_DIR/ruff" format "${PY[@]}"
fi

# --wrap keep preserves this repo's one-sentence-per-line prose (see
# .claude/rules/prose-style.md); --number keeps ordered-list numbering
# sequential. These match the retired .pre-commit-config.yaml mdformat args.
[[ ${#MD[@]} -gt 0 ]] && run "$LINT_BIN_DIR/mdformat" --wrap keep --number "${MD[@]}"

[[ ${#KT[@]} -gt 0 ]] && run "$LINT_BIN_DIR/ktlint" --format "${KT[@]}"

exit "$status"
