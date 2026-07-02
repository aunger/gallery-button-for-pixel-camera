#!/usr/bin/env bash
# dismiss_anr.sh: Best-effort ANR watcher for CI pre-flight.
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
#   3. Always exits 0; the green-feed check is the real correctness gate.
#
# Usage:
#   scripts/dismiss_anr.sh [--adb <path>]
#
# Arguments:
#   --adb <path>   Path to the adb binary. Defaults to
#                  $ANDROID_HOME/platform-tools/adb.
#
# Environment:
#   POLL_INTERVAL              Seconds between poll iterations (default: 3).
#                              Override in tests.
#   TIMEOUT                    Seconds before the poll loop gives up and exits
#                              0 anyway (default: 30). Override in tests.
#   SLEEP_AFTER_ANR_DETECTED   Seconds to wait after logcat fires before sending
#                              KEYCODE_ENTER (default: 7). Override in tests.

# Run the real logic in a subshell with strict error handling.
# The outer script catches any failure from the subshell and still exits 0.
(
  set -euo pipefail

  # Timestamp helper---------------------------------------------------------
  _ts() { date '+%H:%M:%S.%3N'; }

  # Resolve adb---------------------------------------------------------------
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

  SCRIPT_START_TS="$(date +%s%3N)"
  echo "[dismiss_anr] $(_ts) Script started (adb=$ADB)." >&2

  # Detect the home/launcher package at startup-------------------------------
  # Try the intent resolution approach first; fall back to pattern matching.
  LAUNCHER_PKG=""
  _resolve_out="$("$ADB" shell cmd package resolve-activity --brief \
      -a android.intent.action.MAIN -c android.intent.category.HOME \
      2>/dev/null | head -1 || true)"
  if [[ -n "$_resolve_out" ]] && [[ "$_resolve_out" == */* ]]; then
    LAUNCHER_PKG="${_resolve_out%%/*}"
  fi

  if [[ -n "$LAUNCHER_PKG" ]]; then
    echo "[dismiss_anr] $(_ts) Launcher detected: $LAUNCHER_PKG" >&2
  else
    echo "[dismiss_anr] $(_ts) Launcher detection failed; using auto-detected via pattern fallback (nexuslauncher|launcher3)." >&2
  fi

  # Phase 1: logcat trigger-----------------------------------------------------
  # Clear the logcat buffer and start a background stream that sets a flag file
  # the moment an ANR for Pixel Launcher appears in the log.  This costs nothing
  # while no ANR is occurring and fires well before the dialog is rendered.
  #
  # Design notes:
  #   • A `while read` loop (not `grep -m1`) keeps the stream alive so a second
  #     ANR that fires after the first is dismissed is still caught.
  #   • `adb logcat` is started as a backgrounded grandchild inside the subshell
  #     and its PID is written to a temp file so the EXIT trap can kill it
  #     directly; killing only the subshell PID would leave the grandchild
  #     running after the script exits.
  "$ADB" logcat -c 2>/dev/null || true

  ANR_FLAG="$(mktemp)"
  rm -f "$ANR_FLAG"

  ADB_LOGCAT_PID_FILE="$(mktemp)"
  LOGCAT_FIFO="$(mktemp -u)"
  mkfifo "$LOGCAT_FIFO"

  # Start adb logcat in the background, feeding a named pipe.
  "$ADB" logcat ActivityManager:E '*:S' 2>/dev/null > "$LOGCAT_FIFO" &
  echo $! > "$ADB_LOGCAT_PID_FILE"
  echo "[dismiss_anr] $(_ts) Logcat stream started (pid=$(cat "$ADB_LOGCAT_PID_FILE"))." >&2

  # Consumer subshell: reads from the fifo and touches ANR_FLAG on every match.
  # Uses the detected LAUNCHER_PKG if available, otherwise falls back to a
  # case-insensitive pattern covering both Pixel and AOSP launchers.
  _LAUNCHER_PKG_LOCAL="$LAUNCHER_PKG"
  ( while IFS= read -r line; do
      case "$line" in
        *'ANR in '*)
          if [[ -n "$_LAUNCHER_PKG_LOCAL" ]]; then
            case "$line" in
              *"ANR in $_LAUNCHER_PKG_LOCAL"*) touch "$ANR_FLAG" ;;
            esac
          else
            # Pattern fallback: match nexuslauncher or launcher3 (case-insensitive).
            if echo "$line" | grep -qiE 'ANR in [^ ]*(nexuslauncher|launcher3)'; then
              touch "$ANR_FLAG"
            fi
          fi
          ;;
      esac
    done < "$LOGCAT_FIFO" ) &
  LOGCAT_PID=$!

  trap '
    _elapsed_ms=$(( $(date +%s%3N) - SCRIPT_START_TS ))
    ADB_LOGCAT_PID="$(cat "$ADB_LOGCAT_PID_FILE" 2>/dev/null || true)"
    kill "$ADB_LOGCAT_PID" 2>/dev/null || true
    kill "$LOGCAT_PID" 2>/dev/null || true
    rm -f "$ANR_FLAG" "$ADB_LOGCAT_PID_FILE" "$LOGCAT_FIFO"
    echo "[dismiss_anr] [$(_ts)] EXIT trap: cleanup complete (elapsed ${_elapsed_ms}ms)." >&2
    echo "[dismiss_anr] [$(_ts)] Last ActivityManager errors:" >&2
    "$ADB" shell logcat -d ActivityManager:E '"'"'*:S'"'"' 2>/dev/null | tail -10 >&2 || true
  ' EXIT

  # Poll loop--------------------------------------------------------------------
  # POLL_INTERVAL, TIMEOUT, and SLEEP_AFTER_ANR_DETECTED are overridable via
  # environment so the unit test suite can compress the real wall-clock sleeps
  # this loop performs (it polls a real mock adb, not a faked clock).
  POLL_INTERVAL="${POLL_INTERVAL:-3}"
  TIMEOUT="${TIMEOUT:-30}"
  # How long to wait after logcat fires before sending KEYCODE_ENTER.
  # Override via environment for tests.
  SLEEP_AFTER_ANR_DETECTED="${SLEEP_AFTER_ANR_DETECTED:-7}"
  elapsed=0
  idle_count=0
  poll_n=0

  while [[ $elapsed -lt $TIMEOUT ]]; do
    poll_n=$((poll_n + 1))

    # Phase 2: if the logcat background job flagged an ANR, dismiss it.
    if [ -f "$ANR_FLAG" ]; then
      rm -f "$ANR_FLAG"
      echo "[dismiss_anr] $(_ts) Logcat ANR trigger fired; waiting ${SLEEP_AFTER_ANR_DETECTED}s for dialog to render." >&2
      sleep "$SLEEP_AFTER_ANR_DETECTED"
      echo "[dismiss_anr] $(_ts) Sending KEYCODE_ENTER to dismiss ANR dialog." >&2
      "$ADB" shell input keyevent KEYCODE_ENTER 2>/dev/null || true
      idle_count=0
      # Fall through to the CPU check on this same iteration.
    fi

    # Phase 3: check home launcher CPU usage.
    cpu_dump="$("$ADB" shell dumpsys cpuinfo 2>/dev/null || true)"
    if [[ -n "$LAUNCHER_PKG" ]]; then
      launcher_line="$(echo "$cpu_dump" | grep -iF "$LAUNCHER_PKG" | head -1 || true)"
    else
      launcher_line="$(echo "$cpu_dump" | grep -iE "nexuslauncher|launcher3" | head -1 || true)"
    fi

    if [[ -z "$launcher_line" ]]; then
      # Launcher process not present; treat as idle.
      idle_count=$((idle_count + 1))
      echo "[dismiss_anr] $(_ts) [t=${elapsed}s poll=${poll_n}] idle_count=${idle_count} cpu=absent" >&2
    else
      # Extract the leading integer percentage (e.g. "  12% com.google.android.apps...")
      pct="$(echo "$launcher_line" | grep -oE '^[[:space:]]*[0-9]+' | tr -d ' ' || true)"
      if [[ -z "$pct" ]]; then
        # Percentage not parseable; skip this iteration without changing idle_count.
        echo "[dismiss_anr] $(_ts) [t=${elapsed}s poll=${poll_n}] idle_count=${idle_count} cpu=(unknown)%" >&2
      elif [[ "$pct" -lt 5 ]]; then
        idle_count=$((idle_count + 1))
      else
        idle_count=0
      fi
      if [[ -n "$pct" ]]; then
        echo "[dismiss_anr] $(_ts) [t=${elapsed}s poll=${poll_n}] idle_count=${idle_count} cpu=${pct}%" >&2
      fi
    fi

    if [[ $idle_count -ge 2 ]]; then
      # Safety check: even if CPU is idle, confirm the ANR dialog is gone.
      # The logcat trigger can miss the event if the ANR fires before the stream
      # starts or after the CPU has already settled.  A dumpsys window check here
      # is the fallback that catches those cases.
      window_dump=$("$ADB" shell dumpsys window 2>/dev/null) || true
      if echo "$window_dump" | grep -q "Application Not Responding"; then
        echo "[dismiss_anr] $(_ts) dumpsys window safety check: ANR dialog found; sending KEYCODE_ENTER." >&2
        "$ADB" shell input keyevent KEYCODE_ENTER 2>/dev/null || true
        idle_count=0
        # Continue the loop instead of exiting.
      else
        echo "[dismiss_anr] $(_ts) dumpsys window safety check: no ANR dialog found." >&2
        exit 0
      fi
    fi

    sleep "$POLL_INTERVAL"
    elapsed=$((elapsed + POLL_INTERVAL))
  done

  echo "[dismiss_anr] $(_ts) Timeout after ${TIMEOUT}s; proceeding anyway." >&2
  exit 0
) || true
