#!/usr/bin/env bash
# scripts/regenerate-gradle-verification.sh -- regenerate gradle/verification-metadata.xml.
#
# Gradle dependency verification (issue #714) pins every plugin, dependency, and
# metadata artifact the build resolves to a SHA-256 checksum in
# gradle/verification-metadata.xml. Once that file is present, EVERY Gradle
# invocation fails if any resolved artifact is missing from it or mismatched
# against it. So any change that moves the dependency graph--an AGP, Kotlin,
# Compose, AndroidX, or test-library version bump, a new dependency, a new
# plugin--requires regenerating this file in the SAME commit, or the build will
# fail verification.
#
# This script wraps the exact generation command so the file is regenerated the
# same way every time. Read it and review the resulting diff before committing.
#
# Requirements (the file cannot be produced or validated without them):
#   * Linux. The merge-gating CI (.github/workflows/build.yml) runs on
#     ubuntu-latest, and some artifacts are OS-classified (chiefly AGP's aapt2,
#     recorded here as aapt2-<ver>-linux.jar). A file generated on macOS or
#     Windows records that platform's classifier instead and fails verification
#     on the Linux CI. Regenerate on Linux to match CI.
#   * A full Android toolchain: JDK 17 and the Android SDK (platform android-35,
#     build-tools 35.0.0, platform-tools), matching build.yml. A JDK-only box
#     cannot resolve the Android dependency graph.
#
# The superset of resolving tasks below covers every configuration the CI
# workflows resolve:
#   * build.yml unit step: assembleDebug, assembleDebugAndroidTest, the two
#     e2e-mock-* assembleDebug tasks, testDebugUnitTest.
#   * build.yml release smoke + release.yml: assembleRelease.
#   * codeql.yml autobuild: assembleDebug (a subset of the above).
# The instrumented/E2E tasks (connectedDebugAndroidTest, connectedE2EAndroidTest)
# add no new external dependencies: the androidTest classpath is the one
# assembleDebugAndroidTest already resolves, and connectedE2EAndroidTest consumes
# already-built APKs. So no emulator is needed to regenerate.
#
# A fresh GRADLE_USER_HOME is used so no previously-cached artifact is silently
# omitted from the generated file (a warm cache can under-record metadata files).
#
# After running: `git diff gradle/verification-metadata.xml`, review the change,
# then let the complete CI build (build.yml, including the instrumented/E2E
# steps) validate it end to end. That is the only way to catch a configuration
# whose dependencies were missed here; on a verification failure Gradle names the
# offending artifact, so re-run this script (it merges into the existing file)
# and repeat until CI is green.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ -z "${ANDROID_HOME:-}${ANDROID_SDK_ROOT:-}" ]; then
    echo "error: ANDROID_HOME (or ANDROID_SDK_ROOT) is not set." >&2
    echo "This script needs a full Android SDK; see the header comment." >&2
    exit 1
fi

# Generate into a throwaway Gradle home so the file records the full graph.
GRADLE_USER_HOME="$(mktemp -d)"
export GRADLE_USER_HOME
trap 'rm -rf "$GRADLE_USER_HOME"' EXIT

echo "==> Regenerating gradle/verification-metadata.xml (GRADLE_USER_HOME=$GRADLE_USER_HOME)"
./gradlew --write-verification-metadata sha256 \
    assembleDebug assembleRelease \
    assembleDebugAndroidTest \
    :e2e-mock-camera:assembleDebug :e2e-mock-gallery:assembleDebug \
    testDebugUnitTest

echo "==> Done. Review the diff before committing:"
echo "      git diff gradle/verification-metadata.xml"
