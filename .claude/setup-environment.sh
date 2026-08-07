#!/usr/bin/env bash
# GB4PC: Claude Code for Web *environment* Setup script (issue #792).
#
# WHERE THIS RUNS
#
# Nothing in this repository executes this file. It is the checked-in copy of the
# block the maintainer pastes into the Setup script field of the Claude Code for
# Web environment configuration, which runs as root once per environment build,
# before any session starts, with its filesystem output cached across sessions.
# The copy lives here so the pins can be reviewed, diffed, and guarded by CI
# (scripts/test_setup_environment.sh) instead of existing only inside a web form.
# See .claude/environment.md for how to install it and when to re-paste it.
#
# WHAT IT ADDS OVER THE SESSION-START HOOK
#
# .claude/hooks/session-start.sh already provisions the Android SDK, but it runs
# inside each session, so every session pays that cost again. This script does the
# same provisioning once, into the cached image, and adds the two things the hook
# cannot usefully do per session:
#
#   - the Gradle distribution itself, seeded into the wrapper cache so ./gradlew
#     starts offline instead of depending on the session proxy allowing
#     services.gradle.org (observed blocked in 2026-07 sessions and open in
#     2026-08 ones; see issue #774's comments);
#   - Temurin 17, so local validation runs the JDK that CI and the generator
#     workflow use rather than whatever JDK the base image ships (21, at the time
#     of writing).
#
# The hook stays authoritative for anything session-scoped (environment variables,
# the proxy authenticator, lint tooling, the git hook). It is written to skip work
# that is already done, so with this script in place its SDK steps become fast
# no-ops, and without this script it still provisions a working session on its own.
# Nothing here is required for a session to function; this only makes it
# deterministic and fast.
#
# WHAT IT IS NOT
#
# It never regenerates or commits gradle/verification-metadata.xml (the generator
# workflow is the provenance source, issue #774), and it does not provision an
# emulator: the E2E suites need KVM, which web sessions do not have, so they stay
# CI-only.
#
# INTEGRITY
#
# Every download is checked against a pinned SHA-256 and the script refuses to
# install on a mismatch. The URLs are overridable (GRADLE_DIST_DOWNLOAD_URL,
# TEMURIN_DOWNLOAD_URL) for an environment that blocks the canonical hosts, but
# the checksum is not: a mirror is only ever trusted through the pin below, never
# through a hash the mirror publishes about itself.
#
# KEEPING THE PINS IN SYNC
#
# GRADLE_VERSION and GRADLE_DIST_SHA256 must match
# gradle/wrapper/gradle-wrapper.properties, and the Android SDK pins must match
# .claude/hooks/session-start.sh. scripts/test_setup_environment.sh asserts all of
# that in CI, so a toolchain bump that forgets this file fails the build rather
# than silently seeding the wrong Gradle. After changing this file, re-paste it
# into the environment configuration; CI cannot see what is in the web form.
#
# Idempotent: every block checks whether its work is already done, so a re-run on a
# partly provisioned image is safe and fast.

set -euo pipefail

# --- Pins ---------------------------------------------------------------------
#
# Gradle: must match gradle/wrapper/gradle-wrapper.properties. The checksum is the
# one Gradle publishes at
# https://services.gradle.org/distributions/gradle-<version>-bin.zip.sha256
GRADLE_VERSION="9.5.1"
GRADLE_DIST_SHA256="bafc141b619ad6350fd975fc903156dd5c151998cc8b058e8c1044ab5f7b031f"

# The URL the *wrapper* is configured with (distributionUrl in
# gradle-wrapper.properties). The wrapper derives its cache directory name from
# this exact string, so the seeded path is computed from it and never from a
# mirror override.
GRADLE_DIST_URL="https://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip"

# Where the bytes are actually fetched from. Defaults to the wrapper's own URL;
# override only if that host is blocked here. GRADLE_DIST_SHA256 gates it either way.
GRADLE_DIST_DOWNLOAD_URL="${GRADLE_DIST_DOWNLOAD_URL:-$GRADLE_DIST_URL}"

# Temurin 17: matches the JDK actions/setup-java provisions in CI and in the
# generator workflow. Checksum from the Adoptium release's .sha256.txt.
TEMURIN_VERSION="17.0.20+8"
TEMURIN_SHA256="be7668bc030d578b83d6d5ef9221d6d6729bbbca8cf94a7d52e16ac68b5a5a35"
# Fixed path, because .claude/hooks/session-start.sh looks for exactly this one
# when it decides whether to point the session's JAVA_HOME here. Overridable only
# so scripts/test_setup_environment.sh can exercise the script against a sandbox.
TEMURIN_HOME="${TEMURIN_HOME:-/opt/java/temurin-17}"

