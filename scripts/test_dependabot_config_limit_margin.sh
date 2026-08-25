#!/usr/bin/env bash
# test_dependabot_config_limit_margin.sh: tests for the pull request limit
# margin in scripts/test_dependabot_config.sh (issue #937).
#
# That script counts the pull requests a Dependabot update entry's coordinates
# can want open at once (one per non-empty group, plus one per ungrouped
# coordinate) and checks `open-pull-requests-limit` against that count. The
# check requires a margin rather than bare coverage, so it has two bands and
# this file drives both, from either side of the boundary:
#
#   limit <= count   FAIL, naming the limit that fixes it. At parity the limit
#                    covers exactly what today's manifest can want and starves
#                    the next ungrouped coordinate added to it; below parity it
#                    is already starving one, which is what #871 reported.
#   limit >  count   PASS.
#
# Naming the fix is asserted, not just the verdict: the person who trips the
# parity failure is adding a dependency to a configuration that works and has
# no context on margins, so a bare verdict would leave them nothing to act on.
#
# The fixtures are the real .github/dependabot.yml with nothing but the limit
# rewritten, so every other check in the script stays green and the exit status
# reports the limit check alone. The count is read back out of the script's own
# output rather than hardcoded, so adding a dependency to app/build.gradle.kts
# moves the fixtures with it instead of breaking this file.
#
# Always exits 0 on success, non-zero on failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET="$SCRIPT_DIR/test_dependabot_config.sh"
CONFIG="$REPO_ROOT/.github/dependabot.yml"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

# The script under test needs PyYAML (scripts/requirements.txt) to read the
# config at all; without it every band would look alike. Skip rather than fail,
# matching how the lint tests skip a tool they cannot provision.
if ! python3 -c "import yaml" > /dev/null 2>&1; then
    echo "SKIP: PyYAML is not installed (see scripts/requirements.txt)"
    exit 0
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

OUTPUT=""
STATUS=0

# Run the script under test against a copy of the real config whose
# open-pull-requests-limit is $1, leaving the result in $OUTPUT and $STATUS.
run_with_limit() {
    local limit="$1"
    local fixture="$TMP_DIR/dependabot-limit-$limit.yml"
    sed -E "s/^([[:space:]]*open-pull-requests-limit:).*/\1 $limit/" "$CONFIG" > "$fixture"
    # A renamed or reformatted key would leave the real limit in place and make
    # every band below run the same configuration.
    if ! grep -qE "^[[:space:]]*open-pull-requests-limit: $limit\$" "$fixture"; then
        fail "the fixture for a limit of $limit does not set that limit"
    fi
    OUTPUT="$(bash "$TARGET" "$fixture" 2>&1)"
    STATUS=$?
}

# The line both bands print, whatever the verdict, is the one carrying the count.
COUNT_RE='the ([0-9]+) pull requests its coordinates can want open at once'

expect_limit_verdict() {
    local label="$1" want="$2"
    if grep -qE "^  $want: .*open-pull-requests-limit of .*$COUNT_RE" <<< "$OUTPUT"; then
        pass "$label reports the limit check as $want"
    else
        fail "$label did not report the limit check as $want; output was: $OUTPUT"
    fi
}

# $2 is "zero" or "non-zero": the script reports its verdict in the exit status
# as well as in its output, and the two have to agree.
expect_status() {
    local label="$1" want="$2"
    if { [ "$want" = "zero" ] && [ "$STATUS" -eq 0 ]; } || { [ "$want" = "non-zero" ] && [ "$STATUS" -ne 0 ]; }; then
        pass "$label exits $want"
    else
        fail "$label exited $STATUS, wanted $want"
    fi
}

# $2 is the limit a failure must name as its fix.
expect_remedy() {
    local label="$1" want="$2"
    if grep -qE "^  FAIL: .*raising the limit to $want or more is what fixes it" <<< "$OUTPUT"; then
        pass "$label names $want as the limit that fixes it"
    else
        fail "$label did not name $want as the limit that fixes it; output was: $OUTPUT"
    fi
}

# The count the real manifest produces, read back from the script itself. Run
# against the config as committed, whose own limit does not matter here: both
# bands print the count line, so either of them answers this question.
echo
echo "=== reading the pull request count from $TARGET ==="
OUTPUT="$(bash "$TARGET" "$CONFIG" 2>&1)"
COUNT="$(grep -oE "$COUNT_RE" <<< "$OUTPUT" | head -1 | grep -oE '[0-9]+')"
if [ -n "$COUNT" ]; then
    pass "the limit check reports a pull request count ($COUNT)"
else
    fail "no pull request count found in the output: $OUTPUT"
    echo
    echo "test_dependabot_config_limit_margin.sh: $PASS passed, $FAIL failed"
    exit 1
fi

echo
echo "=== a limit below the count fails (the starvation #871 reported) ==="
run_with_limit "$((COUNT - 1))"
expect_limit_verdict "a limit of $((COUNT - 1)) against $COUNT" FAIL
expect_status "a limit of $((COUNT - 1)) against $COUNT" non-zero
expect_remedy "a limit of $((COUNT - 1)) against $COUNT" "$((COUNT + 1))"

echo
echo "=== a limit at parity with the count fails (issue #937) ==="
run_with_limit "$COUNT"
expect_limit_verdict "a limit of $COUNT against $COUNT" FAIL
expect_status "a limit of $COUNT against $COUNT" non-zero
expect_remedy "a limit of $COUNT against $COUNT" "$((COUNT + 1))"

echo
echo "=== one above the count is the first limit that passes (issue #937) ==="
run_with_limit "$((COUNT + 1))"
expect_limit_verdict "a limit of $((COUNT + 1)) against $COUNT" PASS
expect_status "a limit of $((COUNT + 1)) against $COUNT" zero

echo
echo "=== a limit further above the count passes too ==="
run_with_limit "$((COUNT + 2))"
expect_limit_verdict "a limit of $((COUNT + 2)) against $COUNT" PASS
expect_status "a limit of $((COUNT + 2)) against $COUNT" zero

echo
echo "test_dependabot_config_limit_margin.sh: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
