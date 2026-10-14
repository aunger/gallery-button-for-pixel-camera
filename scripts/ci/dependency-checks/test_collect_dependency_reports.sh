#!/usr/bin/env bash
# test_collect_dependency_reports.sh: tests for collect_dependency_reports.sh.
#
# Runs the collector against a throwaway tree whose ./gradlew is a fake that
# prints a report, so no Gradle or network is involved. RETRY_DELAYS is set so
# no case sleeps.
#
# Covers:
#   (a) every configuration's report is saved under the name
#       `dependency_checks.py configurations` gives, from TREE's own gradlew
#   (b) a non-zero Gradle exit is retried, and a later success is kept
#   (c) a report with a FAILED node is retried, though Gradle exits 0
#   (d) a configuration that never succeeds fails the script, naming it
#
# Always exits 0 on success, non-zero on failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COLLECT="$SCRIPT_DIR/collect_dependency_reports.sh"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

EXPECTED_FILES="$(python3 "$SCRIPT_DIR/dependency_checks.py" configurations | awk '{print $3}' | sort)"
COUNT="$(echo "$EXPECTED_FILES" | wc -l)"

# make_tree DIR MODE: a tree whose ./gradlew prints a report for its
# --configuration argument. MODE "ok" always succeeds; "exit-once" exits 1 on
# its first call; "failed-once" prints a FAILED node on its first call;
# "failed-always" always does. Calls are counted in DIR/calls.
make_tree() {
    mkdir -p "$1"
    cat > "$1/gradlew" <<EOF
#!/usr/bin/env bash
dir="\$(cd "\$(dirname "\$0")" && pwd)"
echo call >> "\$dir/calls"
calls="\$(wc -l < "\$dir/calls")"
while [[ \$# -gt 0 ]]; do
    if [[ "\$1" == "--configuration" ]]; then configuration="\$2"; fi
    shift
done
echo "\$configuration - fake"
case "$2" in
    exit-once) if [[ "\$calls" == 1 ]]; then echo "boom" >&2; exit 1; fi ;;
    failed-once) if [[ "\$calls" == 1 ]]; then echo "+--- a:b:1.0 FAILED"; exit 0; fi ;;
    failed-always) echo "+--- a:b:1.0 FAILED"; exit 0 ;;
esac
echo "\\--- a:b:1.0"
EOF
    chmod +x "$1/gradlew"
}

export RETRY_DELAYS="0 0"

echo ""
echo "=== (a) every configuration is saved under its file name ==="
make_tree "$TMP/a" ok
if bash "$COLLECT" "$TMP/a" "$TMP/a-out" > /dev/null 2>&1; then
    pass "collector exits 0"
else
    fail "collector failed on a tree that always succeeds"
fi
SAVED="$(cd "$TMP/a-out" && ls -- *.txt | sort)"
if [[ "$SAVED" == "$EXPECTED_FILES" ]]; then pass "saved $COUNT reports"; else fail "saved: $SAVED"; fi
if grep -q '^releaseRuntimeClasspath - fake$' "$TMP/a-out/app-releaseRuntimeClasspath.txt"; then
    pass "a report is the output for its own configuration"
else
    fail "app-releaseRuntimeClasspath.txt holds the wrong output"
fi
if [[ "$(wc -l < "$TMP/a/calls")" == "$COUNT" ]]; then pass "one call per configuration"; else fail "calls: $(wc -l < "$TMP/a/calls")"; fi

echo ""
echo "=== (b) a non-zero exit is retried ==="
make_tree "$TMP/b" exit-once
if bash "$COLLECT" "$TMP/b" "$TMP/b-out" > /dev/null 2>&1; then
    pass "collector recovers"
else
    fail "collector did not retry a failed call"
fi
if [[ "$(wc -l < "$TMP/b/calls")" == "$((COUNT + 1))" ]]; then pass "exactly one retry"; else fail "calls: $(wc -l < "$TMP/b/calls")"; fi

echo ""
echo "=== (c) a FAILED node is retried though Gradle exits 0 ==="
make_tree "$TMP/c" failed-once
if bash "$COLLECT" "$TMP/c" "$TMP/c-out" > /dev/null 2>&1; then
    pass "collector recovers"
else
    fail "collector did not retry a report with a FAILED node"
fi
if grep -rq 'FAILED' "$TMP/c-out"/*.txt; then fail "a FAILED report was kept"; else pass "no FAILED report kept"; fi

echo ""
echo "=== (d) a configuration that never succeeds fails the script ==="
make_tree "$TMP/d" failed-always
if ERR="$(bash "$COLLECT" "$TMP/d" "$TMP/d-out" 2>&1 >/dev/null)"; then
    fail "collector exited 0 though every report had a FAILED node"
else
    pass "collector exits non-zero"
fi
if [[ "$ERR" == *"::error::Gradle could not report :app debugCompileClasspath after 3 attempts"* ]]; then
    pass "error names the configuration and the attempts"
else
    fail "error was: $ERR"
fi
if [[ "$(wc -l < "$TMP/d/calls")" == 3 ]]; then pass "stops at the first configuration that fails"; else fail "calls: $(wc -l < "$TMP/d/calls")"; fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
