#!/usr/bin/env bash
# test_regenerate_gradle_verification_cleanup.sh: unit tests for the EXIT-trap
# cleanup in scripts/regenerate-gradle-verification.sh (issue #1290).
#
# The script deletes its throwaway GRADLE_USER_HOME on exit. That delete can fail
# when the Gradle daemon is still writing under the home, and a failure there must
# not change the script's exit status: a successful generation stays a success,
# and a failed one stays a failure.
#
# Each case runs the real script with a stub Gradle as GRADLE_BIN, so no Gradle,
# JDK, Android SDK, or network is needed, and with a stub `rm` first on PATH that
# fails a set number of times before handing over to the real `rm`. A stub is
# used rather than an unwritable directory because `rm -rf` as root ignores
# permissions. The stub Gradle writes nothing to the checkout.
#
# Cases:
#   (a) generation succeeds, every delete fails: exit 0, a warning names the home
#   (b) generation succeeds, the first delete fails: exit 0, the retry removes
#       the home, no warning
#   (c) generation fails, every delete fails: the generation's status survives
#   (d) generation succeeds, the delete succeeds: exit 0, home removed, and the
#       daemon was stopped before the delete
#
# Cases (a) to (c) each wait out the script's retry delay.
#
# Always exits 0 on success, non-zero on failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGEN_SH="$SCRIPT_DIR/regenerate-gradle-verification.sh"
REAL_RM="$(command -v rm)"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

TMP="$(mktemp -d "${TMPDIR:-/tmp}/regen-cleanup-test-XXXXXX")"
trap '"$REAL_RM" -rf "$TMP"' EXIT

# A case directory holds a stub Gradle, a stub rm, and a tmp directory that the
# script's mktemp creates its GRADLE_USER_HOME in (via TMPDIR), so a home the
# script fails to delete is still removed with $TMP.
#
# The stub Gradle logs each invocation along with the GRADLE_USER_HOME it saw,
# and exits gradle_rc for the generation call and 0 for --stop.
# The stub rm fails its first rm_failures calls, then runs the real rm.
make_case() {
    local case_dir="$1" gradle_rc="$2" rm_failures="$3"
    mkdir -p "$case_dir/bin" "$case_dir/tmp"
    cat > "$case_dir/bin/gradle" << EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$case_dir/gradle-calls.log"
printf '%s\n' "\$GRADLE_USER_HOME" > "$case_dir/gradle-user-home"
mkdir -p "\$GRADLE_USER_HOME/daemon/9.5.1"
[ "\$1" = "--stop" ] && exit 0
exit $gradle_rc
EOF
    cat > "$case_dir/bin/rm" << EOF
#!/usr/bin/env bash
printf 'rm\n' >> "$case_dir/rm-calls.log"
if [ "\$(wc -l < "$case_dir/rm-calls.log")" -le $rm_failures ]; then
    echo "rm: cannot remove 'daemon/9.5.1': Directory not empty" >&2
    exit 1
fi
exec "$REAL_RM" "\$@"
EOF
    chmod +x "$case_dir/bin/gradle" "$case_dir/bin/rm"
}

# Run the real script as a workflow would, with GRADLE_BIN, PATH, and TMPDIR
# redirected into the case directory. Prints the script's exit status.
run_regen() {
    local case_dir="$1"
    ANDROID_HOME="$case_dir/fake-sdk" GRADLE_BIN="$case_dir/bin/gradle" \
        PATH="$case_dir/bin:$PATH" TMPDIR="$case_dir/tmp" \
        bash "$REGEN_SH" > "$case_dir/stdout.log" 2> "$case_dir/stderr.log"
    echo $?
}

home_of() { cat "$1/gradle-user-home" 2>/dev/null; }

echo "==> (a) generation succeeds, every delete fails"
CASE="$TMP/a"
make_case "$CASE" 0 99
rc="$(run_regen "$CASE")"
HOME_A="$(home_of "$CASE")"
if [ "$rc" -eq 0 ]; then
    pass "(a) exit status is 0 despite the failed delete"
else
    fail "(a) exit status was $rc, expected 0; stderr: $(cat "$CASE/stderr.log")"
fi
if [ -n "$HOME_A" ] && grep -qF "warning: could not delete the temporary GRADLE_USER_HOME $HOME_A" "$CASE/stderr.log"; then
    pass "(a) a warning names the home that was left behind"
else
    fail "(a) no warning naming the home '$HOME_A'; stderr: $(cat "$CASE/stderr.log")"
fi
if [ "$(wc -l < "$CASE/rm-calls.log")" -eq 2 ]; then
    pass "(a) the delete was tried twice"
else
    fail "(a) the delete was tried $(wc -l < "$CASE/rm-calls.log") times, expected 2"
fi
if grep -q -- "--write-verification-metadata sha256" "$CASE/gradle-calls.log"; then
    pass "(a) the generation command ran"
else
    fail "(a) the generation command did not run; calls: $(cat "$CASE/gradle-calls.log")"
fi

echo "==> (b) generation succeeds, the first delete fails"
CASE="$TMP/b"
make_case "$CASE" 0 1
rc="$(run_regen "$CASE")"
HOME_B="$(home_of "$CASE")"
if [ "$rc" -eq 0 ]; then
    pass "(b) exit status is 0"
else
    fail "(b) exit status was $rc, expected 0; stderr: $(cat "$CASE/stderr.log")"
fi
if [ -n "$HOME_B" ] && [ ! -e "$HOME_B" ]; then
    pass "(b) the retry removed the home"
else
    fail "(b) the home '$HOME_B' was not removed by the retry"
fi
if grep -qE "warning:|Directory not empty" "$CASE/stderr.log"; then
    fail "(b) cleanup output was printed although the retry succeeded: $(cat "$CASE/stderr.log")"
else
    pass "(b) no warning, and the first failure's message is not shown"
fi

echo "==> (c) generation fails, every delete fails"
CASE="$TMP/c"
make_case "$CASE" 3 99
rc="$(run_regen "$CASE")"
if [ "$rc" -eq 3 ]; then
    pass "(c) the generation's exit status 3 survives the failed delete"
else
    fail "(c) exit status was $rc, expected 3; stderr: $(cat "$CASE/stderr.log")"
fi

echo "==> (d) generation succeeds, the delete succeeds"
CASE="$TMP/d"
make_case "$CASE" 0 0
rc="$(run_regen "$CASE")"
HOME_D="$(home_of "$CASE")"
if [ "$rc" -eq 0 ]; then
    pass "(d) exit status is 0"
else
    fail "(d) exit status was $rc, expected 0; stderr: $(cat "$CASE/stderr.log")"
fi
if [ -n "$HOME_D" ] && [ ! -e "$HOME_D" ]; then
    pass "(d) the home was removed"
else
    fail "(d) the home '$HOME_D' was not removed"
fi
if [ "$(tail -n 1 "$CASE/gradle-calls.log")" = "--stop" ] && [ "$(wc -l < "$CASE/rm-calls.log")" -eq 1 ]; then
    pass "(d) the daemon was stopped, then the home deleted once"
else
    fail "(d) expected a final --stop and one delete; gradle calls: $(cat "$CASE/gradle-calls.log"), deletes: $(wc -l < "$CASE/rm-calls.log")"
fi
if grep -q "warning:" "$CASE/stderr.log"; then
    fail "(d) a warning was printed: $(cat "$CASE/stderr.log")"
else
    pass "(d) no warning"
fi

echo
echo "test_regenerate_gradle_verification_cleanup.sh: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
