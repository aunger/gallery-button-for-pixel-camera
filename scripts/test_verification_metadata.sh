#!/usr/bin/env bash
# test_verification_metadata.sh: guard tests for the Gradle build-integrity files
# added in issue #714.
#
# Gradle dependency verification is high blast radius: gradle/verification-metadata.xml
# governs every Gradle invocation, and the value of the control depends on it staying
# present, well-formed, non-empty, and in strict mode. These checks guard against the
# file being accidentally emptied, downgraded to a non-strict configuration, or the
# distribution pin being dropped. They do NOT re-derive checksums (only a real Gradle
# resolve on the full Android toolchain can do that, which is what CI exercises end to
# end); they assert the file's structural invariants.
#
# Covers:
#   (a) gradle/verification-metadata.xml exists and is well-formed XML
#   (b) verify-metadata is "true" (metadata files are pinned)
#   (c) verify-signatures is present and "false" (sha256-only scope, Decision 1)
#   (d) the file actually pins artifacts (at least one <sha256> entry)
#   (e) nothing downgrades or disables verification: no --dependency-verification
#       lenient|off flag in a CI workflow, and no org.gradle.dependency.verification
#       lenient|off property in a committed gradle.properties
#   (f) gradle-wrapper.properties pins the distribution via distributionSha256Sum
#   (g) gradle-wrapper.jar matches the SHA-256 Gradle officially publishes for its
#       wrapper JAR (issue #744): the wrapper JAR is executed before
#       verification-metadata.xml is consulted, so it cannot be covered there; this
#       authenticates it against Gradle instead of trusting whatever bytes are
#       committed, catching a substituted or corrupted wrapper JAR
#
# Always exits 0 on success, non-zero on failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
METADATA="$REPO_ROOT/gradle/verification-metadata.xml"
WRAPPER_PROPS="$REPO_ROOT/gradle/wrapper/gradle-wrapper.properties"
WRAPPER_JAR="$REPO_ROOT/gradle/wrapper/gradle-wrapper.jar"
WORKFLOWS_DIR="$REPO_ROOT/.github/workflows"

# Pinned SHA-256 of gradle/wrapper/gradle-wrapper.jar, authenticated against the
# checksum Gradle officially publishes for its wrapper JAR (the same authoritative
# source GitHub's gradle/wrapper-validation-action verifies against). Pinning the
# published value, rather than the current file's own hash, makes this guard
# authenticate the JAR against Gradle instead of trusting whatever bytes happen to
# be committed. The committed JAR is the genuine Gradle 8.14 wrapper JAR:
#   https://services.gradle.org/distributions/gradle-8.14-wrapper.jar.sha256
# (This is newer than the 8.9 distribution pinned in gradle-wrapper.properties; a
# newer wrapper JAR launches an older distribution without issue.) On a wrapper
# upgrade, update this pin to the published checksum for the new version from the
# gradle-<version>-wrapper.jar.sha256 URL above.
WRAPPER_JAR_SHA256="7d3a4ac4de1c32b59bc6a4eb8ecb8e612ccd0cf1ae1e99f66902da64df296172"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

# (a) exists and is well-formed XML.
if [ ! -f "$METADATA" ]; then
    fail "gradle/verification-metadata.xml is missing"
else
    pass "gradle/verification-metadata.xml exists"
    if python3 -c "import sys, xml.etree.ElementTree as ET; ET.parse(sys.argv[1])" "$METADATA" 2>/dev/null; then
        pass "verification-metadata.xml is well-formed XML"
    else
        fail "verification-metadata.xml is not well-formed XML"
    fi
fi

