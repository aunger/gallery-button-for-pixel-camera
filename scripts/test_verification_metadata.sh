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
# If a check here fails during a version bump, see "Performing a toolchain bump"
# in gradle/README.md: the constants below are duplicated in the generator
# workflow, and a bump has to move both.
#
# Covers:
#   (a) gradle/verification-metadata.xml exists and is well-formed XML
#   (b) verify-metadata is "true" (metadata files are pinned)
#   (c) verify-signatures is present and "false" (sha256-only scope, Decision 1)
#   (d) the file actually pins artifacts (at least one <sha256> entry)
#   (e) nothing downgrades, disables, or deletes verification: no
#       --dependency-verification lenient|off flag in a CI workflow, no
#       org.gradle.dependency.verification lenient|off property in a committed
#       gradle.properties, and no workflow removing verification-metadata.xml
#       outside the one generator workflow allowlisted for it
#   (f) gradle-wrapper.properties pins the distribution via distributionSha256Sum,
#       names the expected Gradle version, and pins that release's checksum
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

# The expected Gradle release, and the three values that together identify it.
#
# The wrapper-JAR pin alone is version-blind. Gradle republishes byte-identical
# wrapper JARs across releases, so one checksum can be correct for several Gradle
# versions at once (this JAR is shared by 9.5.0, 9.5.1, and 9.6.1). A matching JAR
# therefore proves the bytes are genuinely Gradle's, but says nothing about which
# distribution the wrapper will go on to download. That is how the original defect
# (issue #744) hid: an 8.14 wrapper JAR sat against an 8.9 distribution, and no
# single-value check could see the mismatch.
#
# So the version and distribution checksum are pinned here too, and checked against
# gradle-wrapper.properties. The three constants must describe ONE release:
#
#   GRADLE_VERSION      the release the wrapper must be configured for
#   GRADLE_DIST_SHA256  https://services.gradle.org/distributions/gradle-<ver>-bin.zip.sha256
#   WRAPPER_JAR_SHA256  https://services.gradle.org/distributions/gradle-<ver>-wrapper.jar.sha256
#
# On a Gradle upgrade, update all three from those published URLs in one change.
GRADLE_VERSION="9.5.1"
GRADLE_DIST_SHA256="bafc141b619ad6350fd975fc903156dd5c151998cc8b058e8c1044ab5f7b031f"
WRAPPER_JAR_SHA256="497c8c2a7e5031f6aa847f88104aa80a93532ec32ee17bdb8d1d2f67a194a9c7"

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

# (e) nothing downgrades, disables, or deletes verification. Three paths can weaken
# it: a --dependency-verification lenient|off flag on a gradlew invocation in a CI
# workflow, the org.gradle.dependency.verification=lenient|off property in a
# committed gradle.properties, or a workflow step that simply removes
# verification-metadata.xml (deleting the file turns enforcement off just as
# effectively as a flag). The default (none present) is strict, which is what this
# change requires. In the flag pattern, [= ] already matches the
# "--dependency-verification lenient" space form, so no separate alternation is
# needed.
#
# One workflow is allowlisted by exact filename for the deletion pattern:
# regenerate-gradle-toolchain.yml deletes the file on purpose, so the metadata it
# regenerates is built from scratch rather than merged onto stale pins. It is
# manual-dispatch only, it never pushes, and its output is a review artifact.
DELETION_ALLOWLIST="regenerate-gradle-toolchain.yml"
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
while IFS= read -r wf; do
    [ -n "$wf" ] || continue
    [ "$(basename "$wf")" = "$DELETION_ALLOWLIST" ] && continue
    if grep -nE '(^|[[:space:];&|])(rm([[:space:]]+-[^[:space:]]+)*|git[[:space:]]+rm([[:space:]]+-[^[:space:]]+)*|mv)[[:space:]]+[^#]*verification-metadata\.xml' \
        "$wf" >/dev/null 2>&1; then
        fail "a CI workflow deletes or moves verification-metadata.xml: ${wf#"$REPO_ROOT/"}"
        downgraded=1
    fi
done < <(find "$WORKFLOWS_DIR" -maxdepth 1 -type f \( -name '*.yml' -o -name '*.yaml' \) 2>/dev/null)
if [ "$downgraded" -eq 0 ]; then
    pass "nothing downgrades verification (no lenient/off flag, property, or metadata deletion)"
fi

# (f) the distribution is pinned, and pinned to the expected release. The checksum
# alone would happily authenticate the wrong Gradle version, so the version in
# distributionUrl and the value of distributionSha256Sum are both asserted against
# the constants above.
if grep -Eq '^distributionSha256Sum=[0-9a-f]{64}$' "$WRAPPER_PROPS"; then
    pass "gradle-wrapper.properties pins the distribution via distributionSha256Sum"
else
    fail "gradle-wrapper.properties is missing a valid distributionSha256Sum pin"
fi

ACTUAL_DIST_URL=$(sed -n 's/^distributionUrl=//p' "$WRAPPER_PROPS" | head -n 1)
case "$ACTUAL_DIST_URL" in
    *"gradle-${GRADLE_VERSION}-bin.zip")
        pass "distributionUrl points at gradle-${GRADLE_VERSION}-bin.zip"
        ;;
    *)
        fail "distributionUrl must end with gradle-${GRADLE_VERSION}-bin.zip, was '$ACTUAL_DIST_URL'"
        ;;
esac

ACTUAL_DIST_SHA=$(sed -n 's/^distributionSha256Sum=//p' "$WRAPPER_PROPS" | head -n 1)
if [ "$ACTUAL_DIST_SHA" = "$GRADLE_DIST_SHA256" ]; then
    pass "distributionSha256Sum matches the published checksum for Gradle $GRADLE_VERSION"
else
    fail "distributionSha256Sum must be $GRADLE_DIST_SHA256 (Gradle $GRADLE_VERSION), was '$ACTUAL_DIST_SHA'"
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
        pass "gradle-wrapper.jar matches the wrapper JAR published for Gradle $GRADLE_VERSION"
    else
        fail "gradle-wrapper.jar SHA-256 mismatch: expected $WRAPPER_JAR_SHA256 (Gradle $GRADLE_VERSION), got $ACTUAL_WRAPPER_SHA"
    fi
fi

echo
echo "test_verification_metadata.sh: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
