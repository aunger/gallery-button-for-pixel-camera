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
# build-tools;34.0.0 is required by AGP 8.7.3 even though the project targets 35.
# ───────────────────────────────────────────────────────────────────────────────
declare -A SDK_PACKAGES=(
    ["platforms;android-35"]="$ANDROID_HOME_DIR/platforms/android-35"
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
# git hook (scripts/git-hooks/pre-commit) via scripts/lint.sh.  No hook
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
# ktlint is fetched as the ktlint-cli fat JAR from Maven Central--the same
# trusted, immutable, checksummed host Gradle already resolves this build
# from--rather than from GitHub Releases, which this sandbox blocks with a 403
# (issue #667).  The published SHA-256 is verified before the JAR is used, so a
# proxy error page fails loudly here instead of masquerading as a corrupt
# "binary" that only breaks later with an opaque Exec format error.  A small
# wrapper on PATH runs the JAR via `java -jar`.
#
# When bumping KTLINT_VERSION, update KTLINT_SHA256 to the new release's
# published checksum from
# https://repo1.maven.org/maven2/com/pinterest/ktlint/ktlint-cli/<version>/ktlint-cli-<version>-all.jar.sha256
KTLINT_VERSION="1.8.0"
KTLINT_SHA256="369ad2b789f95a011f807e1fcb690ccef80bd7cd014fd139e73ae82dcc0baeab"
KTLINT_JAR_DIR="$HOME/.local/lib/ktlint"
KTLINT_JAR="$KTLINT_JAR_DIR/ktlint-cli-$KTLINT_VERSION-all.jar"
KTLINT_BIN="$LOCAL_BIN/ktlint"
if [[ -x "$KTLINT_BIN" && -f "$KTLINT_JAR" ]]; then
    echo "[session-start] Step 3a: ktlint present--skip"
else
    echo "[session-start] Step 3a: installing ktlint $KTLINT_VERSION from Maven Central..."
    mkdir -p "$KTLINT_JAR_DIR"
    KTLINT_URL="https://repo1.maven.org/maven2/com/pinterest/ktlint/ktlint-cli/$KTLINT_VERSION/ktlint-cli-$KTLINT_VERSION-all.jar"
    TMP_JAR=$(mktemp "$KTLINT_JAR_DIR/.download-XXXXXX")
    trap 'rm -f "$TMP_JAR"' EXIT
    # -f: fail on an HTTP error status instead of writing the error response
    # body to the JAR as if it were the artifact (issue #667).
    curl -fsSL "$KTLINT_URL" -o "$TMP_JAR"
    ACTUAL_SHA=$(sha256sum "$TMP_JAR" | cut -d' ' -f1)
    if [[ "$ACTUAL_SHA" != "$KTLINT_SHA256" ]]; then
        echo "[session-start] Step 3a: ERROR: ktlint SHA-256 mismatch (refusing to install)" >&2
        echo "[session-start]   expected $KTLINT_SHA256" >&2
        echo "[session-start]   actual   $ACTUAL_SHA" >&2
        exit 1
    fi
    mv "$TMP_JAR" "$KTLINT_JAR"
    trap - EXIT
    cat > "$KTLINT_BIN" << EOF
#!/usr/bin/env bash
exec java -jar "$KTLINT_JAR" "\$@"
EOF
    chmod +x "$KTLINT_BIN"
    echo "[session-start] Step 3a: ktlint installed and SHA-256 verified"
fi

# STEP 3b: Python lint tools (ruff, pre-commit-hooks checks, mdformat), from PyPI.
#
# Installed from scripts/requirements-lint.txt, each pinned to an exact version
# on PyPI.  This replaces the pre-commit framework install (issue #667):
# pre-commit-hooks provides the six generic hygiene checks as console scripts,
# ruff lints/formats Python, and mdformat (with its plugins) formats Markdown.
# See requirements-lint.txt for the per-package rationale and pins.
#
# The install is gated on the SHA-256 of requirements-lint.txt: a marker file
# records the hash that was last installed, so any edit to the pinned versions
# triggers a reinstall on the next session while an unchanged file skips the
# work.  --force-reinstall recreates the console-script entry points even if a
# prior run left a package's dist-info without its scripts (PATH may not include
# $LOCAL_BIN when pip decides whether to write them).
LINT_REQ="$REPO_ROOT/scripts/requirements-lint.txt"
LINT_MARKER="$HOME/.local/share/gb4pc/requirements-lint.sha256"
REQ_SHA=$(sha256sum "$LINT_REQ" | cut -d' ' -f1)
if [[ -f "$LINT_MARKER" && "$(cat "$LINT_MARKER" 2>/dev/null)" == "$REQ_SHA" ]]; then
    echo "[session-start] Step 3b: Python lint tools up to date--skip"
else
    echo "[session-start] Step 3b: installing Python lint tools..."
    pip install --user --force-reinstall --quiet -r "$LINT_REQ"
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
