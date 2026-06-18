#!/usr/bin/env bash
# detect_launch_retry.sh — Surface the issue #233 launch-retry signal from a run's
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
#   - emits a GitHub Actions ::warning annotation when run under GITHUB_ACTIONS so
#     the signal is loud in the run summary;
#   - exits 10 when the retry signal is present (the #233 race recurred this run),
#     0 when it is absent (attempt-1 success only, the steady state), and
#     1 on usage error.
#
# Exit 10 (signal found) is deliberately NOT a hard failure of the surrounding step:
# callers run it informationally (`|| true` / continue-on-error). The point is to
# make the long-awaited run self-evident, not to fail an otherwise-green build.
#
# Usage:
#   scripts/detect_launch_retry.sh results/e2e-overlay-logcat.txt
#   adb logcat -d | scripts/filter_logcat.sh | scripts/detect_launch_retry.sh

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
    # Collapse to a single line for the annotation body.
    summary="$(echo "$MATCHES" | tr '\n' ' ')"
    echo "::warning title=issue #233 launch retry observed::$summary"
  fi
  exit 10
fi

echo "DETECT_LAUNCH_RETRY: no launch-retry signal (attempt-1 success only); issue #364 watch item still awaits a triggering run."
exit 0
