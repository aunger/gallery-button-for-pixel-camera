#!/usr/bin/env bash
# GB4PC — Claude Code for Web session-start hook
#
# Idempotent: every block checks whether its work is already done before
# doing it, so re-running the hook (or running it on a container that already
# has some steps completed) is safe and fast.
#
# See .claude/environment.md for the full explanation of each step.

set -euo pipefail

# Only run in remote (Claude Code for Web) environments.
if [[ "${CLAUDE_CODE_REMOTE:-}" != "true" ]]; then
    exit 0
fi

ANDROID_HOME_DIR=/home/user/android-sdk
CMDLINE_TOOLS_URL="https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"
SDKMANAGER="$ANDROID_HOME_DIR/cmdline-tools/latest/bin/sdkmanager"
GRADLE_INIT="$HOME/.gradle/init.d/proxy-auth.gradle"

echo "[session-start] GB4PC environment setup…"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 0 — Fix JAVA_TOOL_OPTIONS proxy / DNS issue.
#
# The container's JAVA_TOOL_OPTIONS lists *.google.com and *.googleapis.com in
# nonProxyHosts, so Java tries direct connections to those domains — but there
# is no direct DNS resolution for *.google.com in this network.  Stripping
# those entries makes Java route them through the proxy, the same way wget/curl
# already do.  The fixed value is exported via $CLAUDE_ENV_FILE so it persists
# for all subsequent tool invocations in this session.
# ─────────────────────────────────────────────────────────────────────────────
if echo "${JAVA_TOOL_OPTIONS:-}" | grep -qE '\*\.(google|googleapis)\.com'; then
    # Strip each entry independently — order in the container value is not guaranteed.
    FIXED_JTO=$(echo "$JAVA_TOOL_OPTIONS" \
        | sed 's/|\*\.googleapis\.com//' \
        | sed 's/|\*\.google\.com//')
    export JAVA_TOOL_OPTIONS="$FIXED_JTO"
    if [[ -n "${CLAUDE_ENV_FILE:-}" ]] \
            && ! grep -q 'JAVA_TOOL_OPTIONS' "${CLAUDE_ENV_FILE}" 2>/dev/null; then
        echo "export JAVA_TOOL_OPTIONS=$(printf '%q' "$FIXED_JTO")" >> "$CLAUDE_ENV_FILE"
    fi
    echo "[session-start] Step 0: stripped *.google.com from nonProxyHosts"
else
    echo "[session-start] Step 0: JAVA_TOOL_OPTIONS already clean — skip"
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Gradle proxy authenticator.
#
# Java 9+ no longer auto-registers http.proxyUser/proxyPassword as an
# Authenticator, so proxied HTTPS CONNECT tunnels get HTTP 407.
# ─────────────────────────────────────────────────────────────────────────────
if [[ -f "$GRADLE_INIT" ]]; then
    echo "[session-start] Step 1: Gradle proxy-auth init.d present — skip"
else
    mkdir -p "$(dirname "$GRADLE_INIT")"
    cat > "$GRADLE_INIT" << 'GROOVY'
import java.net.Authenticator
import java.net.PasswordAuthentication

String proxyUser = System.getProperty("http.proxyUser") ?: ""
String proxyPass = System.getProperty("http.proxyPassword") ?: ""
if (proxyUser) {
    Authenticator.setDefault(new Authenticator() {
        @Override
        protected PasswordAuthentication getPasswordAuthentication() {
            return new PasswordAuthentication(proxyUser, proxyPass.toCharArray())
        }
    })
}
GROOVY
    echo "[session-start] Step 1: Gradle proxy-auth init.d written"
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2a — Android SDK command-line tools.
# ─────────────────────────────────────────────────────────────────────────────
if [[ -x "$SDKMANAGER" ]]; then
    echo "[session-start] Step 2a: sdkmanager present — skip"
