#!/usr/bin/env bash
# setup-e2e-emulator.sh: Prepare an Android emulator for E2E tests.
#
# Usage:
#   scripts/ci/test-support/setup-e2e-emulator.sh            # Full local setup (steps 1-9)
#   scripts/ci/test-support/setup-e2e-emulator.sh --post-boot # CI post-boot setup only (steps 4-9)
#
# Full setup (local):
#   1. Create AVD (API 35, Google APIs, x86_64, Pixel_6 skin)
#   2. Start emulator headlessly
#   3. Wait for full boot
#   4. Grant GET_USAGE_STATS to GB4PC
#   5. Grant SYSTEM_ALERT_WINDOW to GB4PC
#   6. Disable animations
#   7. Enable touch visualization (show_touches, pointer_location)
#   8. Set lock-screen PIN (so the keyguard actually engages on sleep)
#   9. Dismiss the keyguard (setting the PIN engages it; leaving it engaged
#      would block activity launches from non-lockScreen tests)
#
# Post-boot setup (CI): the emulator is already running and all system services
# have been verified ready by the workflow; this script performs steps 4-9 only.
# Mock Pixel Camera (e2e-mock-camera) is installed separately by the CI workflow
# and by the connectedE2EAndroidTest Gradle task.
#
# To clear the PIN after the test run (e.g. for a clean local AVD), run:
#   adb shell locksettings clear --old 1234
#
# Prerequisites:
#   - ANDROID_HOME (or ANDROID_SDK_ROOT) must be set
#   - For full setup: sdkmanager, avdmanager must be on PATH (or in $ANDROID_HOME/cmdline-tools/latest/bin)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

POST_BOOT_ONLY=false
if [[ "${1:-}" == "--post-boot" ]]; then
    POST_BOOT_ONLY=true
fi

# ── Resolve Android SDK ─────────────────────────────────────────────────────
ANDROID_SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
if [[ -z "$ANDROID_SDK" ]]; then
    echo "ERROR: ANDROID_HOME or ANDROID_SDK_ROOT must be set." >&2
    exit 1
fi

ADB="$ANDROID_SDK/platform-tools/adb"
if [[ ! -x "$ADB" ]]; then
    echo "ERROR: adb not found at $ADB" >&2
    exit 1
fi

CMDLINE_TOOLS="$ANDROID_SDK/cmdline-tools/latest/bin"
if [[ ! -d "$CMDLINE_TOOLS" ]]; then
    # Try older paths
    CMDLINE_TOOLS="$ANDROID_SDK/tools/bin"
fi

# Step 1-3: AVD creation and emulator start (local only)-------------------
if [[ "$POST_BOOT_ONLY" == false ]]; then
    AVD_NAME="gb4pc_e2e"
    API_LEVEL=35
    SYSTEM_IMAGE="system-images;android-${API_LEVEL};google_apis;x86_64"

    echo "==> Installing system image: $SYSTEM_IMAGE"
    "$CMDLINE_TOOLS/sdkmanager" --install "$SYSTEM_IMAGE" "platform-tools" "emulator" || \
        "$ANDROID_SDK/cmdline-tools/bin/sdkmanager" --install "$SYSTEM_IMAGE" "platform-tools" "emulator"

    echo "==> Creating AVD: $AVD_NAME"
    echo "no" | "$CMDLINE_TOOLS/avdmanager" create avd \
        --name "$AVD_NAME" \
        --package "$SYSTEM_IMAGE" \
        --device "pixel_6" \
        --force 2>/dev/null || true   # --force overwrites existing AVD (idempotent)

    echo "==> Starting emulator headlessly"
    EMULATOR="$ANDROID_SDK/emulator/emulator"
    nohup "$EMULATOR" \
        -avd "$AVD_NAME" \
        -no-window \
        -no-audio \
        -no-boot-anim \
        -gpu swiftshader_indirect \
        -memory 2048 \
        > /tmp/emulator.log 2>&1 &
    EMULATOR_PID=$!
    echo "Emulator PID: $EMULATOR_PID"

    echo "==> Waiting for device to come online..."
    "$ADB" wait-for-device

    echo "==> Waiting for full boot (sys.boot_completed=1)..."
    BOOT_TIMEOUT=180
    ELAPSED=0
    while [[ "$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" != "1" ]]; do
        if [[ $ELAPSED -ge $BOOT_TIMEOUT ]]; then
            echo "ERROR: Emulator did not finish booting within ${BOOT_TIMEOUT}s." >&2
            exit 1
        fi
        sleep 5
        ELAPSED=$((ELAPSED + 5))
        echo "  ...waiting ($ELAPSED / ${BOOT_TIMEOUT}s)"
    done
    echo "==> Device fully booted."
