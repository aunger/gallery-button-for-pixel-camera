#!/usr/bin/env bash
# filter_logcat.sh: Filter logcat lines for CI failure diagnostics.
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
#      (com.gb4pc, OverlayService, MockCamera, CameraService), the E2E
#      harness diagnostic tag (GB4PC_E2E, emitted by
#      E2EFixture.launchPixelCamera() to make the issue #233 bounded-relaunch
#      path observable), and any logcat error tag (E/<tag>).
#
# Usage:
#   adb logcat -d | scripts/filter_logcat.sh
#   SINCE=$(adb shell date +'%m-%d %H:%M:%S.000') && adb logcat -d -T "$SINCE" | scripts/filter_logcat.sh
#
# Examples of elided patterns:
#   data:image/jpeg;base64,/9j/4AAQ...    →  data:image/jpeg;base64,[elided]
#   bitmap_url=data:image/png;base64,ABC= →  bitmap_url=data:image/png;base64,[elided]

sed 's/;base64,[A-Za-z0-9+/=]\{8,\}/;base64,[elided]/g' | \
  grep -E "AndroidRuntime|FATAL|Process crashed|com\.gb4pc|OverlayService|MockCamera|CameraService|GB4PC_E2E|E/\w" || true