# Android SDK: must match .claude/hooks/session-start.sh.
ANDROID_HOME_DIR="${ANDROID_HOME:-/home/user/android-sdk}"
CMDLINE_TOOLS_URL="https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"
SDK_PACKAGES=(
    "platforms;android-35"
    "build-tools;36.0.0"
    "build-tools;35.0.0"
    "build-tools;34.0.0"
    "platform-tools"
)

# The home directory sessions run with. The Gradle wrapper looks for its cached
# distribution under this home, so seeding the wrong one provisions nothing.
SESSION_HOME="${SESSION_HOME:-$HOME}"
GRADLE_USER_HOME_DIR="${GRADLE_USER_HOME:-$SESSION_HOME/.gradle}"

log() { echo "[setup-environment] $*"; }

# --- Preflight ----------------------------------------------------------------
MISSING_TOOLS=()
for tool in curl unzip tar sha256sum python3 stat; do
    command -v "$tool" >/dev/null 2>&1 || MISSING_TOOLS+=("$tool")
done
if [[ ${#MISSING_TOOLS[@]} -gt 0 ]]; then
    log "ERROR: missing required commands: ${MISSING_TOOLS[*]}" >&2
    exit 1
fi

# Download to a caller-named path and refuse to proceed unless the bytes match the
# pinned SHA-256. -f makes curl fail on an HTTP error status instead of saving a
# proxy error page as if it were the artifact (issue #667).
download_verified() {
    local url="$1" expected_sha="$2" dest="$3" label="$4"
    log "$label: downloading $url"
    if ! curl -fsSL "$url" -o "$dest"; then
        log "ERROR: $label: download failed from $url" >&2
        log "       If this host is blocked here, re-run with a mirror URL; the" >&2
        log "       pinned SHA-256 still gates whatever that mirror serves." >&2
        return 1
    fi
    local actual_sha
    actual_sha=$(sha256sum "$dest" | cut -d' ' -f1)
    if [[ "$actual_sha" != "$expected_sha" ]]; then
        log "ERROR: $label: SHA-256 mismatch (refusing to install)" >&2
        log "       expected $expected_sha" >&2
        log "       actual   $actual_sha" >&2
        return 1
    fi
    log "$label: SHA-256 verified"
}

# --- STEP 1: Temurin 17 -------------------------------------------------------
#
# The base image ships a newer JDK; the build targets Java 17 and CI runs Temurin
# 17, so a local run on 17 is the one that reproduces CI. Installed to a fixed
# path that .claude/hooks/session-start.sh exports JAVA_HOME to when it exists.
if [[ -x "$TEMURIN_HOME/bin/java" ]]; then
    log "Step 1: Temurin $TEMURIN_VERSION present--skip"
else
    # jdk-17.0.20+8 -> tag jdk-17.0.20%2B8, file ...hotspot_17.0.20_8.tar.gz.
    # Derived from TEMURIN_VERSION so the version appears exactly once.
    TEMURIN_TAG="jdk-${TEMURIN_VERSION//+/%2B}"
    TEMURIN_FILE="OpenJDK17U-jdk_x64_linux_hotspot_${TEMURIN_VERSION//+/_}.tar.gz"
    TEMURIN_DOWNLOAD_URL="${TEMURIN_DOWNLOAD_URL:-https://github.com/adoptium/temurin17-binaries/releases/download/${TEMURIN_TAG}/${TEMURIN_FILE}}"

    TMP_JDK=$(mktemp /tmp/temurin17-XXXXXX.tar.gz)
    TMP_JDK_DIR=$(mktemp -d /tmp/temurin17-extract-XXXXXX)
    trap 'rm -rf "$TMP_JDK" "$TMP_JDK_DIR"' EXIT
    download_verified "$TEMURIN_DOWNLOAD_URL" "$TEMURIN_SHA256" "$TMP_JDK" "Step 1"
    tar -xzf "$TMP_JDK" -C "$TMP_JDK_DIR"
    # The tarball unpacks to a single jdk-<version> directory; move it into place
    # under a stable name rather than encoding the version in the path.
    EXTRACTED_JDK=$(find "$TMP_JDK_DIR" -maxdepth 1 -mindepth 1 -type d | head -n 1)
    if [[ -z "$EXTRACTED_JDK" || ! -x "$EXTRACTED_JDK/bin/java" ]]; then
        log "ERROR: Step 1: the unpacked archive has no bin/java" >&2
        exit 1
    fi
    mkdir -p "$(dirname "$TEMURIN_HOME")"
    rm -rf "$TEMURIN_HOME"
    mv "$EXTRACTED_JDK" "$TEMURIN_HOME"
    rm -rf "$TMP_JDK" "$TMP_JDK_DIR"
    trap - EXIT
    log "Step 1: Temurin installed to $TEMURIN_HOME"
fi

# Use the pinned JDK for the rest of this script (sdkmanager is a Java program).
export JAVA_HOME="$TEMURIN_HOME"
export PATH="$JAVA_HOME/bin:$PATH"

# The image's JAVA_TOOL_OPTIONS lists *.google.com in nonProxyHosts, so Java tries
# a direct connection to dl.google.com, which has no direct DNS here. Strip those
# entries for this script's sdkmanager runs, exactly as the session-start hook
# does for the session (see its STEP 0).
if echo "${JAVA_TOOL_OPTIONS:-}" | grep -qE '\*\.(google|googleapis)\.com'; then
    JAVA_TOOL_OPTIONS=$(echo "$JAVA_TOOL_OPTIONS" \
        | sed 's/|\*\.googleapis\.com//' \
        | sed 's/|\*\.google\.com//')
    export JAVA_TOOL_OPTIONS
    log "Stripped *.google.com from nonProxyHosts for this script"
fi

# --- STEP 2: Gradle distribution, seeded into the wrapper cache ---------------
#
# ./gradlew resolves its distribution to
#   <GRADLE_USER_HOME>/wrapper/dists/<dist name>/<hash>/<unpacked dir>/
# where <hash> is Gradle's base-36 MD5 of the configured distributionUrl. Writing
# the unpacked distribution there, plus the .ok marker the wrapper writes on a
# successful install, makes the wrapper find the distribution already installed
# and skip the download entirely.
#
# The hash is derived from GRADLE_DIST_URL (what the wrapper is configured with),
# never from a mirror override, or the wrapper would look in a different directory
# than the one seeded.
#
# Note what seeding costs: the wrapper normally verifies the zip it downloaded
# against distributionSha256Sum, but a distribution it finds already installed is
# taken as given, and it deletes the zip after unpacking so there is nothing left
# to re-check. The verification below is therefore not belt-and-braces, it is the
# only check that stands between a substituted distribution and every build in
# every session, which is why it is fail-closed and why the pin lives in the
# script rather than being read from whatever was downloaded.
DIST_BASE="${GRADLE_DIST_URL##*/}"          # gradle-9.5.1-bin.zip
DIST_NAME="${DIST_BASE%.zip}"               # gradle-9.5.1-bin
DIST_HASH=$(python3 -c '
import hashlib
import sys

digest = hashlib.md5(sys.argv[1].encode()).digest()
value = int.from_bytes(digest, "big")
digits = "0123456789abcdefghijklmnopqrstuvwxyz"
out = ""
while value:
    value, remainder = divmod(value, 36)
    out = digits[remainder] + out
print(out or "0")
' "$GRADLE_DIST_URL")
DIST_DIR="$GRADLE_USER_HOME_DIR/wrapper/dists/$DIST_NAME/$DIST_HASH"
DIST_MARKER="$DIST_DIR/$DIST_BASE.ok"
DIST_UNPACKED="$DIST_DIR/gradle-$GRADLE_VERSION"

if [[ -x "$DIST_UNPACKED/bin/gradle" && -f "$DIST_MARKER" ]]; then
    log "Step 2: Gradle $GRADLE_VERSION already seeded at $DIST_DIR--skip"
else
    TMP_DIST=$(mktemp /tmp/gradle-dist-XXXXXX.zip)
    TMP_DIST_DIR=$(mktemp -d /tmp/gradle-dist-extract-XXXXXX)
    trap 'rm -rf "$TMP_DIST" "$TMP_DIST_DIR"' EXIT
    download_verified "$GRADLE_DIST_DOWNLOAD_URL" "$GRADLE_DIST_SHA256" "$TMP_DIST" "Step 2"
    unzip -q "$TMP_DIST" -d "$TMP_DIST_DIR"
    if [[ ! -x "$TMP_DIST_DIR/gradle-$GRADLE_VERSION/bin/gradle" ]]; then
        log "ERROR: Step 2: the archive has no gradle-$GRADLE_VERSION/bin/gradle" >&2
        exit 1
    fi
    # Replace any half-seeded directory rather than merging into it, so an
    # interrupted earlier run cannot leave a mixed distribution behind.
    rm -rf "$DIST_DIR"
    mkdir -p "$DIST_DIR"
    mv "$TMP_DIST_DIR/gradle-$GRADLE_VERSION" "$DIST_UNPACKED"
    # The marker is written last: it is what tells the wrapper the install
    # completed, so it must not exist before the distribution is fully in place.
    # The wrapper deletes the zip once it has unpacked it, so a correctly seeded
    # cache holds the unpacked distribution and this marker, and no archive.
    touch "$DIST_MARKER"
    rm -rf "$TMP_DIST" "$TMP_DIST_DIR"
    trap - EXIT
    log "Step 2: Gradle $GRADLE_VERSION seeded at $DIST_DIR"
fi

# --- STEP 3: Android SDK ------------------------------------------------------
#
# Same command-line tools, licenses, and package pins as
# .claude/hooks/session-start.sh (its STEP 2a-2c); doing it here puts them in the
# cached image so sessions find them already installed.
#
# No checksum pin here, unlike the two downloads above, and the asymmetry is
# deliberate rather than an oversight: Google publishes no stable checksum for
# these, and the packages sdkmanager then fetches are verified by sdkmanager
# against its own repository manifest. What is pinned is the version of every
# component (issue #774 records the same reasoning for the generator workflow).
SDKMANAGER="$ANDROID_HOME_DIR/cmdline-tools/latest/bin/sdkmanager"

if [[ -x "$SDKMANAGER" ]]; then
    log "Step 3a: sdkmanager present--skip"
else
    TMP_TOOLS=$(mktemp /tmp/cmdline-tools-XXXXXX.zip)
    TMP_TOOLS_DIR=$(mktemp -d /tmp/cmdline-tools-extract-XXXXXX)
    trap 'rm -rf "$TMP_TOOLS" "$TMP_TOOLS_DIR"' EXIT
    log "Step 3a: downloading the Android command-line tools..."
    curl -fsSL "$CMDLINE_TOOLS_URL" -o "$TMP_TOOLS"
    unzip -q "$TMP_TOOLS" -d "$TMP_TOOLS_DIR"
    mkdir -p "$ANDROID_HOME_DIR/cmdline-tools"
    rm -rf "$ANDROID_HOME_DIR/cmdline-tools/latest"
    mv "$TMP_TOOLS_DIR/cmdline-tools" "$ANDROID_HOME_DIR/cmdline-tools/latest"
    rm -rf "$TMP_TOOLS" "$TMP_TOOLS_DIR"
    trap - EXIT
    log "Step 3a: cmdline-tools installed"
fi

export ANDROID_HOME="$ANDROID_HOME_DIR"

if [[ -f "$ANDROID_HOME_DIR/licenses/android-sdk-license" ]]; then
    log "Step 3b: SDK licenses present--skip"
else
    mkdir -p "$ANDROID_HOME_DIR/licenses"
    printf '\n8933bad161af4178b1185d1a37fbf41ea5269c55\nd56f5187479451eabf01fb78af6dfcb131a6481e\n24333f8a63b6825ea9c5514f83c2829b004d1fee' \
        > "$ANDROID_HOME_DIR/licenses/android-sdk-license"
    printf '\n84831b9409646a918e30573bab4c9c91346d8abd' \
        > "$ANDROID_HOME_DIR/licenses/android-sdk-preview-license"
    log "Step 3b: licenses written"
fi

MISSING_PACKAGES=()
for pkg in "${SDK_PACKAGES[@]}"; do
    # "build-tools;36.0.0" installs to $ANDROID_HOME/build-tools/36.0.0.
    if [[ -d "$ANDROID_HOME_DIR/${pkg//;//}" ]]; then
        log "Step 3c: $pkg present--skip"
    else
        MISSING_PACKAGES+=("$pkg")
    fi
done

if [[ ${#MISSING_PACKAGES[@]} -gt 0 ]]; then
    log "Step 3c: installing: ${MISSING_PACKAGES[*]}"
    yes | "$SDKMANAGER" --licenses > /dev/null 2>&1 \
        || log "Step 3c: warning: sdkmanager --licenses failed--install may fail if a license is unaccepted"
    "$SDKMANAGER" "${MISSING_PACKAGES[@]}"
    log "Step 3c: done"
else
    log "Step 3c: all SDK packages present--skip"
fi

# --- STEP 4: Ownership --------------------------------------------------------
#
# This script runs as root. Anything it leaves root-owned is unusable by a session
# running as another user, and Gradle in particular must be able to write inside
# its user home. Match whatever owns the session home rather than naming a user:
# when sessions also run as root this is a no-op.
OWNER=$(stat -c '%u:%g' "$SESSION_HOME")
chown -R "$OWNER" "$GRADLE_USER_HOME_DIR" "$ANDROID_HOME_DIR" "$TEMURIN_HOME"
log "Step 4: ownership of the provisioned trees set to $OWNER"

log "Complete. JAVA_HOME=$TEMURIN_HOME ANDROID_HOME=$ANDROID_HOME_DIR"
log "Gradle $GRADLE_VERSION seeded in $GRADLE_USER_HOME_DIR"