fi

# ── Wait for package manager to be fully ready ──────────────────────────────
# sys.boot_completed=1 can be set before the package manager service accepts
# install sessions. Poll until 'pm list packages' succeeds.
echo "==> Waiting for package manager to be ready..."
PM_TIMEOUT=120
PM_ELAPSED=0
until "$ADB" shell pm list packages > /dev/null 2>&1; do
    if [[ $PM_ELAPSED -ge $PM_TIMEOUT ]]; then
        echo "ERROR: Package manager not ready after ${PM_TIMEOUT}s." >&2
        exit 1
    fi
    sleep 5
    PM_ELAPSED=$((PM_ELAPSED + 5))
    echo "  ...waiting for PM ($PM_ELAPSED / ${PM_TIMEOUT}s)"
done
echo "Package manager is ready."

# ── Step 4: Grant GET_USAGE_STATS to GB4PC ──────────────────────────────────
# GB4PC's ForegroundDetector reads UsageStatsManager to detect which app is in
# the foreground. Without this permission, the overlay never appears.
# API 29+ renamed the appops string from PACKAGE_USAGE_STATS to GET_USAGE_STATS.
# Note: in CI the app may not be installed yet at this point (the Gradle task
# installs it); the Gradle task also grants this permission after install.
echo "==> Granting GET_USAGE_STATS to GB4PC..."
"$ADB" shell appops set com.gb4pc GET_USAGE_STATS allow || \
"$ADB" shell appops set com.gb4pc PACKAGE_USAGE_STATS allow || true

# ── Step 5: Grant SYSTEM_ALERT_WINDOW to GB4PC ──────────────────────────────
echo "==> Granting SYSTEM_ALERT_WINDOW to GB4PC..."
"$ADB" shell appops set com.gb4pc SYSTEM_ALERT_WINDOW allow || true

# ── Step 6: Disable animations ──────────────────────────────────────────────
echo "==> Disabling animations..."
"$ADB" shell settings put global window_animation_scale 0
"$ADB" shell settings put global transition_animation_scale 0
"$ADB" shell settings put global animator_duration_scale 0

