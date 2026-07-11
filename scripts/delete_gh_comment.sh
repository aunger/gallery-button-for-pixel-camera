#!/usr/bin/env bash
# delete_gh_comment.sh: delete a single GitHub issue/PR comment via the REST API.
#
# Background: cleaning up auto-filed test-failure comments (e.g. the throwaway
# CI-artifact-verification comments left on issues #657, #656, #581, #571,
# #241, and #233 by PR #654) has no dedicated GitHub MCP tool, and no `gh` CLI
# is available in some environments this repo's agents run in. This script
# fills that gap with a plain REST call.
#
# Deletes ISSUE comments (the /issues/comments/{id} endpoint), which also
# covers PR conversation comments (PRs are issues under the hood). It does
# NOT delete PR review comments (inline diff comments); those live under
# /pulls/comments/{id} instead.
#
# Usage:
#   scripts/delete_gh_comment.sh <owner> <repo> <comment_id>
#
# Example:
#   scripts/delete_gh_comment.sh aunger gallery-button-for-pixel-camera 4940940270
#
# Required environment variables:
#   GITHUB_TOKEN   Token with permission to delete the comment (repo scope,
#                  and either you authored the comment or you have
#                  write/admin access to the repo)

set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  grep '^#' "$0" | sed 's/^# \{0,1\}//'
  exit 1
fi

if [[ $# -ne 3 ]]; then
  echo "Usage: $0 <owner> <repo> <comment_id>" >&2
  exit 1
fi

OWNER="$1"
REPO="$2"
COMMENT_ID="$3"

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "Error: GITHUB_TOKEN env var is not set." >&2
  exit 1
fi

if ! [[ "$COMMENT_ID" =~ ^[0-9]+$ ]]; then
  echo "Error: comment_id must be numeric, got '$COMMENT_ID'." >&2
  exit 1
fi

URL="https://api.github.com/repos/${OWNER}/${REPO}/issues/comments/${COMMENT_ID}"

BODY_FILE="$(mktemp)"
trap 'rm -f "$BODY_FILE"' EXIT

HTTP_STATUS="$(curl -sS -o "$BODY_FILE" -w '%{http_code}' \
  -X DELETE \
  -H "Authorization: Bearer ${GITHUB_TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "$URL")"

case "$HTTP_STATUS" in
  204)
    echo "Deleted comment ${COMMENT_ID} from ${OWNER}/${REPO}."
    ;;
  404)
    echo "Error: comment ${COMMENT_ID} not found (already deleted, or wrong owner/repo/id)." >&2
    exit 1
    ;;
  403)
    echo "Error: forbidden -- token lacks permission to delete comment ${COMMENT_ID}." >&2
    [[ -s "$BODY_FILE" ]] && cat "$BODY_FILE" >&2
    exit 1
    ;;
  401)
    echo "Error: unauthorized -- check that GITHUB_TOKEN is valid." >&2
    exit 1
    ;;
  *)
    echo "Error: unexpected HTTP status ${HTTP_STATUS} deleting comment ${COMMENT_ID}." >&2
    [[ -s "$BODY_FILE" ]] && cat "$BODY_FILE" >&2
    exit 1
    ;;
esac