else
    echo "[session-start] Step 2a: downloading Android command-line tools…"
    TMP_ZIP=$(mktemp /tmp/cmdline-tools-XXXXXX.zip)
    TMP_EXTRACT=$(mktemp -d /tmp/cmdline-tools-extract-XXXXXX)
    # Clean up temp files and any partial extraction on failure.
    trap 'rm -rf "$TMP_ZIP" "$TMP_EXTRACT"' EXIT
    wget -q "$CMDLINE_TOOLS_URL" -O "$TMP_ZIP"
    unzip -q "$TMP_ZIP" -d "$TMP_EXTRACT"
    mkdir -p "$ANDROID_HOME_DIR/cmdline-tools"
    mv "$TMP_EXTRACT/cmdline-tools" "$ANDROID_HOME_DIR/cmdline-tools/latest"
    rm -rf "$TMP_ZIP" "$TMP_EXTRACT"
    trap - EXIT
    echo "[session-start] Step 2a: cmdline-tools installed"
fi

export ANDROID_HOME="$ANDROID_HOME_DIR"
export PATH="$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools"
if [[ -n "${CLAUDE_ENV_FILE:-}" ]] \
        && ! grep -q 'ANDROID_HOME' "${CLAUDE_ENV_FILE}" 2>/dev/null; then
    echo "export ANDROID_HOME=$ANDROID_HOME_DIR" >> "$CLAUDE_ENV_FILE"
    echo "export PATH=\$PATH:$ANDROID_HOME_DIR/cmdline-tools/latest/bin:$ANDROID_HOME_DIR/platform-tools" \
        >> "$CLAUDE_ENV_FILE"
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2b — SDK licenses.
# ─────────────────────────────────────────────────────────────────────────────
if [[ -f "$ANDROID_HOME_DIR/licenses/android-sdk-license" ]]; then
    echo "[session-start] Step 2b: SDK licenses present — skip"
else
    echo "[session-start] Step 2b: writing SDK license files…"
    mkdir -p "$ANDROID_HOME_DIR/licenses"
    printf '\n8933bad161af4178b1185d1a37fbf41ea5269c55\nd56f5187479451eabf01fb78af6dfcb131a6481e\n24333f8a63b6825ea9c5514f83c2829b004d1fee' \
        > "$ANDROID_HOME_DIR/licenses/android-sdk-license"
    printf '\n84831b9409646a918e30573bab4c9c91346d8abd' \
        > "$ANDROID_HOME_DIR/licenses/android-sdk-preview-license"
    echo "[session-start] Step 2b: licenses written"
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2c — SDK packages (only installs what is missing).
# build-tools;34.0.0 is required by AGP 8.7.3 even though the project targets 35.
# ─────────────────────────────────────────────────────────────────────────────
declare -A SDK_PACKAGES=(
    ["platforms;android-35"]="$ANDROID_HOME_DIR/platforms/android-35"
    ["build-tools;35.0.0"]="$ANDROID_HOME_DIR/build-tools/35.0.0"
    ["build-tools;34.0.0"]="$ANDROID_HOME_DIR/build-tools/34.0.0"
    ["platform-tools"]="$ANDROID_HOME_DIR/platform-tools"
)

MISSING=()
for pkg in "${!SDK_PACKAGES[@]}"; do
    [[ -d "${SDK_PACKAGES[$pkg]}" ]] \
        && echo "[session-start] Step 2c: $pkg present — skip" \
        || MISSING+=("$pkg")
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo "[session-start] Step 2c: installing: ${MISSING[*]}"
    yes | "$SDKMANAGER" --licenses > /dev/null 2>&1 \
        || echo "[session-start] Step 2c: warning: sdkmanager --licenses failed — install may fail if license is unaccepted"
    "$SDKMANAGER" "${MISSING[@]}"
    echo "[session-start] Step 2c: done"
else
    echo "[session-start] Step 2c: all SDK packages present — skip"
fi

echo "[session-start] Complete. ANDROID_HOME=$ANDROID_HOME"
