#!/usr/bin/env bash
# test_dependabot_config_limit_margin.sh: tests for the pull request limit
# margin in scripts/test_dependabot_config.sh (issue #937).
#
# That script counts the pull requests a Dependabot update entry's coordinates
# can want open at once (one per non-empty group, plus one per ungrouped
# coordinate) and reports on `open-pull-requests-limit` against that count. The
# reporting has three bands, and this file drives all three:
#
#   limit <= count       FAIL. At parity the limit is the binding constraint:
#                        it covers exactly what today's manifest can want and
#                        starves the next ungrouped coordinate added to it.
#   limit == count + 1   PASS with a WARN. Nothing is wrong yet, and one more
#                        ungrouped coordinate makes it parity.
#   limit >= count + 2   PASS, silently.
#
# A warning is not fatal, which is the other property asserted here: the band
# that warns still exits 0. Nor is printing it the whole of delivering it: under
# GitHub Actions the guard re-emits each warning as a ::warning annotation, so
# the warning band is driven both ways and the silent band is driven under
# Actions too, which is what shows the annotation follows the warning rather
# than the environment.
#
# Every fixture run captures the guard's stdout, so an annotation a fixture
# provokes is swallowed here rather than landing on the run that is only testing
# for it, and the runs that are not testing annotations clear GITHUB_ACTIONS
# outright.
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

# Captured guard output, defanged for quoting in a failure message: a workflow
# command echoed verbatim would be obeyed by the runner reporting the failure.
dump() { sed 's/^::/[::]/' <<< "$1"; }

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
# Pass "actions" as $2 to run it as GitHub Actions would; the default clears
# GITHUB_ACTIONS so an inherited one cannot change what the other bands print.
run_with_limit() {
    local limit="$1" environment="${2:-local}"
    local fixture="$TMP_DIR/dependabot-limit-$limit.yml"
    sed -E "s/^([[:space:]]*open-pull-requests-limit:).*/\1 $limit/" "$CONFIG" > "$fixture"
    # A renamed or reformatted key would leave the real limit in place and make
    # every band below run the same configuration.
    if ! grep -qE "^[[:space:]]*open-pull-requests-limit: $limit\$" "$fixture"; then
        fail "the fixture for a limit of $limit does not set that limit"
    fi
    if [ "$environment" = "actions" ]; then
        OUTPUT="$(GITHUB_ACTIONS=true bash "$TARGET" "$fixture" 2>&1)"
    else
        OUTPUT="$(env -u GITHUB_ACTIONS bash "$TARGET" "$fixture" 2>&1)"
    fi
    STATUS=$?
}

# The line every band prints, whatever its verdict, is the one carrying the count.
COUNT_RE='the ([0-9]+) pull requests its coordinates can want open at once'

expect_limit_verdict() {
    local label="$1" want="$2"
    if grep -qE "^  $want: .*open-pull-requests-limit of .*$COUNT_RE" <<< "$OUTPUT"; then
        pass "$label reports the limit check as $want"
    else
        fail "$label did not report the limit check as $want; output was: $(dump "$OUTPUT")"
    fi
}

# $2 is "zero" or "non-zero": the script reports its verdict in the exit status
# as well as in its output, and a warning must not move it.
expect_status() {
    local label="$1" want="$2"
    if { [ "$want" = "zero" ] && [ "$STATUS" -eq 0 ]; } || { [ "$want" = "non-zero" ] && [ "$STATUS" -ne 0 ]; }; then
        pass "$label exits $want"
    else
        fail "$label exited $STATUS, wanted $want"
    fi
}

expect_warn_count() {
    local label="$1" want="$2"
    local got
    got="$(grep -c '^  WARN: ' <<< "$OUTPUT")"
    if [ "$got" -eq "$want" ]; then
        pass "$label emits $want warning(s)"
    else
        fail "$label emitted $got warning(s), wanted $want; output was: $(dump "$OUTPUT")"
    fi
    if grep -qE "^test_dependabot_config\.sh: [0-9]+ passed, [0-9]+ failed, $want warned$" <<< "$OUTPUT"; then
        pass "$label counts $want warning(s) in its summary line"
    else
        fail "$label did not count $want warning(s) in its summary line; output was: $(dump "$OUTPUT")"
    fi
}

# $2 is the number of ::warning annotations wanted, and each must carry the text
# of the warning it stands for rather than a bare title.
expect_annotations() {
    local label="$1" want="$2"
    local got
    got="$(grep -c '^::warning title=.*::.*open-pull-requests-limit of ' <<< "$OUTPUT")"
    if [ "$got" -eq "$want" ]; then
        pass "$label emits $want annotation(s)"
    else
        fail "$label emitted $got annotation(s), wanted $want; output was: $(dump "$OUTPUT")"
    fi
}

# The count the real manifest produces, read back from the script itself. Run
# against the config as committed, whose own limit does not matter here: every
# band prints the count line, so any of them answers this question.
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
echo "=== a limit below the count fails (the starvation of issue #873) ==="
run_with_limit "$((COUNT - 1))"
expect_limit_verdict "a limit of $((COUNT - 1)) against $COUNT" FAIL
expect_status "a limit of $((COUNT - 1)) against $COUNT" non-zero
expect_warn_count "a limit of $((COUNT - 1)) against $COUNT" 0

echo
echo "=== a limit at parity with the count fails (issue #937) ==="
run_with_limit "$COUNT"
expect_limit_verdict "a limit of $COUNT against $COUNT" FAIL
expect_status "a limit of $COUNT against $COUNT" non-zero
expect_warn_count "a limit of $COUNT against $COUNT" 0

echo
echo "=== a limit one above the count passes with a warning (issue #937) ==="
run_with_limit "$((COUNT + 1))"
expect_limit_verdict "a limit of $((COUNT + 1)) against $COUNT" PASS
expect_status "a limit of $((COUNT + 1)) against $COUNT" zero
expect_warn_count "a limit of $((COUNT + 1)) against $COUNT" 1
expect_annotations "a limit of $((COUNT + 1)) against $COUNT outside Actions" 0

echo
echo "=== a limit two above the count passes silently ==="
run_with_limit "$((COUNT + 2))"
expect_limit_verdict "a limit of $((COUNT + 2)) against $COUNT" PASS
expect_status "a limit of $((COUNT + 2)) against $COUNT" zero
expect_warn_count "a limit of $((COUNT + 2)) against $COUNT" 0

echo
echo "=== under GitHub Actions a warning is also an annotation (issue #937) ==="
run_with_limit "$((COUNT + 1))" actions
expect_status "a limit of $((COUNT + 1)) against $COUNT under Actions" zero
expect_warn_count "a limit of $((COUNT + 1)) against $COUNT under Actions" 1
expect_annotations "a limit of $((COUNT + 1)) against $COUNT under Actions" 1

# The annotation has to follow the warning, not the environment: the band that
# warns about nothing must stay silent in the same environment.
run_with_limit "$((COUNT + 2))" actions
expect_warn_count "a limit of $((COUNT + 2)) against $COUNT under Actions" 0
expect_annotations "a limit of $((COUNT + 2)) against $COUNT under Actions" 0

echo
echo "test_dependabot_config_limit_margin.sh: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
