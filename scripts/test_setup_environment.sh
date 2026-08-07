#!/usr/bin/env bash
# test_setup_environment.sh: guard tests for .claude/setup-environment.sh, the
# Claude Code for Web environment Setup script (issue #792).
#
# That script is a copy. The executable original lives in the Claude web
# environment configuration, where CI cannot see it, and it duplicates pins that
# are declared elsewhere in this repository: the Gradle version and distribution
# checksum (gradle/wrapper/gradle-wrapper.properties) and the Android SDK
# provisioning (.claude/hooks/session-start.sh). Duplication that nothing checks
# is duplication that rots, and the failure is quiet: a Gradle bump would leave
# every session seeded with the previous distribution, which the wrapper would
# then silently re-download, undoing the whole point of the script.
#
# These checks make that drift loud at merge time. They cannot see the web form,
# so they guarantee the committed copy is correct, not that it has been pasted;
# re-pasting after a change stays a human step (see .claude/environment.md).
#
# Covers:
#   (a) the script exists, is executable, parses, and runs under set -euo pipefail
#   (b) its Gradle version, distribution URL, and checksum match
#       gradle/wrapper/gradle-wrapper.properties
#   (c) its Android SDK pins (command-line tools URL, ANDROID_HOME, package list,
#       license hashes) match .claude/hooks/session-start.sh
#   (d) its Temurin install path matches the one the session-start hook exports
#       JAVA_HOME to
#   (e) it computes the Gradle wrapper's cache directory the way the wrapper does
#       (behavioral: a correctly seeded cache is detected and no download starts)
#   (f) a checksum mismatch is fatal and installs nothing (behavioral)
#   (g) it never writes gradle/verification-metadata.xml, which only the generator
#       workflow may produce (issue #774)
#
# Always exits 0 on success, non-zero on failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SETUP="$REPO_ROOT/.claude/setup-environment.sh"
HOOK="$REPO_ROOT/.claude/hooks/session-start.sh"
WRAPPER_PROPS="$REPO_ROOT/gradle/wrapper/gradle-wrapper.properties"

# The wrapper cache directory name for the pinned distributionUrl: Gradle's
# base-36 rendering of the MD5 of that URL string. Pinned rather than recomputed,
# because recomputing it with the same algorithm the script uses would assert
# nothing. It was read from a cache that the Gradle wrapper itself populated
# (~/.gradle/wrapper/dists/gradle-9.5.1-bin/<this>/), which is what makes it
# independent evidence.
#
# On a Gradle version bump this value changes with the URL. Re-derive it the same
# way: let ./gradlew download the new distribution once, then read the directory
# name it created under wrapper/dists/gradle-<version>-bin/.
EXPECTED_DIST_HASH="iq79hdu3mqx29lgffhp8bfmx"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

# Read a top-level FOO="bar" assignment out of a script, without executing it.
# An overridable pin is written FOO="${OTHER:-default}"; this returns the default,
# which is the value that ships.
read_var() {
    local file="$1" name="$2" raw
    raw=$(sed -n "s/^${name}=//p" "$file" | head -n 1)
    printf '%s' "$raw" | sed -e 's/^"//' -e 's/"$//' \
        -e 's/^\${[A-Za-z_][A-Za-z0-9_]*:-//' -e 's/}$//'
}

if [ ! -f "$SETUP" ]; then
    echo "  FAIL: .claude/setup-environment.sh is missing"
    echo
    echo "test_setup_environment.sh: 0 passed, 1 failed"
    exit 1
fi

# (a) structural: executable, parses, and fails fast.
if [ -x "$SETUP" ]; then
    pass "setup-environment.sh is executable"
else
    fail "setup-environment.sh must be executable (chmod +x)"
fi

if bash -n "$SETUP" 2>/dev/null; then
    pass "setup-environment.sh parses as bash"
else
    fail "setup-environment.sh has a syntax error"
fi

if grep -q '^set -euo pipefail$' "$SETUP"; then
    pass "setup-environment.sh runs under set -euo pipefail"
