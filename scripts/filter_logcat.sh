#!/usr/bin/env bash
# filter_logcat.sh — Filter logcat lines for CI failure diagnostics.
#
# Reads logcat lines from stdin and writes filtered lines to stdout.
#
# Two transformations are applied in order:
#   1. Any base64 data URI payload (the content after ";base64,") is replaced
#      with "[elided]" so that the surrounding context (key name, closing
#      delimiter) is preserved while the potentially very long blob is
#      suppressed.
#   2. Lines are filtered to only those relevant for CI failure diagnosis:
#      AndroidRuntime, FATAL, "Process crashed", app/service tags
#      (com.gb4pc, OverlayService, MockCamera, CameraService), and any
#      logcat error tag (E/<tag>).
#
# Usage:
#   adb logcat -d | scripts/filter_logcat.sh
#   adb logcat -d | scripts/filter_logcat.sh | tail -300
#
# Examples of elided patterns:
#   data:image/jpeg;base64,/9j/4AAQ...    →  data:image/jpeg;base64,[elided]
#   bitmap_url=data:image/png;base64,ABC= →  bitmap_url=data:image/png;base64,[elided]

sed 's/;base64,[A-Za-z0-9+/=]\{8,\}/;base64,[elided]/g' | \
  grep -E "AndroidRuntime|FATAL|Process crashed|com\.gb4pc|OverlayService|MockCamera|CameraService|E/\w" || true
