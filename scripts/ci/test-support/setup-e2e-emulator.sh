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
#   - For full setup: sdkmanager and avdmanager, together in whichever of
#     $ANDROID_HOME/cmdline-tools/latest/bin or $ANDROID_HOME/cmdline-tools/bin
#     is used; both are read out of the one directory resolved below
#
# Environment:
#   DEVICE_TIMEOUT        Seconds to wait for the emulator to appear on adb
#                         before giving up (default: 1200). Raise it if this
#                         machine is slower than that; the tests lower it.
#   DEVICE_POLL_INTERVAL  Seconds between those checks (default: 5).
#   EMULATOR_LOG          Where the emulator's output goes, and what is printed
#                         when the wait above fails (default: /tmp/emulator.log).

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

# The SDK manager installs the command-line tools under `cmdline-tools/latest`;
# a download unzipped in place and never renamed leaves them in
# `cmdline-tools/bin`. Both directories hold sdkmanager and avdmanager, so one
# resolved directory serves both binaries. Testing for sdkmanager rather than
# for the directory also covers a `latest` that exists without the tools in it.
#
# The fallback named the standalone `tools` package until issue #1133. Google
# has withdrawn that package from the SDK catalog (issue #1127), so no install
# can obtain it now. An SDK that acquired it before the withdrawal still has the
# directory, and this deliberately stops reaching for it: `tools/bin` is a dead
# end for anyone setting a machine up today.
#
# The candidates are named once here because the guard below reports them back
# to the developer, and a message built from these variables cannot drift from
# the resolution it describes.
CMDLINE_TOOLS_LATEST="$ANDROID_SDK/cmdline-tools/latest/bin"
CMDLINE_TOOLS_UNZIPPED="$ANDROID_SDK/cmdline-tools/bin"
CMDLINE_TOOLS="$CMDLINE_TOOLS_LATEST"
if [[ ! -x "$CMDLINE_TOOLS/sdkmanager" ]]; then
    CMDLINE_TOOLS="$CMDLINE_TOOLS_UNZIPPED"
fi