else
    fail "setup-environment.sh must set -euo pipefail (a half-provisioned image is worse than none)"
fi

# (b) the Gradle pins agree with the wrapper the repository actually commits.
# distributionUrl is escaped in the .properties format (https\://...), so the
# backslash comes out before comparing.
SETUP_GRADLE_VERSION=$(read_var "$SETUP" GRADLE_VERSION)
SETUP_GRADLE_SHA=$(read_var "$SETUP" GRADLE_DIST_SHA256)
SETUP_GRADLE_URL=$(read_var "$SETUP" GRADLE_DIST_URL)
SETUP_GRADLE_URL="${SETUP_GRADLE_URL//\$\{GRADLE_VERSION\}/$SETUP_GRADLE_VERSION}"

PROPS_URL=$(sed -n 's/^distributionUrl=//p' "$WRAPPER_PROPS" | head -n 1)
PROPS_URL="${PROPS_URL//\\/}"
PROPS_SHA=$(sed -n 's/^distributionSha256Sum=//p' "$WRAPPER_PROPS" | head -n 1)

if [ -n "$SETUP_GRADLE_URL" ] && [ "$SETUP_GRADLE_URL" = "$PROPS_URL" ]; then
    pass "GRADLE_DIST_URL matches gradle-wrapper.properties distributionUrl"
else
    fail "GRADLE_DIST_URL is '$SETUP_GRADLE_URL', gradle-wrapper.properties says '$PROPS_URL'"
fi

if [ -n "$SETUP_GRADLE_SHA" ] && [ "$SETUP_GRADLE_SHA" = "$PROPS_SHA" ]; then
    pass "GRADLE_DIST_SHA256 matches gradle-wrapper.properties distributionSha256Sum"
else
    fail "GRADLE_DIST_SHA256 is '$SETUP_GRADLE_SHA', gradle-wrapper.properties says '$PROPS_SHA'"
fi

case "$PROPS_URL" in
    *"gradle-${SETUP_GRADLE_VERSION}-bin.zip")
        pass "GRADLE_VERSION ($SETUP_GRADLE_VERSION) is the version the wrapper downloads"
        ;;
    *)
        fail "GRADLE_VERSION is '$SETUP_GRADLE_VERSION', which does not name the distribution in '$PROPS_URL'"
        ;;
esac

# (c) the Android SDK pins agree with the session-start hook. Both provision the
# same SDK; the hook per session, the Setup script once into the cached image.
SETUP_TOOLS_URL=$(read_var "$SETUP" CMDLINE_TOOLS_URL)
HOOK_TOOLS_URL=$(read_var "$HOOK" CMDLINE_TOOLS_URL)
if [ -n "$HOOK_TOOLS_URL" ] && [ "$SETUP_TOOLS_URL" = "$HOOK_TOOLS_URL" ]; then
    pass "CMDLINE_TOOLS_URL matches the session-start hook"
else
    fail "CMDLINE_TOOLS_URL is '$SETUP_TOOLS_URL', the hook uses '$HOOK_TOOLS_URL'"
fi

SETUP_ANDROID_HOME=$(read_var "$SETUP" ANDROID_HOME_DIR)
HOOK_ANDROID_HOME=$(sed -n 's/^ANDROID_HOME_DIR=//p' "$HOOK" | head -n 1)
if [ -n "$HOOK_ANDROID_HOME" ] && [ "$SETUP_ANDROID_HOME" = "$HOOK_ANDROID_HOME" ]; then
    pass "ANDROID_HOME_DIR default matches the session-start hook ($HOOK_ANDROID_HOME)"
else
    fail "ANDROID_HOME_DIR is '$SETUP_ANDROID_HOME', the hook uses '$HOOK_ANDROID_HOME'"
fi

