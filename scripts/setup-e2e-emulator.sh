#!/usr/bin/env bash
# setup-e2e-emulator.sh — Prepare an Android emulator for E2E tests.
#
# Usage:
#   scripts/setup-e2e-emulator.sh            # Full local setup (steps 1–6)
#   scripts/setup-e2e-emulator.sh --post-boot # CI post-boot setup only (steps 4–6)
#
# Full setup (local):
#   1. Create AVD (API 35, Google APIs, x86_64, Pixel_6 skin)
#   2. Start emulator headlessly
#   3. Wait for full boot
#   4. Grant GET_USAGE_STATS to GB4PC
#   5. Grant SYSTEM_ALERT_WINDOW to GB4PC
#   6. Disable animations
#
# Post-boot setup (CI): the emulator is already running and all system services
# have been verified ready by the workflow; this script performs steps 4–6 only.
# Mock Pixel Camera (e2e-mock-camera) is installed separately by the CI workflow
# and by the connectedE2EAndroidTest Gradle task.
#
# Prerequisites:
#   - ANDROID_HOME (or ANDROID_SDK_ROOT) must be set
#   - For full setup: sdkmanager, avdmanager must be on PATH (or in $ANDROID_HOME/cmdline-tools/latest/bin)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

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

# ── Step 1–3: AVD creation and emulator start (local only) ──────────────────
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

echo ""
echo "==> E2E emulator setup complete. Run E2E tests with:"
echo "    ./gradlew connectedE2EAndroidTest"
