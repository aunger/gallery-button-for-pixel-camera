#!/usr/bin/env bash
# ocr_screenshots.sh: Run Tesseract OCR on every PNG in a directory and
# append a markdown summary table to $GITHUB_STEP_SUMMARY.
#
# Usage: ocr_screenshots.sh <screenshot-dir>
#
# For each *.png found (recursively) this script:
#   1. Runs `tesseract` to produce a companion <name>.ocr.txt file.
#   2. Appends one table row (filename + first 80 chars of OCR text) to
#      $GITHUB_STEP_SUMMARY when that variable is set.
#
# Exits 0 in all cases; OCR failure on individual images is non-fatal.

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $(basename "$0") <screenshot-dir>" >&2
  exit 1
fi

SCREENSHOT_DIR="$1"

if [[ ! -d "$SCREENSHOT_DIR" ]]; then
  echo "Directory not found: $SCREENSHOT_DIR" >&2
  exit 1
fi

shopt -s globstar nullglob
PNG_FILES=("$SCREENSHOT_DIR"/**/*.png)

if [[ ${#PNG_FILES[@]} -eq 0 ]]; then
  echo "No screenshots found in $SCREENSHOT_DIR; skipping OCR." >&2
  exit 0
fi

# Write the summary header only when there are images to report.
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo "## E2E Screenshot OCR"
    echo ""
    echo "| Screenshot | OCR preview (first 80 chars) |"
    echo "| --- | --- |"
  } >> "$GITHUB_STEP_SUMMARY"
fi

for PNG in "${PNG_FILES[@]}"; do
  [[ -f "$PNG" ]] || continue

  # Derive output base: strip .png, append .ocr so tesseract writes <name>.ocr.txt
  OCR_BASE="${PNG%.png}.ocr"
  TXT="${OCR_BASE}.txt"

  tesseract "$PNG" "$OCR_BASE" txt 2>/dev/null || true

  if [[ -f "$TXT" ]]; then
    PREVIEW=$(tr -d '\n' < "$TXT" | tr -s ' ' | cut -c1-80)
  else
    PREVIEW="(OCR failed)"
  fi

  BASENAME=$(basename "$PNG")
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    echo "| \`$BASENAME\` | $PREVIEW |" >> "$GITHUB_STEP_SUMMARY"
  fi
done
