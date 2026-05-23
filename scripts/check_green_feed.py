#!/usr/bin/env python3
"""CI pre-flight smoke check: verifies MockCameraActivity's solid-green View renders correctly.

Samples the central 200x200 px region of a screencap and asserts that >= 90% of
pixels match #00C853 (R=0, G=200, B=83) within a per-channel tolerance of 20.
MockCameraActivity (Alternative 1) fills its window with a solid #00C853 View,
so a passing check confirms the activity launched and its View is fully visible.

Usage:
    # Single-shot check against an already-captured image:
    python3 scripts/check_green_feed.py <image.png>

    # Retry loop: capture a fresh screencap via adb on each attempt:
    python3 scripts/check_green_feed.py --adb <adb-path> <image.png>

Exits 0 on success, non-zero on failure (prints actual dominant color).
"""

import os
import shutil
import struct
import subprocess
import sys
import time
import zlib


# Target color: #00C853
TARGET_R = 0
TARGET_G = 200
TARGET_B = 83
TOLERANCE = 20
REGION_SIZE = 200
MIN_COVERAGE = 0.90

# Retry-loop settings (used when --adb is passed)
MAX_ATTEMPTS = 30
RETRY_DELAY_SECONDS = 2

# Directory for diagnostic screencap saves (relative to cwd, created on demand).
DIAG_SCREENCAP_DIR = "ci_diag_screencaps"


