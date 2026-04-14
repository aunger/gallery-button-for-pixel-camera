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

# gh release download skips prerelease tags by default; query the API to find
# the most recent release (including prereleases) that contains an .apkm asset.
LATEST_TAG=$(curl -s \
    -H "Authorization: Bearer $GH_TOKEN" \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/aunger/gallery-button-test-assets/releases" \
  | python3 -c "
import sys, json
releases = json.load(sys.stdin)
for r in releases:
    if any(a['name'].endswith('.apkm') for a in r.get('assets', [])):
        print(r['tag_name'])
        break
")

if [[ -z "$LATEST_TAG" ]]; then
    echo "ERROR: No release with an .apkm asset found in aunger/gallery-button-test-assets" >&2
    exit 1
fi

echo "  Using release tag: $LATEST_TAG"
gh release download "$LATEST_TAG" \
    --repo aunger/gallery-button-test-assets \
    --pattern "*.apkm" \
    --output "$OUTPUT" \
    --clobber

echo "Saved to: $OUTPUT"
echo "Size: $(du -sh "$OUTPUT" | cut -f1)"