# ── Step 7: Enable touch visualization ──────────────────────────────────────
# Show a ripple at every tap location (show_touches) and a live readout of pointer
# position/pressure (pointer_location), so screen recordings and the live screen make
# it obvious where and when the tests are tapping (issue #604). Configured here, once
# for the whole job, rather than per-suite, so every emulator-based E2E run gets it.
#
# Neither setting is the source of the window-obscured touch drops the permission-dialog
# suites re-tap around (issues #581, #925). Issue #930 investigated exactly that and cleared
# them, so do not disable a deliberate debugging aid chasing that flake. Three independent
# reasons, any one of which is sufficient:
#
#   1. Mechanism. AOSP's DisplayPolicy.enablePointerLocation() adds the readout as a
#      TYPE_SECURE_SYSTEM_OVERLAY window, and InputMonitor.isTrustedOverlay() lists that type,
#      so WindowState.isWindowTrustedOverlay() holds for it on its type alone. InputDispatcher's
#      canBeObscuredBy() returns false for every TRUSTED_OVERLAY window, which drops it out of
#      both isWindowObscuredAtPointLocked() and isWindowObscuredLocked()--the only two producers
#      of the FLAG_WINDOW_IS_OBSCURED/FLAG_WINDOW_IS_PARTIALLY_OBSCURED pair that AOSP's
#      SecureButton filters on. show_touches is not a window at all: inputflinger's
#      PointerChoreographer draws its spots through a PointerController's sprites, which never
#      enter the dispatcher's window list. (Sources read at android15-release, this AVD's API 35.)
#   2. A constant cannot explain an intermittent result. Both settings are written once, here,
#      and stay on for the rest of the job, so every tap of every suite sees the same overlay.
#      The drop does not behave that way: over the 25 E2E runs of 22-23 Aug 2026,
#      PartialAccessPhotoPickerE2ETest logged a re-tap on 6 of them and none on the other 19.
#      Run 32587090727 makes the point inside a single run: the tap that was dropped and the
#      identical re-tap 5s later that landed were separated by nothing but time.
#   3. What does separate the two populations is when the tap went in. Across those same 25 runs
#      the dropped taps landed sooner after the request than the ones that stuck, a mean 1430ms
#      against 1735ms (one-sided exact permutation test on the 25 values, p = 0.013). That is
#      the freshly-created-window condition issue #581 described, and it is a property of the
#      test's own timing rather than of anything on screen.
echo "==> Enabling touch visualization (show_touches, pointer_location)..."
"$ADB" shell settings put system show_touches 1
"$ADB" shell settings put system pointer_location 1

# ── Step 8: Set lock-screen PIN ─────────────────────────────────────────────
# Without a lock-screen credential the keyguard never engages on the CI
# emulator: KEYCODE_SLEEP turns the display off but KeyguardManager.isKeyguardLocked
# remains false, so E2EFixture.lockScreen() times out (issue #178).
# Setting a PIN makes the keyguard secure, so it engages whenever the display
# sleeps. The PIN itself is never entered by the tests; they only rely on the
# keyguard being locked. Clear the PIN with `locksettings clear --old 1234`
# if you need to remove it later (the AVD is otherwise idempotent).
#
# `locksettings set-pin` rejects a new PIN if one is already configured unless
# `--old` is supplied. For idempotency across re-runs of --post-boot on a
# persistent local AVD, try the bare form first and fall back to `--old 1234`
# (which is a no-op when the PIN is already 1234). Either path leaves the AVD
# with PIN=1234, which is all the keyguard needs to engage on sleep.
echo "==> Setting lock-screen PIN (1234) so keyguard engages on sleep..."
"$ADB" shell locksettings set-pin 1234 || \
    "$ADB" shell locksettings set-pin --old 1234 1234

# Step 9: Dismiss the (now-secure) keyguard--------------------------------
# Setting a PIN engages the keyguard immediately on API 35, even with the
# display on. If we leave it engaged here, the next `am start STILL_IMAGE_CAMERA`
# from a test (e.g. PixelCameraOverlayE2ETest.overlayAppearsWhenViewfinderOpens)
# is force-removed by WindowManager because the activity is launched under the
# lock screen; the test then times out waiting for the overlay.
# Wake the display, request keyguard dismissal, type the PIN, and submit ENTER.
# The CI workflow's `stay_on_while_plugged_in 7` then keeps the keyguard
# dismissed until a test explicitly calls KEYCODE_SLEEP (E2EFixture.lockScreen).
echo "==> Dismissing keyguard so subsequent tests can launch activities..."
"$ADB" shell input keyevent 224                 # KEYCODE_WAKEUP
"$ADB" shell wm dismiss-keyguard
"$ADB" shell input text 1234
"$ADB" shell input keyevent 66                  # KEYCODE_ENTER

echo ""
echo "==> E2E emulator setup complete. Run E2E tests with:"
echo "    ./gradlew connectedE2EAndroidTest"
