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
#   1. Dismisses the ANR dialog immediately if it appears.
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

  # ── Poll loop ────────────────────────────────────────────────────────────────
  POLL_INTERVAL=3
  TIMEOUT=30
  elapsed=0
  idle_count=0

  while [[ $elapsed -lt $TIMEOUT ]]; do
    # 1. Check for the ANR dialog.
    window_dump="$("$ADB" shell dumpsys window windows 2>/dev/null || true)"
    if echo "$window_dump" | grep -q "AppNotRespondingDialog"; then
      echo "[dismiss_anr] ANR dialog detected — sending KEYCODE_BACK to dismiss." >&2
      "$ADB" shell input keyevent KEYCODE_BACK 2>/dev/null || true
      idle_count=0
      sleep "$POLL_INTERVAL"
      elapsed=$((elapsed + POLL_INTERVAL))
      continue
    fi

    # 2. Check Pixel Launcher CPU usage.
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
      # Before exiting, confirm no ANR dialog is still on screen.
      # The dialog does not consume CPU once rendered, so CPU idleness alone is
      # not a reliable proxy for "no ANR dialog present."
      window_dump2="$("$ADB" shell dumpsys window windows 2>/dev/null || true)"
      if echo "$window_dump2" | grep -q "AppNotRespondingDialog"; then
        echo "[dismiss_anr] ANR dialog present despite Launcher being idle — dismissing." >&2
        "$ADB" shell input keyevent KEYCODE_BACK 2>/dev/null || true
        idle_count=0
        sleep 1
      else
        echo "[dismiss_anr] Pixel Launcher has settled (idle_count=$idle_count) — exiting." >&2
        exit 0
      fi
    fi

    sleep "$POLL_INTERVAL"
    elapsed=$((elapsed + POLL_INTERVAL))
  done

  echo "[dismiss_anr] Timeout after ${TIMEOUT}s — proceeding anyway." >&2
  exit 0
) || true
