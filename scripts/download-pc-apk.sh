#!/usr/bin/env bash
# download-pc-apk.sh — Download the Pixel Camera APKM bundle from the private test-assets repo.
#
# Usage:
#   scripts/download-pc-apk.sh
#
# Requires the gh CLI to be authenticated. In CI, set GH_TOKEN to a PAT with
# read access to aunger/gallery-button-test-assets (stored as secret E2E_PC_APK_TOKEN).
# Locally, run 'gh auth login' once, or export GH_TOKEN before running.
#
# The downloaded file is saved to e2e/pixel-camera.apkm and is excluded from git.
# To install it on a device/emulator, use scripts/setup-e2e-emulator.sh which calls
# 'adb install-multiple' after extracting the splits.
#
# Alternative (physical Pixel device):
#   adb pull $(adb shell pm path com.google.android.GoogleCamera | cut -d: -f2) /tmp/base.apk
#   mkdir -p e2e && cp /tmp/base.apk e2e/pixel-camera.apkm   # single-APK fallback

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUTPUT="$REPO_ROOT/e2e/pixel-camera.apkm"

mkdir -p "$(dirname "$OUTPUT")"

echo "==> Downloading Pixel Camera APKM from aunger/gallery-button-test-assets (latest release)..."
gh release download \
    --repo aunger/gallery-button-test-assets \
    --pattern "*.apkm" \
    --output "$OUTPUT" \
    --clobber

echo "Saved to: $OUTPUT"
echo "Size: $(du -sh "$OUTPUT" | cut -f1)"
