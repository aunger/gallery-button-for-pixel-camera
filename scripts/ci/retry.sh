#!/usr/bin/env bash
# retry.sh: run a command, and run it again while it fails, up to a fixed
# number of attempts.
#
# Usage:
#   scripts/ci/retry.sh <attempts> <delay-seconds> <command> [<arg>...]
#     attempts       how many times to run the command at most (1 or more)
#     delay-seconds  how long to sleep between attempts (0 or more)
#   Both are decimal integers written without leading zeros.
#
# Runs <command> with its arguments unchanged until it exits 0 or has run
# <attempts> times. Exits 0 on the first success; otherwise exits with the
# status of the last attempt. A failure that will be retried is reported as a
# GitHub Actions ::warning, so it stays visible on a run that ends green; the
# last one is reported as ::error. Exits 2, running nothing, on a usage error.

set -uo pipefail

usage() {
  echo "usage: $0 <attempts> <delay-seconds> <command> [<arg>...]" >&2
  exit 2
}

[[ $# -ge 3 ]] || usage
attempts="$1"
delay="$2"
shift 2
# No leading zeros: bash arithmetic would read `010` as octal 8.
[[ "$attempts" =~ ^[1-9][0-9]*$ ]] || usage
[[ "$delay" =~ ^(0|[1-9][0-9]*)$ ]] || usage

for ((attempt = 1; ; attempt++)); do
  "$@"
  status=$?
  if [[ $status -eq 0 ]]; then
    exit 0
  fi
  if [[ $attempt -ge $attempts ]]; then
    echo "::error::$1 failed with exit $status on attempt $attempt of $attempts; giving up" >&2
    exit "$status"
  fi
  echo "::warning::$1 failed with exit $status on attempt $attempt of $attempts; retrying in ${delay}s" >&2
  sleep "$delay"
done