# (b)-(d) parse configuration and artifact invariants (namespace-agnostic).
if [ -f "$METADATA" ]; then
    read -r VERIFY_METADATA VERIFY_SIGNATURES SHA_COUNT < <(python3 - "$METADATA" <<'PY'
import sys
import xml.etree.ElementTree as ET

root = ET.parse(sys.argv[1]).getroot()


def localname(tag):
    return tag.rsplit("}", 1)[-1]


def find_text(name):
    for el in root.iter():
        if localname(el.tag) == name:
            return (el.text or "").strip()
    return ""


sha_count = sum(1 for el in root.iter() if localname(el.tag) == "sha256")
print(find_text("verify-metadata") or "MISSING",
      find_text("verify-signatures") or "MISSING",
      sha_count)
PY
    )

    if [ "$VERIFY_METADATA" = "true" ]; then
        pass "verify-metadata is true"
    else
        fail "verify-metadata must be true, was '$VERIFY_METADATA'"
    fi

    if [ "$VERIFY_SIGNATURES" = "false" ]; then
        pass "verify-signatures is false (sha256-only scope)"
    else
        fail "verify-signatures must be present and false, was '$VERIFY_SIGNATURES'"
    fi

    if [ "$SHA_COUNT" -gt 0 ] 2>/dev/null; then
        pass "verification-metadata.xml pins $SHA_COUNT sha256 artifacts"
    else
        fail "verification-metadata.xml pins no sha256 artifacts (file emptied?)"
    fi
fi

# (e) nothing downgrades or disables verification. Two paths can weaken it: a
# --dependency-verification lenient|off flag on a gradlew invocation in a CI
# workflow, or the org.gradle.dependency.verification=lenient|off property in a
# committed gradle.properties. The default (neither present) is strict, which is
# what this change requires. In the flag pattern, [= ] already matches the
# "--dependency-verification lenient" space form, so no separate alternation is
# needed.
downgraded=0
if grep -rnE -- "--dependency-verification[= ](lenient|off)" "$WORKFLOWS_DIR" >/dev/null 2>&1; then
    fail "a CI workflow downgrades verification via a --dependency-verification flag"
    downgraded=1
fi
while IFS= read -r props; do
    [ -n "$props" ] || continue
    if grep -nE '^[[:space:]]*org\.gradle\.dependency\.verification[[:space:]]*=[[:space:]]*(lenient|off)' \
        "$REPO_ROOT/$props" >/dev/null 2>&1; then
        fail "committed gradle.properties downgrades verification: $props"
        downgraded=1
    fi
done < <(git -C "$REPO_ROOT" ls-files -- '*gradle.properties' 2>/dev/null)
if [ "$downgraded" -eq 0 ]; then
    pass "nothing downgrades verification (no lenient/off flag or gradle.properties property)"
fi

# (f) the distribution is pinned.
if grep -Eq '^distributionSha256Sum=[0-9a-f]{64}$' "$WRAPPER_PROPS"; then
    pass "gradle-wrapper.properties pins the distribution via distributionSha256Sum"
else
    fail "gradle-wrapper.properties is missing a valid distributionSha256Sum pin"
fi

# (g) the committed wrapper JAR matches its pinned SHA-256. gradlew reads and runs
# this JAR before gradle/verification-metadata.xml is ever consulted (issue #714,
# Decision 3), so dependency verification cannot cover it. This first-party check
# mirrors the pinned-checksum compare in scripts/lint/install-ktlint.sh so a substituted
# or corrupted wrapper JAR is caught rather than silently trusted (issue #744).
if [ ! -f "$WRAPPER_JAR" ]; then
    fail "gradle/wrapper/gradle-wrapper.jar is missing"
else
    ACTUAL_WRAPPER_SHA=$(sha256sum "$WRAPPER_JAR" | cut -d' ' -f1)
    if [ "$ACTUAL_WRAPPER_SHA" = "$WRAPPER_JAR_SHA256" ]; then
        pass "gradle-wrapper.jar matches its pinned SHA-256"
    else
        fail "gradle-wrapper.jar SHA-256 mismatch: expected $WRAPPER_JAR_SHA256, got $ACTUAL_WRAPPER_SHA"
    fi
fi

echo
echo "test_verification_metadata.sh: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
