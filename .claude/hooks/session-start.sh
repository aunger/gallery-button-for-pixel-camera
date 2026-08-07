#!/usr/bin/env bash
# GB4PC: Claude Code for Web session-start hook
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
# Where .claude/setup-environment.sh installs Temurin 17, if that environment
# Setup script is configured (issue #792). Absent on an unconfigured environment.
TEMURIN_HOME=/opt/java/temurin-17

echo "[session-start] GB4PC environment setup..."

# ───────────────────────────────────────────────────────────────────────────────
# STEP 0: Fix JAVA_TOOL_OPTIONS proxy / DNS issue.
#
# The container's JAVA_TOOL_OPTIONS lists *.google.com and *.googleapis.com in
# nonProxyHosts, so Java tries direct connections to those domains, but there
# is no direct DNS resolution for *.google.com in this network.  Stripping
# those entries makes Java route them through the proxy, the same way wget/curl
# already do.  The fixed value is exported via $CLAUDE_ENV_FILE so it persists
# for all subsequent tool invocations in this session.
# ───────────────────────────────────────────────────────────────────────────────
if echo "${JAVA_TOOL_OPTIONS:-}" | grep -qE '\*\.(google|googleapis)\.com'; then
    # Strip each entry independently; order in the container value is not guaranteed.
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
    echo "[session-start] Step 0: JAVA_TOOL_OPTIONS already clean--skip"
fi

# ───────────────────────────────────────────────────────────────────────────────
# STEP 0b: Use the environment's Temurin 17, when it provisioned one.
#
# The base image ships a newer JDK (21), while CI and the generator workflow build
# on Temurin 17, so a local run on 17 is the one that reproduces them.  The JDK is
# installed by .claude/setup-environment.sh, the environment Setup script
# (issue #792), which runs as root before the session and is cached across
# sessions.  That script is optional, so this step is conditional: without it the
# session simply keeps the image's default JDK, exactly as before.
#
# Exported ahead of every Java-using step below (sdkmanager in 2a/2c, and Gradle
# for the rest of the session).
# ───────────────────────────────────────────────────────────────────────────────
if [[ -x "$TEMURIN_HOME/bin/java" ]]; then
    export JAVA_HOME="$TEMURIN_HOME"
    export PATH="$JAVA_HOME/bin:$PATH"
    if [[ -n "${CLAUDE_ENV_FILE:-}" ]] \
            && ! grep -q 'JAVA_HOME' "${CLAUDE_ENV_FILE}" 2>/dev/null; then
        echo "export JAVA_HOME=$TEMURIN_HOME" >> "$CLAUDE_ENV_FILE"
        echo "export PATH=$TEMURIN_HOME/bin:\$PATH" >> "$CLAUDE_ENV_FILE"
    fi
    echo "[session-start] Step 0b: JAVA_HOME=$TEMURIN_HOME"
else
    echo "[session-start] Step 0b: no Setup-script JDK at $TEMURIN_HOME--using the image default"
fi

# ───────────────────────────────────────────────────────────────────────────────
# STEP 1: Gradle proxy authenticator.
#
# Java 9+ no longer auto-registers http.proxyUser/proxyPassword as an
# Authenticator, so proxied HTTPS CONNECT tunnels get HTTP 407.
# ───────────────────────────────────────────────────────────────────────────────
if [[ -f "$GRADLE_INIT" ]]; then
    echo "[session-start] Step 1: Gradle proxy-auth init.d present--skip"
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

# ───────────────────────────────────────────────────────────────────────────────
# STEP 2a: Android SDK command-line tools.
# ───────────────────────────────────────────────────────────────────────────────
if [[ -x "$SDKMANAGER" ]]; then
    echo "[session-start] Step 2a: sdkmanager present--skip"
else
    echo "[session-start] Step 2a: downloading Android command-line tools..."
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

# ───────────────────────────────────────────────────────────────────────────────
# STEP 2b: SDK licenses.
# ───────────────────────────────────────────────────────────────────────────────
if [[ -f "$ANDROID_HOME_DIR/licenses/android-sdk-license" ]]; then
    echo "[session-start] Step 2b: SDK licenses present--skip"
