#!/usr/bin/env bash
# update_gh_labels.sh: add and/or remove GitHub issue/PR labels as deltas via the REST API.
#
# Background: the only tool agents have for changing labels on GitHub Actions
# for Web is `mcp__github__issue_write`, whose `labels` field is a *replacement
# set*, not a delta. An agent that reads the current labels, computes the new
# set locally, and writes it back races anyone else who touches labels in the
# meantime (another agent, a workflow, or a human), silently discarding their
# change. See issue #710.
#
# This script instead calls GitHub's native delta label endpoints, so it can
# add and remove specific labels without ever reading or replacing the whole
# set:
#   - POST   /issues/{n}/labels          adds labels, leaving existing ones alone
#   - DELETE /issues/{n}/labels/{name}   removes one label, leaving others alone
#
# Follows the pattern of scripts/delete_gh_comment.sh (issue #658): a plain
# REST call via curl, for environments with no `gh` CLI.
#
# Usage:
#   scripts/update_gh_labels.sh <owner> <repo> <issue_or_pr_number> [--add LABEL]... [--remove LABEL]...
#
# At least one --add or --remove is required. A label must not appear in both.
# Removing a label that is not currently on the issue/PR is treated as
# success (the goal state, "label absent," is already met), matching the
# idempotent behavior of scripts/enforce_mutually_exclusive_labels.py.
#
# Example (the dev_orchestration.md "Remove: orchestrate / Add: orchestrating"
# transition, in one delta call instead of a read-then-replace):
#   scripts/update_gh_labels.sh aunger gallery-button-for-pixel-camera 710 \
#     --remove orchestrate --add orchestrating
#
# Required environment variables:
#   GITHUB_TOKEN   Token with permission to modify labels on the issue/PR
#                  (repo scope, and write access to the repo)

set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  grep '^#' "$0" | sed 's/^# \{0,1\}//'
  exit 1
fi

if [[ $# -lt 3 ]]; then
  echo "Usage: $0 <owner> <repo> <issue_or_pr_number> [--add LABEL]... [--remove LABEL]..." >&2
  exit 1
fi

OWNER="$1"
REPO="$2"
ISSUE_NUMBER="$3"
shift 3

if ! [[ "$ISSUE_NUMBER" =~ ^[0-9]+$ ]]; then
  echo "Error: issue_or_pr_number must be numeric, got '$ISSUE_NUMBER'." >&2
  exit 1
fi

ADD_LABELS=()
REMOVE_LABELS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --add)
      if [[ $# -lt 2 ]]; then
        echo "Error: --add requires a label name." >&2
        exit 1
      fi
      ADD_LABELS+=("$2")
      shift 2
      ;;
    --remove)
      if [[ $# -lt 2 ]]; then
        echo "Error: --remove requires a label name." >&2
        exit 1
      fi
      REMOVE_LABELS+=("$2")
      shift 2
      ;;
    *)
      echo "Error: unrecognized argument '$1'." >&2
      exit 1
      ;;
  esac
done

if [[ ${#ADD_LABELS[@]} -eq 0 && ${#REMOVE_LABELS[@]} -eq 0 ]]; then
  echo "Error: at least one --add or --remove is required." >&2
  exit 1
fi

for add_label in "${ADD_LABELS[@]}"; do
  for remove_label in "${REMOVE_LABELS[@]}"; do
    if [[ "$add_label" == "$remove_label" ]]; then
      echo "Error: label '$add_label' cannot be both added and removed in the same call." >&2
      exit 1
    fi
  done
done

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "Error: GITHUB_TOKEN env var is not set." >&2
  exit 1
fi

EXIT_CODE=0

# ── Remove labels first (one DELETE call per label; a 404 means it was
#    already absent, which satisfies the goal state, so it does not count as
#    a failure) ──
for label in "${REMOVE_LABELS[@]}"; do
  ENCODED_LABEL="$(printf '%s' "$label" | jq -Rr @uri)"
  URL="https://api.github.com/repos/${OWNER}/${REPO}/issues/${ISSUE_NUMBER}/labels/${ENCODED_LABEL}"

  BODY_FILE="$(mktemp)"
  HTTP_STATUS="$(curl -sS -o "$BODY_FILE" -w '%{http_code}' \
    -X DELETE \
    -H "Authorization: Bearer ${GITHUB_TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "$URL")"

  case "$HTTP_STATUS" in
    200)
      echo "Removed label '${label}' from ${OWNER}/${REPO}#${ISSUE_NUMBER}."
      ;;
    404)
      echo "Label '${label}' already absent from ${OWNER}/${REPO}#${ISSUE_NUMBER}."
      ;;
    403)
      echo "Error: forbidden -- token lacks permission to remove label '${label}'." >&2
      if [[ -s "$BODY_FILE" ]]; then cat "$BODY_FILE" >&2; fi
      EXIT_CODE=1
      ;;
    401)
      echo "Error: unauthorized -- check that GITHUB_TOKEN is valid." >&2
      EXIT_CODE=1
      ;;
    *)
      echo "Error: unexpected HTTP status ${HTTP_STATUS} removing label '${label}'." >&2
      if [[ -s "$BODY_FILE" ]]; then cat "$BODY_FILE" >&2; fi
      EXIT_CODE=1
      ;;
  esac
  rm -f "$BODY_FILE"
done

# ── Add labels second, in a single POST call for all of them: this endpoint
#    adds to the existing label set, it never replaces it ──
if [[ ${#ADD_LABELS[@]} -gt 0 ]]; then
  URL="https://api.github.com/repos/${OWNER}/${REPO}/issues/${ISSUE_NUMBER}/labels"
  LABELS_JSON="$(jq -nc --args '{"labels": $ARGS.positional}' "${ADD_LABELS[@]}")"

  BODY_FILE="$(mktemp)"
  HTTP_STATUS="$(curl -sS -o "$BODY_FILE" -w '%{http_code}' \
    -X POST \
    -H "Authorization: Bearer ${GITHUB_TOKEN}" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    -H "Content-Type: application/json" \
    -d "$LABELS_JSON" \
    "$URL")"

  case "$HTTP_STATUS" in
    200)
      echo "Added label(s) [${ADD_LABELS[*]}] to ${OWNER}/${REPO}#${ISSUE_NUMBER}."
      ;;
    404)
      echo "Error: ${OWNER}/${REPO}#${ISSUE_NUMBER} not found." >&2
      EXIT_CODE=1
      ;;
    403)
      echo "Error: forbidden -- token lacks permission to add labels." >&2
      if [[ -s "$BODY_FILE" ]]; then cat "$BODY_FILE" >&2; fi
      EXIT_CODE=1
      ;;
    401)
      echo "Error: unauthorized -- check that GITHUB_TOKEN is valid." >&2
      EXIT_CODE=1
      ;;
    *)
      echo "Error: unexpected HTTP status ${HTTP_STATUS} adding labels." >&2
      if [[ -s "$BODY_FILE" ]]; then cat "$BODY_FILE" >&2; fi
      EXIT_CODE=1
      ;;
  esac
  rm -f "$BODY_FILE"
fi

exit $EXIT_CODE
