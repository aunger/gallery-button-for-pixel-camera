#!/usr/bin/env bash
# dismiss_anr.sh — Best-effort ANR watcher for CI pre-flight.
#
# After `adb install -r` the Pixel Launcher receives ACTION_PACKAGE_REPLACED and
# reconciles its icon grid. On slow CI emulators this can saturate the Launcher's
# main thread long enough to hit the broadcast ANR timeout (~10 s), producing a
# modal "Pixel Launcher isn't responding" dialog that overlays MockCameraActivity
# and causes the green-feed check to fail (Issue #194).
#
# This script runs in the background during the install → green-feed window and:
#   1. Dismisses the ANR dialog if it appears (detected via logcat, not polling).
#   2. Waits for Pixel Launcher CPU usage to fall below 5% (two consecutive
#      readings) before exiting, so the caller knows the system has settled.
#   3. Always exits 0 — the green-feed check is the real correctness gate.
#
# Usage:
#   scripts/dismiss_anr.sh [--adb <path>]
#
# Arguments:
#   --adb <path>   Path to the adb binary. Defaults to
#                  $ANDROID_HOME/platform-tools/adb.
#
# Environment:
#   SLEEP_AFTER_ANR_DETECTED   Seconds to wait after logcat fires before sending
#                              KEYCODE_BACK (default: 7). Override in tests.

# Run the real logic in a subshell with strict error handling.
# The outer script catches any failure from the subshell and still exits 0.
(
  set -euo pipefail

  # ── Resolve adb ─────────────────────────────────────────────────────────────
  ADB=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --adb)
        ADB="$2"
        shift 2
        ;;
      *)
        shift
        ;;
    esac
  done

  if [[ -z "$ADB" ]]; then
    ADB="${ANDROID_HOME:-}/platform-tools/adb"
  fi

  # ── Phase 1: logcat trigger ──────────────────────────────────────────────────
  # Clear the logcat buffer and start a background stream that sets a flag file
  # the moment an ANR for Pixel Launcher appears in the log.  This costs nothing
  # while no ANR is occurring and fires well before the dialog is rendered.
  "$ADB" logcat -c 2>/dev/null || true

  ANR_FLAG="$(mktemp)"
  rm -f "$ANR_FLAG"

  ( "$ADB" logcat ActivityManager:E '*:S' 2>/dev/null \
      | grep -m1 'ANR in com.google.android.apps.nexuslauncher' > /dev/null \
    && touch "$ANR_FLAG" ) &
  LOGCAT_PID=$!

  trap 'kill "$LOGCAT_PID" 2>/dev/null || true; rm -f "$ANR_FLAG"' EXIT

  # ── Poll loop ────────────────────────────────────────────────────────────────
  POLL_INTERVAL=3
  TIMEOUT=30
  # How long to wait after logcat fires before sending KEYCODE_BACK.
  # Override via environment for tests.
  SLEEP_AFTER_ANR_DETECTED="${SLEEP_AFTER_ANR_DETECTED:-7}"
  elapsed=0
  idle_count=0

  while [[ $elapsed -lt $TIMEOUT ]]; do
    # Phase 2: if the logcat background job flagged an ANR, dismiss it.
    if [ -f "$ANR_FLAG" ]; then
      rm -f "$ANR_FLAG"
      echo "[dismiss_anr] ANR detected via logcat — waiting ${SLEEP_AFTER_ANR_DETECTED}s for dialog to render." >&2
      sleep "$SLEEP_AFTER_ANR_DETECTED"
      echo "[dismiss_anr] Sending KEYCODE_BACK to dismiss ANR dialog." >&2
      "$ADB" shell input keyevent KEYCODE_BACK 2>/dev/null || true
      idle_count=0
      # Fall through to the CPU check on this same iteration.
    fi

    # Phase 3: check Pixel Launcher CPU usage.
    cpu_dump="$("$ADB" shell dumpsys cpuinfo 2>/dev/null || true)"
    launcher_line="$(echo "$cpu_dump" | grep -i "nexuslauncher" | head -1 || true)"

    if [[ -z "$launcher_line" ]]; then
      # Launcher process not present — treat as idle.
      idle_count=$((idle_count + 1))
    else
      # Extract the leading integer percentage (e.g. "  12% com.google.android.apps...")
      pct="$(echo "$launcher_line" | grep -oE '^[[:space:]]*[0-9]+' | tr -d ' ' || true)"
      if [[ -z "$pct" ]] || [[ "$pct" -lt 5 ]]; then
        idle_count=$((idle_count + 1))
      else
        idle_count=0
      fi
    fi

    if [[ $idle_count -ge 2 ]]; then
      echo "[dismiss_anr] Pixel Launcher has settled (idle_count=$idle_count) — exiting." >&2
      exit 0
    fi

    sleep "$POLL_INTERVAL"
    elapsed=$((elapsed + POLL_INTERVAL))
  done

  echo "[dismiss_anr] Timeout after ${TIMEOUT}s — proceeding anyway." >&2
  exit 0
) || true
