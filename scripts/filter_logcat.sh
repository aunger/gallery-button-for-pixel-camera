#!/usr/bin/env bash
# filter_logcat.sh — Filter logcat lines to elide base64-encoded binary blobs.
#
# Reads logcat lines from stdin and writes filtered lines to stdout.
# Any base64 data URI payload (the content after ";base64,") is replaced with
# "[elided]" so that the surrounding context (key name, closing delimiter) is
# preserved while the potentially very long blob is suppressed.
#
# Usage:
#   adb logcat -d | scripts/filter_logcat.sh
#   adb logcat -d | scripts/filter_logcat.sh | grep ... | tail -300
#
# Examples of elided patterns:
#   data:image/jpeg;base64,/9j/4AAQ...    →  data:image/jpeg;base64,[elided]
#   bitmap_url=data:image/png;base64,ABC= →  bitmap_url=data:image/png;base64,[elided]

sed 's/;base64,[A-Za-z0-9+/=]\{8,\}/;base64,[elided]/g'
