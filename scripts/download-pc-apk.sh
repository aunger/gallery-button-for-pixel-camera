#!/usr/bin/env bash
# download-pc-apk.sh — Download the Pixel Camera APK to e2e/pixel-camera.apk.
#
# Usage:
#   scripts/download-pc-apk.sh <URL>
#
# The URL is typically stored in a GitHub Actions variable E2E_PC_APK_URL.
# Locally, developers can obtain the APK from a Pixel device:
#   adb pull /data/app/~~<hash>/com.google.android.GoogleCamera-<hash>/base.apk e2e/pixel-camera.apk
# or download from APKMirror and place it at e2e/pixel-camera.apk manually.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUTPUT="$REPO_ROOT/e2e/pixel-camera.apk"

if [[ $# -lt 1 || -z "$1" ]]; then
    echo "Usage: $0 <URL>" >&2
    echo "  Downloads the Pixel Camera APK to e2e/pixel-camera.apk" >&2
    exit 1
fi

URL="$1"

mkdir -p "$(dirname "$OUTPUT")"

echo "Downloading Pixel Camera APK from: $URL"
if command -v curl &>/dev/null; then
    curl -fL --progress-bar -o "$OUTPUT" "$URL"
elif command -v wget &>/dev/null; then
    wget -q --show-progress -O "$OUTPUT" "$URL"
else
    echo "ERROR: Neither curl nor wget found. Please install one." >&2
    exit 1
fi

echo "Saved to: $OUTPUT"
echo "Size: $(du -sh "$OUTPUT" | cut -f1)"