def decode_png_to_rgb(path: str) -> tuple[list[list[tuple[int, int, int]]], int, int]:
    """Decode a PNG file to a 2D list of (r, g, b) tuples."""
    with open(path, "rb") as f:
        data = f.read()

    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path}: not a valid PNG file")

    # Parse chunks to find IHDR and IDAT
    ihdr = None
    idat_parts = []
    pos = 8
    while pos < len(data):
        if pos + 8 > len(data):
            break
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        chunk_type = data[pos + 4 : pos + 8]
        chunk_data = data[pos + 8 : pos + 8 + length]
        if chunk_type == b"IHDR":
            ihdr = chunk_data
        elif chunk_type == b"IDAT":
            idat_parts.append(chunk_data)
        elif chunk_type == b"IEND":
            break
        pos += 8 + length + 4

    if ihdr is None:
        raise ValueError(f"{path}: missing IHDR chunk")
    if not idat_parts:
        raise ValueError(f"{path}: missing IDAT chunk")

    width, height, bit_depth, color_type = struct.unpack(">IIBB", ihdr[:10])

    if bit_depth != 8:
        raise ValueError(f"{path}: only 8-bit depth supported, got {bit_depth}")
    if color_type not in (2, 6):
        raise ValueError(
            f"{path}: only RGB (2) or RGBA (6) color type supported, got {color_type}"
        )

    channels = 3 if color_type == 2 else 4
    raw = zlib.decompress(b"".join(idat_parts))

    # Reconstruct scanlines with PNG filter reversal
    pixels = []
    stride = width * channels
    prev_row = bytes(stride)
    idx = 0
    for _ in range(height):
        filter_type = raw[idx]
        idx += 1
        row = bytearray(raw[idx : idx + stride])
        idx += stride

        if filter_type == 0:  # None
            pass
        elif filter_type == 1:  # Sub
            for i in range(channels, stride):
                row[i] = (row[i] + row[i - channels]) & 0xFF
        elif filter_type == 2:  # Up
            for i in range(stride):
                row[i] = (row[i] + prev_row[i]) & 0xFF
        elif filter_type == 3:  # Average
            for i in range(stride):
                a = row[i - channels] if i >= channels else 0
                b = prev_row[i]
                row[i] = (row[i] + (a + b) // 2) & 0xFF
        elif filter_type == 4:  # Paeth
            for i in range(stride):
                a = row[i - channels] if i >= channels else 0
                b = prev_row[i]
                c = prev_row[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                row[i] = (row[i] + pr) & 0xFF
        else:
            raise ValueError(f"Unknown PNG filter type {filter_type}")

        row_pixels = []
        for x in range(width):
            r = row[x * channels]
            g = row[x * channels + 1]
            b = row[x * channels + 2]
            row_pixels.append((r, g, b))
        pixels.append(row_pixels)
        prev_row = bytes(row)

    return pixels, width, height


def matches_green(r: int, g: int, b: int) -> bool:
    return (
        abs(r - TARGET_R) <= TOLERANCE
        and abs(g - TARGET_G) <= TOLERANCE
        and abs(b - TARGET_B) <= TOLERANCE
    )


def dominant_color(pixels_region: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    """Return the average color of sampled pixels."""
    total = len(pixels_region)
    if total == 0:
        return (0, 0, 0)
    avg_r = sum(p[0] for p in pixels_region) // total
    avg_g = sum(p[1] for p in pixels_region) // total
    avg_b = sum(p[2] for p in pixels_region) // total
    return (avg_r, avg_g, avg_b)


_ANR_DISMISS_MAX_RETRIES = 5
# Intentionally separate from RETRY_DELAY_SECONDS: this poll interval governs how
# long to wait between each KEYCODE_BACK send and the subsequent dumpsys window
# confirmation inside _dismiss_anr_if_present(), while RETRY_DELAY_SECONDS controls
# the outer green-feed retry loop.  They happen to share the same value today but
# may diverge if one needs tuning independently of the other.
_ANR_DISMISS_POLL_SECONDS = 2


def _dismiss_anr_if_present(adb: str) -> None:
    """Check for the Pixel Launcher ANR dialog and dismiss it if present.

    This is a lightweight guard that runs before every screencap in the retry
    loop.  dismiss_anr.sh exits as soon as Launcher CPU goes idle, but the ANR
    dialog can appear during or after the `am start -W` call (which takes 3+ s),
    after the watcher has already exited.  By checking here we ensure the retry
    loop is self-defending against late-appearing ANR dialogs.

    After sending KEYCODE_BACK, polls dumpsys window to confirm the dialog has
    actually disappeared before returning, retrying up to _ANR_DISMISS_MAX_RETRIES
    times.  This guards against cases where KEYCODE_BACK is dispatched but the
    dialog does not dismiss immediately (e.g. slow emulator rendering, focus
    stolen by another window).
    """
    try:
        window_dump = subprocess.run(
            [adb, "shell", "dumpsys", "window"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if "Application Not Responding" not in window_dump.stdout:
            return

        ts = time.strftime("%H:%M:%S")
        print(
            f"[check_green_feed] {ts} ANR dialog detected before screencap — sending KEYCODE_BACK.",
            file=sys.stderr,
        )
        for attempt in range(1, _ANR_DISMISS_MAX_RETRIES + 1):
            subprocess.run(
                [adb, "shell", "input", "keyevent", "KEYCODE_BACK"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            time.sleep(_ANR_DISMISS_POLL_SECONDS)
            ts = time.strftime("%H:%M:%S")
            confirm = subprocess.run(
                [adb, "shell", "dumpsys", "window"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if "Application Not Responding" not in confirm.stdout:
                print(
                    f"[check_green_feed] {ts} ANR dialog dismissed after {attempt} KEYCODE_BACK(s).",
                    file=sys.stderr,
                )
                return
            print(
                f"[check_green_feed] {ts} ANR dialog still present after KEYCODE_BACK #{attempt}.",
                file=sys.stderr,
            )
        print(
            f"[check_green_feed] {time.strftime('%H:%M:%S')} ANR dialog persisted after "
            f"{_ANR_DISMISS_MAX_RETRIES} KEYCODE_BACK sends — proceeding to screencap anyway.",
            file=sys.stderr,
        )
    except Exception as exc:  # noqa: BLE001
        print(
            f"[check_green_feed] ANR pre-screencap check failed (ignored): {exc}",
            file=sys.stderr,
        )


def _dump_first_failure_diagnostics(
    adb: str, screencap_path: str, attempt: int, start_time: float
) -> None:
    """Emit diagnostics to stderr on the first retry-loop failure.

    Logs the elapsed time, dumps window focus state and recent ANR events from
    logcat, and saves the current screencap to a dedicated diagnostics file so CI
    artifact collectors can retrieve it.  This runs only once (on the first
    failure) to avoid filling logs with repetitive output on later retries.
    """
    elapsed = time.monotonic() - start_time
    ts = time.strftime("%H:%M:%S")
    print(
        f"[check_green_feed] {ts} FIRST FAILURE DIAGNOSTICS "
        f"(attempt={attempt}, elapsed={elapsed:.1f}s)",
        file=sys.stderr,
    )

    # Dump window focus / dialog state — filtered for the most relevant lines.
    try:
        window_result = subprocess.run(
            [adb, "shell", "dumpsys", "window"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        relevant_lines = [
            line
            for line in window_result.stdout.splitlines()
            if any(kw in line for kw in ("Dialog", "Focused", "mCurrentFocus"))
        ]
        print(
            f"[check_green_feed] {ts} dumpsys window (Dialog/Focused/mCurrentFocus lines):",
            file=sys.stderr,
        )
        for line in relevant_lines:
            print(f"  {line}", file=sys.stderr)
        if not relevant_lines:
            print("  (no matching lines)", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[check_green_feed] dumpsys window failed: {exc}", file=sys.stderr)

    # Dump the last 20 lines of recent ActivityManager errors from logcat.
    try:
        logcat_result = subprocess.run(
            [adb, "shell", "logcat", "-d", "-t", "100", "ActivityManager:E", "*:S"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        logcat_lines = logcat_result.stdout.splitlines()[-20:]
        print(
            f"[check_green_feed] {ts} logcat ActivityManager:E (last 20 lines):",
            file=sys.stderr,
        )
        for line in logcat_lines:
            print(f"  {line}", file=sys.stderr)
        if not logcat_lines:
            print("  (no output)", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[check_green_feed] logcat dump failed: {exc}", file=sys.stderr)

    # Save the screencap to a dedicated diagnostics file.
    os.makedirs(DIAG_SCREENCAP_DIR, exist_ok=True)
    diag_path = os.path.join(DIAG_SCREENCAP_DIR, f"failure_attempt_{attempt:03d}.png")
    try:
        shutil.copy2(screencap_path, diag_path)
        print(
            f"[check_green_feed] {ts} Diagnostic screencap saved: {diag_path}",
            file=sys.stderr,
        )
    except Exception as exc:  # noqa: BLE001
        print(
            f"[check_green_feed] Failed to save diagnostic screencap: {exc}",
            file=sys.stderr,
        )


def check_image(path: str) -> int:
    """Check a single PNG image. Returns 0 on pass, 1 on fail, 2 on error."""
    try:
        pixels, width, height = decode_png_to_rgb(path)
    except (OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    # Sample the central REGION_SIZE x REGION_SIZE region
    cx = width // 2
    cy = height // 2
    half = REGION_SIZE // 2
    x0 = max(0, cx - half)
    y0 = max(0, cy - half)
    x1 = min(width, cx + half)
    y1 = min(height, cy + half)

    region_pixels = []
    for y in range(y0, y1):
        for x in range(x0, x1):
            region_pixels.append(pixels[y][x])

    total = len(region_pixels)
    if total == 0:
        print("ERROR: sampled region is empty (image too small?)", file=sys.stderr)
        return 2

    green_count = sum(1 for (r, g, b) in region_pixels if matches_green(r, g, b))
    coverage = green_count / total

    print(
        f"MockCameraActivity smoke check: {green_count}/{total} pixels match #00C853 "
        f"(tolerance={TOLERANCE}) — coverage={coverage:.1%}"
    )

    if coverage >= MIN_COVERAGE:
        print("PASS: MockCameraActivity solid-green View verified.")
        return 0
    else:
        dom = dominant_color(region_pixels)
        print(
            f"FAIL: coverage {coverage:.1%} < {MIN_COVERAGE:.0%}. "
            f"Actual dominant color: #{dom[0]:02X}{dom[1]:02X}{dom[2]:02X} "
            f"(R={dom[0]}, G={dom[1]}, B={dom[2]})"
        )
        return 1


def main() -> int:
    args = sys.argv[1:]

    # Parse optional --adb flag
    adb_path: str | None = None
    if len(args) >= 2 and args[0] == "--adb":
        adb_path = args[1]
        args = args[2:]

    if len(args) != 1:
        print(
            f"Usage: {sys.argv[0]} [--adb <adb-path>] <image.png>",
            file=sys.stderr,
        )
        return 2

    path = args[0]

    if adb_path is None:
        # Single-shot mode: check the provided image directly.
        return check_image(path)

    # Retry-loop mode: capture a fresh screencap on each attempt.
    start_time = time.monotonic()
    first_failure_diagnosed = False
    for attempt in range(1, MAX_ATTEMPTS + 1):
        # Sleep first so the display has time to render before the first check
        # and between retries; the caller has already waited for am start -W.
        time.sleep(RETRY_DELAY_SECONDS)
        # Wake the display and dismiss the keyguard before every screencap.
        # The screen may dim or the keyguard may re-appear during a long retry
        # loop (e.g. while the APK was being installed).
        # KEYCODE_WAKEUP (224) turns the screen on on all API levels.
        # An upward swipe then dismisses the swipe-based lock screen that the
        # emulator shows before the E2E PIN setup script runs.  Using a swipe
        # gesture avoids the side-effect of KEYCODE_MENU (82), which dispatches
        # KeyEvent.KEYCODE_MENU to a foregrounded activity and can open the
        # options/overflow menu, obscuring the green view.  When the screen is
        # already unlocked the swipe is a harmless gesture over the activity.
        input_service_ok = True
        wakeup_result = subprocess.run(
            [adb_path, "shell", "input", "keyevent", "224"],
            check=False,
            capture_output=True,
            text=True,
        )
        if wakeup_result.returncode != 0 or "Can't find service" in wakeup_result.stderr:
            print(
                f"WARNING: input service unavailable during KEYCODE_WAKEUP "
                f"(attempt {attempt}); sleeping extra {RETRY_DELAY_SECONDS}s for recovery.",
                file=sys.stderr,
            )
            input_service_ok = False
            time.sleep(RETRY_DELAY_SECONDS)

        swipe_result = subprocess.run(
            [adb_path, "shell", "input", "swipe", "300", "1000", "300", "300"],
            check=False,
            capture_output=True,
            text=True,
        )
        if swipe_result.returncode != 0 or "Can't find service" in swipe_result.stderr:
            if input_service_ok:
                # Only log/sleep here if we haven't already done so for the wakeup.
                print(
                    f"WARNING: input service unavailable during swipe "
                    f"(attempt {attempt}); sleeping extra {RETRY_DELAY_SECONDS}s for recovery.",
                    file=sys.stderr,
                )
                time.sleep(RETRY_DELAY_SECONDS)
        # Wait for any swipe animation to settle before capturing the screen.
        time.sleep(RETRY_DELAY_SECONDS)
        # Per-retry ANR check: if the Pixel Launcher ANR dialog appeared after
        # dismiss_anr.sh exited (which can happen because the watcher exits as
        # soon as CPU goes idle, before am start -W completes), dismiss it now
        # rather than capturing a screencap that is obscured by the dialog.
        _dismiss_anr_if_present(adb_path)
        with open(path, "wb") as out:
            subprocess.run(
                [adb_path, "exec-out", "screencap", "-p"],
                stdout=out,
                check=False,
            )
        result = check_image(path)
        if result == 0:
            return 0
        if not first_failure_diagnosed:
            first_failure_diagnosed = True
            _dump_first_failure_diagnostics(adb_path, path, attempt, start_time)
        if attempt < MAX_ATTEMPTS:
            print(f"Attempt {attempt} failed, retrying...")
    print(f"ERROR: pre-flight failed after {MAX_ATTEMPTS} attempts")
    return 1


if __name__ == "__main__":
    sys.exit(main())