# Step 1-3: AVD creation and emulator start (local only)-------------------
if [[ "$POST_BOOT_ONLY" == false ]]; then
    # Mirrors the adb guard above. Without it the run dies on a bash 127 that
    # names only whichever candidate the resolution settled on, which is the
    # unzipped-in-place layout rather than the `cmdline-tools/latest` a
    # developer most likely needs to create. The guard is here rather than
    # beside the resolution because --post-boot runs, which is how CI invokes
    # this script, need no command-line tools at all.
    if [[ ! -x "$CMDLINE_TOOLS/sdkmanager" ]]; then
        echo "ERROR: sdkmanager not found in $CMDLINE_TOOLS_LATEST" >&2
        echo "       or $CMDLINE_TOOLS_UNZIPPED." >&2
        echo "       Install the Android SDK Command-line Tools, or pass --post-boot" >&2
        echo "       to skip AVD creation on an emulator that is already running." >&2
        exit 1
    fi

    # The resolution keys on sdkmanager, so nothing so far has looked for
    # avdmanager. Both binaries ship in the same package and are read out of the
    # one resolved directory, so one without the other is a damaged install and
    # worth naming as that. Left to the create line below it would arrive as a
    # bash "command not found" (127, or 126 where the file is there without its
    # execute bit, which `-x` rejects too) inside that command's captured
    # stderr, after a system-image download the run has no use for (issue #1141).
    if [[ ! -x "$CMDLINE_TOOLS/avdmanager" ]]; then
        echo "ERROR: avdmanager not found beside sdkmanager in $CMDLINE_TOOLS." >&2
        echo "       Reinstall the Android SDK Command-line Tools, or pass --post-boot" >&2
        echo "       to skip AVD creation on an emulator that is already running." >&2
        exit 1
    fi

    AVD_NAME="gb4pc_e2e"
    API_LEVEL=35
    SYSTEM_IMAGE="system-images;android-${API_LEVEL};google_apis;x86_64"

    echo "==> Installing system image: $SYSTEM_IMAGE"
    "$CMDLINE_TOOLS/sdkmanager" --install "$SYSTEM_IMAGE" "platform-tools" "emulator"

    echo "==> Creating AVD: $AVD_NAME"
    # `--force` is what makes a re-run idempotent: it overwrites an existing AVD
    # rather than refusing to create one. That overwrite is the only case the
    # discarded exit status here was written for, so nothing else it was hiding
    # is worth hiding, and a non-zero exit now ends the run (issue #1141).
    #
    # avdmanager writes progress and package warnings to stderr even when it
    # succeeds, so the stream is captured rather than left on the terminal. A
    # successful run is as quiet as `2>/dev/null` made it, and a failing one
    # gets the diagnosis that redirection threw away along with the failure.
    AVD_CREATE_LOG="$(mktemp)"
    if ! echo "no" | "$CMDLINE_TOOLS/avdmanager" create avd \
        --name "$AVD_NAME" \
        --package "$SYSTEM_IMAGE" \
        --device "pixel_6" \
        --force 2>"$AVD_CREATE_LOG"; then
        echo "ERROR: avdmanager could not create the AVD $AVD_NAME." >&2
        cat "$AVD_CREATE_LOG" >&2
        rm -f "$AVD_CREATE_LOG"
        exit 1
    fi
    rm -f "$AVD_CREATE_LOG"

    echo "==> Starting emulator headlessly"
    EMULATOR="$ANDROID_SDK/emulator/emulator"
    EMULATOR_LOG="${EMULATOR_LOG:-/tmp/emulator.log}"
    nohup "$EMULATOR" \
        -avd "$AVD_NAME" \
        -no-window \
        -no-audio \
        -no-boot-anim \
        -gpu swiftshader_indirect \
        -memory 2048 \
        > "$EMULATOR_LOG" 2>&1 &
    EMULATOR_PID=$!
    echo "Emulator PID: $EMULATOR_PID"

    # `adb wait-for-device` blocks with no bound, which made this the one step in
    # the sequence that could not give up: the boot and package-manager loops
    # below both do. Polling `get-state` for the condition wait-for-device waits
    # on keeps the shape of those loops and needs no `timeout` binary, which is
    # not on every developer's machine. CI bounds its own wait-for-device
    # separately, in the "Wait for emulator service readiness" step of
    # .github/workflows/build.yml.
    #
    # An emulator that dies during startup (no KVM, a corrupt AVD) is reported as
    # soon as its process is gone rather than at the bound, since nothing is
    # gained by waiting out a clock for a process that has already left. That is
    # the check the workflow's "Start emulator" step makes on the same emulator
    # binary. Either way the log holds the reason, so it is printed alongside.
    echo "==> Waiting for device to come online..."
    # 1200 is what the "Wait for emulator service readiness" step of
    # .github/workflows/build.yml already allows this same wait, against an
    # emulator it has just launched. That runner has KVM and a warm system
    # image, and a developer's machine may have neither, so the local bound
    # should not be the tighter of the two. This converts a wait that never gave
    # up into one that does, and a slow first boot succeeding slowly is the case
    # that a smaller number would newly break.
    DEVICE_TIMEOUT="${DEVICE_TIMEOUT:-1200}"
    DEVICE_POLL_INTERVAL="${DEVICE_POLL_INTERVAL:-5}"
    DEVICE_ELAPSED=0
    until [[ "$("$ADB" get-state 2>/dev/null | tr -d '\r')" == "device" ]]; do
        if ! kill -0 "$EMULATOR_PID" 2>/dev/null; then
            echo "ERROR: The emulator exited before a device came online." >&2
            echo "=== $EMULATOR_LOG ===" >&2
            cat "$EMULATOR_LOG" >&2
            exit 1
        fi
        if [[ $DEVICE_ELAPSED -ge $DEVICE_TIMEOUT ]]; then
            echo "ERROR: No device came online within ${DEVICE_TIMEOUT}s." >&2
            echo "       If this machine is just slow to boot an emulator, set" >&2
            echo "       DEVICE_TIMEOUT higher and run again." >&2
            # The poll discards this to test the state, and it is the one place
            # adb explains itself: "more than one device/emulator" reads very
            # differently from "no devices/emulators found", and the script
            # assumes a single device from here on either way.
            echo "       adb get-state says:" >&2
            "$ADB" get-state 2>&1 | sed 's/^/       /' >&2 || true
            # Left running on purpose. It may yet be booting, and raising
            # DEVICE_TIMEOUT should not mean starting it over; a developer who
            # wants it gone is better placed to decide that than this script is.
            # Announced because nothing else announces it, and because the
            # obvious next move is to change something and run again: that run's
            # `avdmanager create avd --force` rewrites this AVD's files
            # underneath whatever is still using them.
            echo "       The emulator is still running as PID $EMULATOR_PID." >&2
            echo "       Leave it to finish booting, or stop it with: kill $EMULATOR_PID" >&2
            echo "=== $EMULATOR_LOG ===" >&2
            cat "$EMULATOR_LOG" >&2
            exit 1
        fi
        sleep "$DEVICE_POLL_INTERVAL"
        DEVICE_ELAPSED=$((DEVICE_ELAPSED + DEVICE_POLL_INTERVAL))
        echo "  ...waiting for device ($DEVICE_ELAPSED / ${DEVICE_TIMEOUT}s)"
    done
    echo "==> Device online."

    echo "==> Waiting for full boot (sys.boot_completed=1)..."
    BOOT_TIMEOUT=180
    BOOT_ELAPSED=0
    while [[ "$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" != "1" ]]; do
        if [[ $BOOT_ELAPSED -ge $BOOT_TIMEOUT ]]; then
            echo "ERROR: Emulator did not finish booting within ${BOOT_TIMEOUT}s." >&2
            exit 1
        fi
        sleep 5
        BOOT_ELAPSED=$((BOOT_ELAPSED + 5))
        echo "  ...waiting ($BOOT_ELAPSED / ${BOOT_TIMEOUT}s)"
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
# Neither setting is the source of the window-obscured touch drops the permission-dialog suites
# re-tap around (issues #581, #925). Issue #930 investigated and cleared them, so do not disable a
# deliberate debugging aid chasing that flake. Reason 1 settles it; 2 and 3 only corroborate.
#
#   1. AOSP's DisplayPolicy.enablePointerLocation() adds the readout as a
#      TYPE_SECURE_SYSTEM_OVERLAY, InputMonitor.isTrustedOverlay() lists that type, and so
#      WindowState.isWindowTrustedOverlay() holds on type alone. InputDispatcher's
#      canBeObscuredBy() returns false for every TRUSTED_OVERLAY window, dropping it out of
#      isWindowObscuredAtPointLocked() and isWindowObscuredLocked(), the only producers of the
#      FLAG_WINDOW_IS_OBSCURED/FLAG_WINDOW_IS_PARTIALLY_OBSCURED pair SecureButton filters on.
#      show_touches is not a window at all: inputflinger's PointerChoreographer draws its spots
#      as PointerController sprites, which never reach that window list. (Read at
#      android15-release, this AVD's API 35.)
#   2. Both settings are written once, here, so every tap of every suite sees the same overlay,
#      while the drop comes and goes: PartialAccessPhotoPickerE2ETest, whose awaitPickerWindow
#      doc points back here, needed a re-tap on 6 of the 25 E2E runs of 22-23 Aug 2026 and none
#      on the other 19 (that count is its own; the sibling dialog suites are not measured), and
#      run 32587090727 holds a dropped tap and an identical re-tap 5s later that landed. That
#      rules the overlay out as a sufficient cause only. A constant can be one half of a
#      conjunction, so this would read the same even if the overlay were a necessary co-factor,
#      which is why reason 1 and not this one closes the question.
#   3. What does vary is a timing of the test's own: over those same 25 runs the dropped taps
#      were logged sooner after the requestPermissions() click than the ones that stuck, a mean
#      1430ms against 1735ms (one-sided exact permutation test over all 177,100 splits,
#      p = 0.012). A lead, not a finding: n is 6 on the dropped side, the ranges (1200-1572ms,
#      1136-2150ms) almost entirely overlap, and the split was chosen after seeing the data. It
#      also admits two readings, since awaitAndTap taps as soon as the option is findable: a
#      younger dialog window at tap time (issue #581's condition), or merely a dialog that
#      appeared sooner. Issue #930 did not separate them.
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
