#!/usr/bin/env bash
# test_install_ktlint.sh: unit tests for scripts/lint/install-ktlint.sh's stale-JAR
# cleanup (issue #700).
#
# On a KTLINT_VERSION bump, the previous version's JAR is version-suffixed and
# never overwritten, so it would otherwise sit in $KTLINT_JAR_DIR forever.
# install-ktlint.sh now removes every ktlint-cli-*-all.jar other than the
# current KTLINT_VERSION's, once that current JAR is confirmed present.
#
# These tests exercise the script's "already installed--skip" fast path only,
# by pre-seeding fake JAR/wrapper files under isolated KTLINT_BIN_DIR /
# KTLINT_JAR_DIR temp directories. That path performs no network I/O, so it
# runs unconditionally in CI and locally without depending on Maven Central
# reachability; the real download-and-verify path is already covered by every
# session-start.sh run and the kotlin-ktlint CI job.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_SH="$SCRIPT_DIR/install-ktlint.sh"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

# The script's own KTLINT_VERSION is the source of truth here, so these tests
# keep working across a version bump without edits.
CURRENT_VERSION="$(sed -n 's/^KTLINT_VERSION="\(.*\)"$/\1/p' "$INSTALL_SH")"
if [[ -z "$CURRENT_VERSION" ]]; then
    echo "FAIL: could not parse KTLINT_VERSION out of $INSTALL_SH"
    exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Set up isolated bin/jar dirs with the current version already "installed"
# (fake, network-free), plus whatever extra jar files the caller lists as
# additional args, and run install-ktlint.sh over them.
run_case() {
    local case_dir="$1"
    shift
    local bin_dir="$case_dir/bin"
    local jar_dir="$case_dir/jar"
    mkdir -p "$bin_dir" "$jar_dir"
    printf '#!/usr/bin/env bash\nexec java -jar fake "$@"\n' > "$bin_dir/ktlint"
    chmod +x "$bin_dir/ktlint"
    printf 'fake-jar' > "$jar_dir/ktlint-cli-$CURRENT_VERSION-all.jar"
    local extra
    for extra in "$@"; do
        printf 'fake-jar' > "$jar_dir/$extra"
    done
    KTLINT_BIN_DIR="$bin_dir" KTLINT_JAR_DIR="$jar_dir" "$INSTALL_SH" > "$case_dir/out.log" 2>&1
    echo $?
}

# ── (a) a stale older-version JAR is removed ─────────────────────────────────
echo ""
echo "=== (a) stale older-version JAR is removed ==="
CASE="$TMP/a"
mkdir -p "$CASE"
RC="$(run_case "$CASE" "ktlint-cli-1.6.0-all.jar")"
if [[ "$RC" -eq 0 ]]; then pass "install-ktlint.sh exits 0"; else fail "install-ktlint.sh should exit 0, rc=$RC"; fi
if [[ ! -f "$CASE/jar/ktlint-cli-1.6.0-all.jar" ]]; then
    pass "stale ktlint-cli-1.6.0-all.jar was removed"
else
    fail "stale ktlint-cli-1.6.0-all.jar should have been removed"
fi
if [[ -f "$CASE/jar/ktlint-cli-$CURRENT_VERSION-all.jar" ]]; then
    pass "current-version JAR was kept"
else
    fail "current-version JAR should have been kept"
fi

# ── (b) multiple stale JARs are all removed ──────────────────────────────────
echo ""
echo "=== (b) multiple stale JARs are all removed ==="
CASE="$TMP/b"
mkdir -p "$CASE"
RC="$(run_case "$CASE" "ktlint-cli-1.5.0-all.jar" "ktlint-cli-1.6.0-all.jar" "ktlint-cli-1.7.0-all.jar")"
if [[ "$RC" -eq 0 ]]; then pass "install-ktlint.sh exits 0"; else fail "install-ktlint.sh should exit 0, rc=$RC"; fi
REMAINING="$(find "$CASE/jar" -maxdepth 1 -name 'ktlint-cli-*-all.jar' | wc -l | tr -d ' ')"
if [[ "$REMAINING" -eq 1 ]]; then
    pass "only the current-version JAR remains"
else
    fail "expected exactly 1 JAR to remain, found $REMAINING"
fi

# ── (c) no stale JARs: idempotent no-op ──────────────────────────────────────
echo ""
echo "=== (c) no stale JARs is a no-op ==="
CASE="$TMP/c"
mkdir -p "$CASE"
RC="$(run_case "$CASE")"
if [[ "$RC" -eq 0 ]]; then pass "install-ktlint.sh exits 0"; else fail "install-ktlint.sh should exit 0, rc=$RC"; fi
if [[ -f "$CASE/jar/ktlint-cli-$CURRENT_VERSION-all.jar" ]]; then
    pass "current-version JAR is untouched"
else
    fail "current-version JAR should still be present"
fi

# ── (d) an unrelated file in the JAR dir is left alone ───────────────────────
echo ""
echo "=== (d) unrelated file is left alone ==="
CASE="$TMP/d"
mkdir -p "$CASE"
RC="$(run_case "$CASE" "ktlint-cli-1.6.0-all.jar")"
printf 'keep me' > "$CASE/jar/notes.txt"
KTLINT_BIN_DIR="$CASE/bin" KTLINT_JAR_DIR="$CASE/jar" "$INSTALL_SH" > "$CASE/out2.log" 2>&1
RC=$?
if [[ "$RC" -eq 0 ]]; then pass "install-ktlint.sh exits 0"; else fail "install-ktlint.sh should exit 0, rc=$RC"; fi
if [[ -f "$CASE/jar/notes.txt" ]]; then
    pass "unrelated file was left alone"
else
    fail "unrelated file should not have been removed"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed."
[[ $FAIL -gt 0 ]] && exit 1
exit 0