else
    echo "[session-start] Step 2b: writing SDK license files..."
    mkdir -p "$ANDROID_HOME_DIR/licenses"
    printf '\n8933bad161af4178b1185d1a37fbf41ea5269c55\nd56f5187479451eabf01fb78af6dfcb131a6481e\n24333f8a63b6825ea9c5514f83c2829b004d1fee' \
        > "$ANDROID_HOME_DIR/licenses/android-sdk-license"
    printf '\n84831b9409646a918e30573bab4c9c91346d8abd' \
        > "$ANDROID_HOME_DIR/licenses/android-sdk-preview-license"
    echo "[session-start] Step 2b: licenses written"
fi

# ───────────────────────────────────────────────────────────────────────────────
# STEP 2c: SDK packages (only installs what is missing).
# build-tools;36.0.0 is the version AGP 9.1.0 requires, even though the project
# compiles and targets SDK 35. The 35.0.0 and 34.0.0 build-tools stay pinned here
# so a session can still build older revisions of the tree.
# ───────────────────────────────────────────────────────────────────────────────
declare -A SDK_PACKAGES=(
    ["platforms;android-35"]="$ANDROID_HOME_DIR/platforms/android-35"
    ["build-tools;36.0.0"]="$ANDROID_HOME_DIR/build-tools/36.0.0"
    ["build-tools;35.0.0"]="$ANDROID_HOME_DIR/build-tools/35.0.0"
    ["build-tools;34.0.0"]="$ANDROID_HOME_DIR/build-tools/34.0.0"
    ["platform-tools"]="$ANDROID_HOME_DIR/platform-tools"
)

MISSING=()
for pkg in "${!SDK_PACKAGES[@]}"; do
    [[ -d "${SDK_PACKAGES[$pkg]}" ]] \
        && echo "[session-start] Step 2c: $pkg present--skip" \
        || MISSING+=("$pkg")
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo "[session-start] Step 2c: installing: ${MISSING[*]}"
    yes | "$SDKMANAGER" --licenses > /dev/null 2>&1 \
        || echo "[session-start] Step 2c: warning: sdkmanager --licenses failed--install may fail if license is unaccepted"
    "$SDKMANAGER" "${MISSING[@]}"
    echo "[session-start] Step 2c: done"
else
    echo "[session-start] Step 2c: all SDK packages present--skip"
fi

# ───────────────────────────────────────────────────────────────────────────────
# STEP 3: Linting tools (first-party git hook; ktlint + Python tools).
#
# The pre-commit framework used to git-clone each hook's own repository and
# execute that third-party code locally at commit time.  This project no longer
# uses that fetch-and-execute model (issue #667): every lint tool is installed
# here from a trusted package registry (PyPI or Maven Central), pinned to an
# exact version, and (for ktlint) integrity-checked, then run from a checked-in
# git hook (scripts/git-hooks/pre-commit) via scripts/lint/lint.sh.  No hook
# repository is cloned and nothing is fetched from GitHub Releases.
# ───────────────────────────────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOCAL_BIN="$HOME/.local/bin"
mkdir -p "$LOCAL_BIN"

# Ensure ~/.local/bin is on PATH (for ktlint and the Python lint tools).
if [[ ":$PATH:" != *":$LOCAL_BIN:"* ]]; then
    export PATH="$LOCAL_BIN:$PATH"
fi
if [[ -n "${CLAUDE_ENV_FILE:-}" ]] \
        && ! grep -q 'local/bin' "${CLAUDE_ENV_FILE}" 2>/dev/null; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$CLAUDE_ENV_FILE"
fi

# STEP 3a: ktlint (Kotlin formatter), from Maven Central.
#
# The pinned, SHA-256-verified install lives in scripts/lint/install-ktlint.sh, which
# the CI ktlint lint job (.github/workflows/lint.yml) also runs, so both paths
# provision the identical ktlint from one definition.  It installs the wrapper
# into $HOME/.local/bin (the $LOCAL_BIN this step already put on PATH) and is
# idempotent, so re-running is a fast no-op.  See that script for the rationale
# on fetching the fat JAR from Maven Central rather than GitHub Releases.
echo "[session-start] Step 3a: ensuring ktlint is installed..."
"$REPO_ROOT/scripts/lint/install-ktlint.sh"

