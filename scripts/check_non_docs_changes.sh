#!/usr/bin/env bash
# check_non_docs_changes.sh -- decide whether CI's full build/test phase is
# needed for a pull request, based on whether it touches anything besides
# Markdown files.
#
# Background: this used to be inlined in .github/workflows/build.yml as
#   CHANGED=$(git diff --name-only "origin/$BASE_REF"...HEAD | grep -v '\.md$' || true)
# The triple-dot diff requires a merge base between the base ref and HEAD.
# When the base branch's history has been rewritten out from under a PR (a
# force-push or a rebase server-side), no merge base exists and `git diff`
# fails with "no merge base" and prints nothing to stdout. The trailing
# `|| true` swallowed that failure, leaving $CHANGED empty -- indistinguishable
# from "no changes" -- so the full test suite was silently skipped even though
# the diff was simply undeterminable, not empty. Observed live in CI run
# https://github.com/aunger/gallery-button-for-pixel-camera/actions/runs/27517745981/job/84790634265.
#
# This script fixes that by finding the merge base explicitly first, and
# failing safe (printing "true") when one cannot be found, rather than
# silently treating "unknown" as "no changes".
#
# Usage:
#   scripts/check_non_docs_changes.sh <base-ref> [<head-ref>]
#     base-ref  a ref/commit already available locally (e.g. origin/main)
#     head-ref  defaults to HEAD
#
# Prints "true" or "false" to stdout. Always exits 0.

set -uo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || -z "${1:-}" ]]; then
  # Skip line 1 (the shebang): it also matches '^#' but isn't part of the
  # usage text, and the sed below only strips a bare '#', not '#!'.
  tail -n +2 "$0" | grep '^#' | sed 's/^# \{0,1\}//'
  exit 1
fi

BASE_REF="$1"
HEAD_REF="${2:-HEAD}"

MERGE_BASE_ERR_FILE="$(mktemp)"
MERGE_BASE="$(git merge-base "$BASE_REF" "$HEAD_REF" 2>"$MERGE_BASE_ERR_FILE")"
MERGE_BASE_ERR="$(cat "$MERGE_BASE_ERR_FILE")"
rm -f "$MERGE_BASE_ERR_FILE"
if [[ -z "$MERGE_BASE" ]]; then
  echo "::warning::Could not find a merge base between $BASE_REF and $HEAD_REF (${MERGE_BASE_ERR:-no diagnostic output from git}); running the full build as a safe default." >&2
  echo "true"
  exit 0
fi

DIFF_OUTPUT="$(git diff --name-only "$MERGE_BASE" "$HEAD_REF")"
DIFF_RC=$?
if [[ $DIFF_RC -ne 0 ]]; then
  echo "::warning::git diff between $MERGE_BASE and $HEAD_REF failed unexpectedly (exit $DIFF_RC); running the full build as a safe default." >&2
  echo "true"
  exit 0
fi

CHANGED="$(printf '%s\n' "$DIFF_OUTPUT" | grep -v '\.md$' || true)"
if [[ -n "$CHANGED" ]]; then
  echo "true"
else
  echo "false"
fi