# The Setup script lists packages in an indexed array; the hook keys an
# associative array by the same package strings. Compare them as sets.
SETUP_PACKAGES=$(awk '/^SDK_PACKAGES=\(/ { inside = 1; next }
                      inside && /^\)/ { inside = 0 }
                      inside { gsub(/[" \t]/, ""); if ($0 != "") print }' "$SETUP" | sort)
HOOK_PACKAGES=$(sed -n 's/^[[:space:]]*\["\([^"]*\)"\]=.*/\1/p' "$HOOK" | sort)
if [ -n "$HOOK_PACKAGES" ] && [ "$SETUP_PACKAGES" = "$HOOK_PACKAGES" ]; then
    pass "SDK package list matches the session-start hook ($(echo "$SETUP_PACKAGES" | wc -l) packages)"
else
    fail "SDK package list differs from the session-start hook"
    echo "    Setup script: $(echo "$SETUP_PACKAGES" | tr '\n' ' ')"
    echo "    hook:         $(echo "$HOOK_PACKAGES" | tr '\n' ' ')"
fi

# License hashes are 40-hex SHA-1 digests inside the printf lines that write the
# licence files. Scoped to those lines so the 64-hex artifact checksums elsewhere
# in the file cannot match.
SETUP_LICENSES=$(grep 'printf' "$SETUP" | grep -oE '[0-9a-f]{40}' | sort)
HOOK_LICENSES=$(grep 'printf' "$HOOK" | grep -oE '[0-9a-f]{40}' | sort)
if [ -n "$HOOK_LICENSES" ] && [ "$SETUP_LICENSES" = "$HOOK_LICENSES" ]; then
    pass "SDK license hashes match the session-start hook"
else
    fail "SDK license hashes differ from the session-start hook"
fi

# (d) the JDK path the Setup script installs to is the one the hook looks for.
SETUP_TEMURIN=$(read_var "$SETUP" TEMURIN_HOME)
HOOK_TEMURIN=$(sed -n 's/^TEMURIN_HOME=//p' "$HOOK" | head -n 1)
if [ -n "$HOOK_TEMURIN" ] && [ "$SETUP_TEMURIN" = "$HOOK_TEMURIN" ]; then
    pass "TEMURIN_HOME matches the path the session-start hook exports ($HOOK_TEMURIN)"
else
    fail "TEMURIN_HOME is '$SETUP_TEMURIN', the hook looks in '$HOOK_TEMURIN'"
fi

# --- Behavioral checks --------------------------------------------------------
#
# Both run the real script against a sandbox home, with every download URL
# pointed at a nonexistent file:// path. Nothing touches the network: if the
# script tries to download anything it is supposed to have found already, curl
# fails and the case reports it.
SANDBOX=$(mktemp -d "${TMPDIR:-/tmp}/setup-env-test-XXXXXX")
trap 'rm -rf "$SANDBOX"' EXIT

# A stub JDK and SDK, so only the behavior under test is exercised.
make_sandbox() {
    local root="$1" pkg
    mkdir -p "$root/home" "$root/jdk/bin" "$root/sdk/cmdline-tools/latest/bin"
    : > "$root/jdk/bin/java"
    chmod +x "$root/jdk/bin/java"
    : > "$root/sdk/cmdline-tools/latest/bin/sdkmanager"
    chmod +x "$root/sdk/cmdline-tools/latest/bin/sdkmanager"
    mkdir -p "$root/sdk/licenses"
    : > "$root/sdk/licenses/android-sdk-license"
    for pkg in "platforms/android-35" "build-tools/36.0.0" "build-tools/35.0.0" \
        "build-tools/34.0.0" "platform-tools"; do
        mkdir -p "$root/sdk/$pkg"
    done
}

run_setup() {
    local root="$1"
    SESSION_HOME="$root/home" \
        GRADLE_USER_HOME="$root/home/.gradle" \
        ANDROID_HOME="$root/sdk" \
        TEMURIN_HOME="$root/jdk" \
        GRADLE_DIST_DOWNLOAD_URL="file://$root/absent-gradle.zip" \
        TEMURIN_DOWNLOAD_URL="file://$root/absent-jdk.tar.gz" \
        bash "$SETUP" > "$root/out.log" 2>&1
}

# (e) a cache seeded at the path the Gradle wrapper actually uses is recognised.
# The seeded directory name is the independently observed EXPECTED_DIST_HASH, so
# if the script computed the hash differently it would find nothing there, attempt
# the (unreachable) download, and fail.
SEED="$SANDBOX/seed"
make_sandbox "$SEED"
SEED_DIST="$SEED/home/.gradle/wrapper/dists/gradle-${SETUP_GRADLE_VERSION}-bin/$EXPECTED_DIST_HASH"
mkdir -p "$SEED_DIST/gradle-${SETUP_GRADLE_VERSION}/bin"
: > "$SEED_DIST/gradle-${SETUP_GRADLE_VERSION}/bin/gradle"
chmod +x "$SEED_DIST/gradle-${SETUP_GRADLE_VERSION}/bin/gradle"
: > "$SEED_DIST/gradle-${SETUP_GRADLE_VERSION}-bin.zip.ok"
if run_setup "$SEED"; then
    if grep -q "already seeded" "$SEED/out.log"; then
        pass "a cache seeded at the wrapper's own directory ($EXPECTED_DIST_HASH) is detected, no download"
    else
        fail "the script did not report the seeded distribution; it looked somewhere else"
        sed 's/^/    /' "$SEED/out.log"
    fi
else
    fail "the script failed against a correctly seeded cache (wrong cache path?)"
    sed 's/^/    /' "$SEED/out.log"
fi

# (f) fail closed: bytes that do not match the pin must install nothing. The
# distribution here is a valid zip carrying a plausible-looking Gradle tree, so
# only the checksum can reject it.
BAD="$SANDBOX/bad"
make_sandbox "$BAD"
# Structurally a valid distribution, down to the executable bit on bin/gradle, so
# the checksum is the only thing that can reject it. If verification were ever
# removed, this archive would install cleanly and the case below would say so.
python3 - "$BAD/tampered.zip" "gradle-${SETUP_GRADLE_VERSION}" <<'PY'
import sys
import zipfile

archive, dist = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(archive, "w") as zf:
    entry = zipfile.ZipInfo(f"{dist}/bin/gradle")
    entry.external_attr = 0o755 << 16
    zf.writestr(entry, "#!/bin/sh\necho not gradle\n")
PY
if SESSION_HOME="$BAD/home" \
    GRADLE_USER_HOME="$BAD/home/.gradle" \
    ANDROID_HOME="$BAD/sdk" \
    TEMURIN_HOME="$BAD/jdk" \
    GRADLE_DIST_DOWNLOAD_URL="file://$BAD/tampered.zip" \
    bash "$SETUP" > "$BAD/out.log" 2>&1; then
    fail "a distribution failing the SHA-256 pin was accepted"
elif grep -q "SHA-256 mismatch" "$BAD/out.log" \
    && [ ! -d "$BAD/home/.gradle/wrapper/dists/gradle-${SETUP_GRADLE_VERSION}-bin/$EXPECTED_DIST_HASH/gradle-${SETUP_GRADLE_VERSION}" ]; then
    pass "a distribution failing the SHA-256 pin is rejected and nothing is seeded"
else
    fail "the script rejected the distribution, but not for the checksum, or it seeded anyway"
    sed 's/^/    /' "$BAD/out.log"
fi

# (g) the Setup script must never produce gradle/verification-metadata.xml. The
# generator workflow is that file's only provenance (issue #774), and a locally
# generated one would look identical while carrying no review trail.
if grep -q 'verification-metadata\.xml' "$SETUP" \
    && grep 'verification-metadata\.xml' "$SETUP" | grep -qvE '^[[:space:]]*#'; then
    fail "setup-environment.sh acts on verification-metadata.xml; only the generator workflow may"
else
    pass "setup-environment.sh never writes verification-metadata.xml"
fi

echo
echo "test_setup_environment.sh: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
