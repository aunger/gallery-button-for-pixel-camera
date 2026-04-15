#!/usr/bin/env bash
# setup-e2e-emulator.sh — Prepare an Android emulator for E2E tests.
#
# Usage:
#   scripts/setup-e2e-emulator.sh            # Full local setup (steps 1–7)
#   scripts/setup-e2e-emulator.sh --post-boot # CI post-boot setup only (steps 4–7)
#
# Full setup (local):
#   1. Create AVD (API 33, Google APIs, x86_64, Pixel_6 skin)
#   2. Start emulator headlessly
#   3. Wait for full boot
#   4. Install Pixel Camera APK
#   5. Grant PACKAGE_USAGE_STATS to Pixel Camera
#   6. Grant SYSTEM_ALERT_WINDOW to GB4PC
#   7. Disable animations
#
# Post-boot setup (CI): the emulator is already running and all system services
# have been verified ready by the workflow; this script performs steps 4–7 only.
#
# Prerequisites:
#   - ANDROID_HOME (or ANDROID_SDK_ROOT) must be set
#   - For full setup: sdkmanager, avdmanager must be on PATH (or in $ANDROID_HOME/cmdline-tools/latest/bin)
#   - e2e/pixel-camera.apkm must exist (run scripts/download-pc-apk.sh first)
#   - unzip must be available (standard on Ubuntu/macOS)

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
    API_LEVEL=33
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

# ── Step 4: Install Pixel Camera from APKM bundle ───────────────────────────
PC_APKM="$REPO_ROOT/e2e/pixel-camera.apkm"
if [[ -f "$PC_APKM" ]]; then
    echo "==> Extracting Pixel Camera splits from APKM bundle..."
    SPLITS_DIR=$(mktemp -d)
    unzip -o "$PC_APKM" -d "$SPLITS_DIR" > /dev/null
    echo "==> Installing Pixel Camera splits (adb install-multiple)..."
    # Retry up to 3 times; 'Broken pipe' can occur if the package manager
    # service is still warming up immediately after sys.boot_completed=1.
    INSTALL_OK=false
    for attempt in 1 2 3; do
        echo "  Install attempt $attempt..."
        # shellcheck disable=SC2046
        INSTALL_OUT=$("$ADB" install-multiple -r \
            $(find "$SPLITS_DIR" -maxdepth 1 -name "*.apk" | sort) 2>&1)
        INSTALL_EXIT=$?
        echo "$INSTALL_OUT"
        if [[ $INSTALL_EXIT -eq 0 ]]; then
            INSTALL_OK=true
            break
        fi
        echo "  Attempt $attempt failed (exit $INSTALL_EXIT) — waiting 15s before retry..."
        sleep 15
    done
    rm -rf "$SPLITS_DIR"
    if [[ "$INSTALL_OK" == true ]]; then
        echo "Pixel Camera installed."
    else
        echo "ERROR: Pixel Camera install failed after 3 attempts — cannot run E2E tests." >&2
        exit 1
    fi
else
    echo "WARNING: $PC_APKM not found. Skipping Pixel Camera install."
    echo "  Run 'scripts/download-pc-apk.sh' to download it."
fi

# ── Step 5: Grant PACKAGE_USAGE_STATS to Pixel Camera ───────────────────────
# API 29+ renamed the appops string from PACKAGE_USAGE_STATS to GET_USAGE_STATS.
echo "==> Granting usage stats permission to Pixel Camera..."
"$ADB" shell appops set com.google.android.GoogleCamera GET_USAGE_STATS allow || \
"$ADB" shell appops set com.google.android.GoogleCamera PACKAGE_USAGE_STATS allow || true

# ── Step 6: Grant SYSTEM_ALERT_WINDOW to GB4PC ──────────────────────────────
echo "==> Granting SYSTEM_ALERT_WINDOW to GB4PC..."
"$ADB" shell appops set com.gb4pc SYSTEM_ALERT_WINDOW allow || true

# ── Step 7: Disable animations ──────────────────────────────────────────────
echo "==> Disabling animations..."
"$ADB" shell settings put global window_animation_scale 0
"$ADB" shell settings put global transition_animation_scale 0
"$ADB" shell settings put global animator_duration_scale 0

echo ""
echo "==> E2E emulator setup complete. Run E2E tests with:"
echo "    ./gradlew connectedE2EAndroidTest"
