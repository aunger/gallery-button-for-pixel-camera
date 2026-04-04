#!/usr/bin/env bash
# GB4PC — Claude Code for Web session-start hook
# Idempotent: safe to run multiple times; each block is guarded by a condition.
# See .claude/environment.md for the full explanation of each step.

set -euo pipefail

ANDROID_HOME_DIR=/home/user/android-sdk
CMDLINE_TOOLS_URL="https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"
SDKMANAGER="$ANDROID_HOME_DIR/cmdline-tools/latest/bin/sdkmanager"
GRADLE_INIT_SCRIPT="$HOME/.gradle/init.d/proxy-auth.gradle"

echo "[session-start] GB4PC environment setup starting…"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 0 — Fix JAVA_TOOL_OPTIONS so Java routes *.google.com through the proxy.
#
# The container injects nonProxyHosts containing *.google.com / *.googleapis.com,
# but there is no direct DNS for those in this network. Strip those entries so
# Java uses the proxy (same as wget/curl already do).
# ─────────────────────────────────────────────────────────────────────────────
if echo "${JAVA_TOOL_OPTIONS:-}" | grep -q '\*\.google\.com'; then
    export JAVA_TOOL_OPTIONS
    JAVA_TOOL_OPTIONS=$(echo "$JAVA_TOOL_OPTIONS" \
        | sed 's/|\*\.googleapis\.com|\*\.google\.com//')
    echo "[session-start] Step 0: stripped *.google.com from JAVA_TOOL_OPTIONS nonProxyHosts"
else
    echo "[session-start] Step 0: JAVA_TOOL_OPTIONS already clean — skipping"
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Gradle proxy authenticator init script.
#
# Java 9+ dropped auto-registration of http.proxyUser/http.proxyPassword as an
# Authenticator, so HTTPS CONNECT tunnels get 407. This init.d script registers
# one at Gradle startup, reading the same system properties.
# ─────────────────────────────────────────────────────────────────────────────
if [[ -f "$GRADLE_INIT_SCRIPT" ]]; then
    echo "[session-start] Step 1: Gradle proxy-auth init.d already present — skipping"
else
    mkdir -p "$(dirname "$GRADLE_INIT_SCRIPT")"
    cat > "$GRADLE_INIT_SCRIPT" << 'GROOVY'
import java.net.Authenticator
import java.net.PasswordAuthentication

// Java 9+: http.proxyUser/proxyPassword are NOT auto-registered as an Authenticator.
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
    echo "[session-start] Step 1: Gradle proxy-auth init.d written to $GRADLE_INIT_SCRIPT"
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — Android SDK: command-line tools
# ─────────────────────────────────────────────────────────────────────────────
if [[ -x "$SDKMANAGER" ]]; then
    echo "[session-start] Step 2a: sdkmanager already present — skipping cmdline-tools download"
else
    echo "[session-start] Step 2a: downloading Android command-line tools…"
    mkdir -p "$ANDROID_HOME_DIR/cmdline-tools"
    TMP_ZIP=$(mktemp /tmp/cmdline-tools-XXXXXX.zip)
    wget -q "$CMDLINE_TOOLS_URL" -O "$TMP_ZIP"
    unzip -q "$TMP_ZIP" -d "$ANDROID_HOME_DIR/cmdline-tools"
    mv "$ANDROID_HOME_DIR/cmdline-tools/cmdline-tools" \
       "$ANDROID_HOME_DIR/cmdline-tools/latest"
    rm "$TMP_ZIP"
    echo "[session-start] Step 2a: cmdline-tools installed"
fi

export ANDROID_HOME="$ANDROID_HOME_DIR"
export PATH="$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2b — SDK licenses
# ─────────────────────────────────────────────────────────────────────────────
LICENSE_FILE="$ANDROID_HOME_DIR/licenses/android-sdk-license"
if [[ -f "$LICENSE_FILE" ]]; then
    echo "[session-start] Step 2b: SDK licenses already accepted — skipping"
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
# STEP 2c — SDK packages
# Install each package only if its directory is absent.
# build-tools;34.0.0 is required by AGP 8.7.3 in addition to the project's 35.
# ─────────────────────────────────────────────────────────────────────────────
declare -A SDK_PACKAGES=(
    ["platforms;android-35"]="$ANDROID_HOME_DIR/platforms/android-35"
    ["build-tools;35.0.0"]="$ANDROID_HOME_DIR/build-tools/35.0.0"
    ["build-tools;34.0.0"]="$ANDROID_HOME_DIR/build-tools/34.0.0"
    ["platform-tools"]="$ANDROID_HOME_DIR/platform-tools"
)

MISSING_PACKAGES=()
for pkg in "${!SDK_PACKAGES[@]}"; do
    dir="${SDK_PACKAGES[$pkg]}"
    if [[ -d "$dir" ]]; then
        echo "[session-start] Step 2c: $pkg already installed — skipping"
    else
        MISSING_PACKAGES+=("$pkg")
    fi
done

if [[ ${#MISSING_PACKAGES[@]} -gt 0 ]]; then
    echo "[session-start] Step 2c: installing missing SDK packages: ${MISSING_PACKAGES[*]}"
    yes | "$SDKMANAGER" --licenses > /dev/null 2>&1 || true
    "$SDKMANAGER" "${MISSING_PACKAGES[@]}"
    echo "[session-start] Step 2c: SDK packages installed"
else
    echo "[session-start] Step 2c: all SDK packages present — skipping sdkmanager"
fi

echo "[session-start] Done. ANDROID_HOME=$ANDROID_HOME"
