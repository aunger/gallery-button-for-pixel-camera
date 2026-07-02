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
  grep '^#' "$0" | sed 's/^# \{0,1\}//'
  exit 1
fi

BASE_REF="$1"
HEAD_REF="${2:-HEAD}"

MERGE_BASE="$(git merge-base "$BASE_REF" "$HEAD_REF" 2>/dev/null)"
if [[ -z "$MERGE_BASE" ]]; then
  echo "::warning::Could not find a merge base with $BASE_REF (its history may have been rewritten); running the full build as a safe default." >&2
  echo "true"
  exit 0
fi

CHANGED="$(git diff --name-only "$MERGE_BASE" "$HEAD_REF" | grep -v '\.md$' || true)"
if [[ -n "$CHANGED" ]]; then
  echo "true"
else
  echo "false"
fi
