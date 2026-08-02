#!/usr/bin/env bash
# detect_launch_retry.sh: Surface the issue #233 launch-retry signal from a run's
# overlay logcat, so the issue #364 watch item announces itself instead of relying
# on a human to sweep every CI run by hand.
#
# Background (issue #364): the bounded relaunch in E2EFixture.launchPixelCamera()
# logs `GB4PC_E2E` lines on every run. A *recovered race* or *exhaustion* only ever
# shows up as one of:
#   - "am start attempt 2/3" (or 3/3): a retry was issued, i.e. the first launch was
#     torn down before the camera opened (the #233 race actually recurred), or
#   - "overlay active on attempt 2" (or 3): the retry recovered, or
#   - "overlay still inactive after ... attempts" / "first-launch teardown race":
#     a Log.w retry/exhaustion line.
# The conclusive run #364 is waiting for is the first whose overlay logcat contains
# any of these. Across 26+ green runs since PR #362 merged, none has. This script
# scans the captured logcat for that signal so the run flags itself.
#
# Reads the overlay logcat from the file named by $1 (or stdin if no argument), and:
#   - prints any matching lines, prefixed, to stdout;
#   - when run under GITHUB_ACTIONS with GITHUB_TOKEN set:
#       * emits a ::warning annotation so the signal appears in the run summary, and
#       * opens or reopens issue #364 with a comment linking to this run, so the
#         event is not buried under subsequent builds;
#   - exits 10 when the retry signal is present (the #233 race recurred this run),
#     0 when it is absent (attempt-1 success only, the steady state), and
#     1 on usage error.
#
# Exit 10 (signal found) is deliberately NOT a hard failure of the surrounding step:
# callers run it informationally (|| true / continue-on-error). The point is to
# make the long-awaited run self-evident, not to fail an otherwise-green build.
#
# Required environment variables (only used when GITHUB_ACTIONS=true):
#   GITHUB_TOKEN        Token with issues: write permission
#   GITHUB_REPOSITORY   Owner/repo (e.g. "aunger/gallery-button-for-pixel-camera")
#
# Optional environment variables (populated automatically by GitHub Actions):
#   GITHUB_RUN_ID       Workflow run ID (for linking to the triggering run)
#   GITHUB_SERVER_URL   Default: https://github.com
#
# Usage:
#   scripts/ci/prs-and-issues/detect_launch_retry.sh results/e2e-overlay-logcat.txt
#   adb logcat -d | scripts/ci/test-support/filter_logcat.sh | scripts/ci/prs-and-issues/detect_launch_retry.sh

set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  grep '^#' "$0" | sed 's/^# \{0,1\}//'
  exit 1
fi

# Patterns that only appear when a launch needed more than its first attempt, i.e.
# the #233 teardown race actually recurred this run (attempt 1 success alone never
# matches any of these).
RETRY_PATTERN='am start attempt ([2-9]|[0-9]{2,})/|overlay active on attempt ([2-9]|[0-9]{2,})|overlay still inactive after|first-launch teardown race'

input() {
  if [[ -n "${1:-}" ]]; then
    cat -- "$1"
  else
    cat
  fi
}

MATCHES="$(input "${1:-}" | grep -E "$RETRY_PATTERN" || true)"

if [[ -n "$MATCHES" ]]; then
  echo "DETECT_LAUNCH_RETRY: issue #233 launch-retry signal present (the watch item in issue #364 has a run to inspect):"
  while IFS= read -r line; do
    echo "  $line"
  done <<< "$MATCHES"

  if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
    # Emit a ::warning annotation so the signal appears in the run summary.
    summary="$(echo "$MATCHES" | tr '\n' ' ')"
    echo "::warning title=issue #233 launch retry observed::$summary"

    # Open or reopen issue #364 and post a comment linking to this run, so the
    # event is not buried under subsequent builds. Failures here are non-fatal:
    # the script's primary job is detection; issue-filing is best-effort.
    if [[ -n "${GITHUB_TOKEN:-}" && -n "${GITHUB_REPOSITORY:-}" ]]; then
      RUN_ID="${GITHUB_RUN_ID:-}"
      SERVER="${GITHUB_SERVER_URL:-https://github.com}"
      ISSUE_NUMBER=364
      API="https://api.github.com/repos/${GITHUB_REPOSITORY}/issues/${ISSUE_NUMBER}"

      # Reopen the issue if it is currently closed.
      STATE="$(curl -fsSL \
        -H "Authorization: Bearer ${GITHUB_TOKEN}" \
        -H "Accept: application/vnd.github+json" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        "${API}" | grep -o '"state":"[^"]*"' | head -1 | cut -d'"' -f4 || true)"
      if [[ "$STATE" == "closed" ]]; then
        curl -fsSL -X PATCH \
          -H "Authorization: Bearer ${GITHUB_TOKEN}" \
          -H "Accept: application/vnd.github+json" \
          -H "X-GitHub-Api-Version: 2022-11-28" \
          -d '{"state":"open"}' \
          "${API}" > /dev/null
        echo "DETECT_LAUNCH_RETRY: reopened issue #${ISSUE_NUMBER}."
      fi

      # Post a comment with matching lines and a link to this run.
      # Use printf to produce real newlines (double-quoted strings do not
      # interpret \n as a newline in bash), and jq to produce valid JSON so
      # that logcat content containing quotes, backslashes, or newlines does
      # not break the request body.
      if [[ -n "$RUN_ID" ]]; then
        RUN_LINK="${SERVER}/${GITHUB_REPOSITORY}/actions/runs/${RUN_ID}"
        COMMENT_BODY="$(printf 'The issue #233 launch-retry signal was detected in [run %s](%s).\n\nMatching logcat lines:\n```\n%s\n```\n\nInspect the `e2e-overlay-logcat.txt` artifact on that run for the full context.' \
          "$RUN_ID" "$RUN_LINK" "$MATCHES")"
      else
        COMMENT_BODY="$(printf 'The issue #233 launch-retry signal was detected.\n\nMatching logcat lines:\n```\n%s\n```' \
          "$MATCHES")"
      fi
      COMMENT_JSON="$(jq -n --arg body "$COMMENT_BODY" '{"body": $body}')"
      curl -fsSL -X POST \
        -H "Authorization: Bearer ${GITHUB_TOKEN}" \
        -H "Accept: application/vnd.github+json" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        -d "$COMMENT_JSON" \
        "${API}/comments" > /dev/null
      echo "DETECT_LAUNCH_RETRY: posted comment to issue #${ISSUE_NUMBER}."
    else
      echo "DETECT_LAUNCH_RETRY: GITHUB_TOKEN or GITHUB_REPOSITORY not set; skipping issue update."
    fi
  fi

  exit 10
fi

echo "DETECT_LAUNCH_RETRY: no launch-retry signal (attempt-1 success only); issue #364 watch item still awaits a triggering run."
exit 0