# STEP 3b: Python lint tools (ruff, pre-commit-hooks checks, mdformat), from PyPI.
#
# Installed from scripts/lint/requirements-lint.txt, a fully resolved lock in which
# every package (top-level and transitive) is pinned to an exact version and a
# SHA-256 hash.  This replaces the pre-commit framework install (issue #667):
# pre-commit-hooks provides the six generic hygiene checks as console scripts,
# ruff lints/formats Python, and mdformat (with its plugins) formats Markdown.
# See requirements-lint.in for the per-package rationale and the top-level pins
# the lock is generated from.
#
# --require-hashes makes pip refuse to install anything whose artifact does not
# match a hash in the lock, so a compromised or substituted wheel on PyPI (or an
# intercepted download) cannot slip in (issue #699).  It also forces every
# dependency to be hash-pinned, which is why the lock lists the full transitive
# closure rather than just the top-level tools.
#
# The install is gated on the SHA-256 of requirements-lint.txt: a marker file
# records the hash that was last installed, so any edit to the pinned versions
# triggers a reinstall on the next session while an unchanged file skips the
# work.  --force-reinstall recreates the console-script entry points even if a
# prior run left a package's dist-info without its scripts (PATH may not include
# $LOCAL_BIN when pip decides whether to write them).
LINT_REQ="$REPO_ROOT/scripts/lint/requirements-lint.txt"
LINT_MARKER="$HOME/.local/share/gb4pc/requirements-lint.sha256"
REQ_SHA=$(sha256sum "$LINT_REQ" | cut -d' ' -f1)
if [[ -f "$LINT_MARKER" && "$(cat "$LINT_MARKER" 2>/dev/null)" == "$REQ_SHA" ]]; then
    echo "[session-start] Step 3b: Python lint tools up to date--skip"
else
    echo "[session-start] Step 3b: installing Python lint tools..."
    pip install --user --force-reinstall --require-hashes --quiet -r "$LINT_REQ"
    mkdir -p "$(dirname "$LINT_MARKER")"
    echo "$REQ_SHA" > "$LINT_MARKER"
    echo "[session-start] Step 3b: Python lint tools installed"
fi

# STEP 3c: wire the first-party git hook into this repo.
#
# core.hooksPath points git at the checked-in scripts/git-hooks directory,
# replacing pre-commit's generated .git/hooks/pre-commit.  It takes precedence
# over .git/hooks, so any stale hook a previous pre-commit install left there is
# ignored.  Idempotent: re-setting the same value is a no-op.
DESIRED_HOOKS_PATH="scripts/git-hooks"
CURRENT_HOOKS_PATH=$(git -C "$REPO_ROOT" config --local --get core.hooksPath || true)
if [[ "$CURRENT_HOOKS_PATH" == "$DESIRED_HOOKS_PATH" ]]; then
    echo "[session-start] Step 3c: git hook wired--skip"
else
    git -C "$REPO_ROOT" config --local core.hooksPath "$DESIRED_HOOKS_PATH"
    echo "[session-start] Step 3c: git hook wired (core.hooksPath=$DESIRED_HOOKS_PATH)"
fi

# ───────────────────────────────────────────────────────────────────────────────
# STEP 4: Fetch remote refs.
#
# Keep local knowledge of the remote up to date at the start of every session.
# git fetch is always safe (it never modifies the working tree), so no skip
# guard is needed.  Failures are logged as warnings rather than aborting the
# hook, so a transient network outage does not prevent the session from starting.
# ───────────────────────────────────────────────────────────────────────────────
echo "[session-start] Step 4: git fetch..."
git -C "$REPO_ROOT" fetch --prune --quiet \
    && echo "[session-start] Step 4: fetch complete" \
    || echo "[session-start] Step 4: warning: git fetch failed"

echo "[session-start] Complete. ANDROID_HOME=$ANDROID_HOME"
