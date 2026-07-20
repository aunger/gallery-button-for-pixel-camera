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
#   (e) no CI workflow downgrades verification to lenient mode
#   (f) gradle-wrapper.properties pins the distribution via distributionSha256Sum
#
# Always exits 0 on success, non-zero on failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
METADATA="$REPO_ROOT/gradle/verification-metadata.xml"
WRAPPER_PROPS="$REPO_ROOT/gradle/wrapper/gradle-wrapper.properties"
WORKFLOWS_DIR="$REPO_ROOT/.github/workflows"

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

# (e) no workflow downgrades verification to lenient mode.
if grep -rn -- "--dependency-verification[= ]lenient\|--dependency-verification lenient" "$WORKFLOWS_DIR" >/dev/null 2>&1; then
    fail "a CI workflow uses lenient dependency verification"
else
    pass "no CI workflow downgrades verification to lenient"
fi

# (f) the distribution is pinned.
if grep -Eq '^distributionSha256Sum=[0-9a-f]{64}$' "$WRAPPER_PROPS"; then
    pass "gradle-wrapper.properties pins the distribution via distributionSha256Sum"
else
    fail "gradle-wrapper.properties is missing a valid distributionSha256Sum pin"
fi

echo
echo "test_verification_metadata.sh: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
