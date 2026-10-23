#!/usr/bin/env bash
# dump_logcat.sh: Print the device's logcat since a given time, filtered for CI diagnostics.
#
# Runs `adb logcat -d -T <since>` through filter_logcat.sh and writes the result
# to stdout and, when <out-file> is given, to that file as well.
#
# Usage:
#   scripts/ci/test-support/dump_logcat.sh <adb> <since> [<out-file>]
#
# Arguments:
#   <adb>        Path to the adb binary.
#   <since>      Device-side start time, in the form `adb logcat -T` takes
#                (for example `10-09 02:10:21.000`).
#   <out-file>   Optional file to write the filtered dump to, besides stdout.

set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "usage: $0 <adb> <since> [<out-file>]" >&2
  exit 2
fi

ADB="$1"
SINCE="$2"
OUT="${3:-/dev/null}"

"$ADB" logcat -d -T "$SINCE" | bash "$SCRIPT_DIR/filter_logcat.sh" | tee "$OUT"
