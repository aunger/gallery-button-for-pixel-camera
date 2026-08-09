#!/usr/bin/env bash
# test_install_pinned_requirements.sh: unit tests for
# scripts/install-pinned-requirements.sh, the shared installer the session-start
# hook provisions every hash-pinned pip lock through.
#
# The behavioral cases run the real script against an isolated HOME with a stub
# `pip` first on PATH, so they exercise the skip marker, the flags the script
# passes, and its failure handling without touching PyPI: they run unconditionally
# in CI and locally with no network.
#
# The wiring cases guard the call sites instead. The marker file is named after
# the lock, so two locks with the same file name would share one marker and the
# second would be skipped as "already installed" on the strength of the first.
# The script cannot see its own call sites, so that check lives here.
#
# HOW THE WIRING CASES FIND A LOCK
#
# Cases (g) and (h) grep for a spelling rather than parsing bash or YAML, so an
# install written another way drops out of the comparison silently: (h) would
# then still pass, because its non-empty guard only proves that some lock
# matched, not that yours did. If you add an install, match these spellings, or
# extend the patterns here:
#
#   .claude/hooks/session-start.sh   "$REPO_ROOT/scripts/<path>.txt", unquoted
#                                    path, no variable standing in for it
#   .github/workflows/build.yml      -r scripts/<path>.txt (not --requirement,
#                                    not a quoted or variable path)
#
# Comments are stripped from both files first, so prose naming a lock is not
# counted as an install.
#
# Always exits 0 on success, non-zero on failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_SH="$SCRIPT_DIR/install-pinned-requirements.sh"
HOOK="$REPO_ROOT/.claude/hooks/session-start.sh"

PASS=0
FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

TMP="$(mktemp -d "${TMPDIR:-/tmp}/install-pinned-req-test-XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

# A case directory holds its own HOME (so the marker cannot escape into the real
# one), a stub pip that records every invocation, and a lock file to install.
# The stub's exit status is what makes the "pip failed" case possible.
make_case() {
    local case_dir="$1" pip_rc="${2:-0}"
    mkdir -p "$case_dir/home" "$case_dir/bin"
    cat > "$case_dir/bin/pip" << EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$case_dir/pip-args.log"
exit $pip_rc
EOF
    chmod +x "$case_dir/bin/pip"
    printf 'defusedxml==0.7.1 --hash=sha256:%064d\n' 1 > "$case_dir/requirements-fake.txt"
}

# Run the real script exactly as the hook does, with only HOME and PATH redirected.
run_installer() {
    local case_dir="$1"
    local lock="${2:-$case_dir/requirements-fake.txt}"
    HOME="$case_dir/home" PATH="$case_dir/bin:$PATH" \
        "$INSTALL_SH" "$lock" "test dependencies" >> "$case_dir/out.log" 2>&1
}

pip_calls() {
    local case_dir="$1"
    [[ -f "$case_dir/pip-args.log" ]] && wc -l < "$case_dir/pip-args.log" | tr -d ' ' || echo 0
}

marker_of() {
    echo "$1/home/.local/share/gb4pc/requirements-fake.sha256"
}

# ── (a) structural ───────────────────────────────────────────────────────────
echo ""
echo "=== (a) the installer is a well-formed script ==="
if [[ -x "$INSTALL_SH" ]]; then
    pass "install-pinned-requirements.sh is executable"
else
    fail "install-pinned-requirements.sh must be executable (chmod +x)"
fi
if bash -n "$INSTALL_SH" 2>/dev/null; then
    pass "install-pinned-requirements.sh parses as bash"
else
    fail "install-pinned-requirements.sh has a syntax error"
fi
if grep -q '^set -euo pipefail$' "$INSTALL_SH"; then
    pass "install-pinned-requirements.sh runs under set -euo pipefail"
else
    fail "install-pinned-requirements.sh must set -euo pipefail (a half-installed lock must not be recorded as done)"
fi

# ── (b) a lock not yet installed is installed, hash-checked, and marked ──────
echo ""
echo "=== (b) an uninstalled lock is installed with --require-hashes ==="
CASE="$TMP/fresh"
make_case "$CASE"
if run_installer "$CASE"; then
    pass "the installer exits 0"
else
    fail "the installer should exit 0 on a successful install"
    sed 's/^/    /' "$CASE/out.log"
fi
if [[ "$(pip_calls "$CASE")" -eq 1 ]]; then
    pass "pip was invoked once"
else
    fail "expected exactly one pip invocation, got $(pip_calls "$CASE")"
fi
PIP_ARGS="$(cat "$CASE/pip-args.log" 2>/dev/null)"
if [[ "$PIP_ARGS" == *"--require-hashes"* ]]; then
    pass "pip was passed --require-hashes"
else
    fail "pip must be passed --require-hashes; got: $PIP_ARGS"
fi
if [[ "$PIP_ARGS" == *"-r $CASE/requirements-fake.txt"* ]]; then
    pass "pip was pointed at the lock it was given"
else
    fail "pip was not pointed at the lock; got: $PIP_ARGS"
fi
EXPECTED_SHA="$(sha256sum "$CASE/requirements-fake.txt" | cut -d' ' -f1)"
if [[ "$(cat "$(marker_of "$CASE")" 2>/dev/null)" == "$EXPECTED_SHA" ]]; then
    pass "the marker records the SHA-256 of the installed lock"
else
    fail "the marker should hold the lock's SHA-256"
fi

# ── (c) an unchanged lock is skipped ─────────────────────────────────────────
echo ""
echo "=== (c) an unchanged lock is a no-op on a re-run ==="
if run_installer "$CASE" && [[ "$(pip_calls "$CASE")" -eq 1 ]]; then
    pass "the second run installed nothing"
else
    fail "an unchanged lock must not be reinstalled (pip calls: $(pip_calls "$CASE"))"
fi
if grep -q 'up to date--skip' "$CASE/out.log"; then
    pass "the skip is reported"
else
    fail "the skip should be reported"
    sed 's/^/    /' "$CASE/out.log"
fi

# ── (d) an edited lock is reinstalled ────────────────────────────────────────
echo ""
echo "=== (d) an edited lock is reinstalled ==="
printf 'defusedxml==0.7.2 --hash=sha256:%064d\n' 2 > "$CASE/requirements-fake.txt"
if run_installer "$CASE" && [[ "$(pip_calls "$CASE")" -eq 2 ]]; then
    pass "a changed lock triggers a reinstall"
else
    fail "a changed lock must be reinstalled (pip calls: $(pip_calls "$CASE"))"
    sed 's/^/    /' "$CASE/out.log"
fi

# ── (e) a failed install is not remembered as done ───────────────────────────
echo ""
echo "=== (e) a failed pip install writes no marker ==="
BROKEN="$TMP/broken"
make_case "$BROKEN" 1
if run_installer "$BROKEN"; then
    fail "the installer must fail when pip fails"
else
    pass "the installer exits non-zero when pip fails"
fi
if [[ ! -f "$(marker_of "$BROKEN")" ]]; then
    pass "no marker is written, so the next run retries"
else
    fail "a failed install must not write its marker"
fi

# ── (f) a missing lock is an error, not a silent skip ────────────────────────
echo ""
echo "=== (f) a missing lock fails loudly ==="
ABSENT="$TMP/absent"
make_case "$ABSENT"
if run_installer "$ABSENT" "$ABSENT/no-such-lock.txt"; then
    fail "the installer must fail when the lock does not exist"
else
    pass "the installer exits non-zero on a missing lock"
fi
if [[ "$(pip_calls "$ABSENT")" -eq 0 ]]; then
    pass "pip is never invoked for a lock that does not exist"
else
    fail "pip must not run when the lock is missing"
fi

# ── (g) the locks the session-start hook installs ────────────────────────────
#
# Every lock path the hook names, taken from the hook itself rather than listed
# here, so a lock added to the hook is covered without editing this test.
echo ""
echo "=== (g) the session-start hook's locks ==="
HOOK_LOCKS="$(sed 's/#.*$//' "$HOOK" \
    | grep -o '\$REPO_ROOT/scripts/[A-Za-z0-9_./-]*\.txt' | sed 's|^\$REPO_ROOT/||' | sort -u)"
if [[ -n "$HOOK_LOCKS" ]]; then
    pass "the hook provisions locks: $(echo "$HOOK_LOCKS" | tr '\n' ' ')"
else
    fail "the hook names no requirements lock at all"
fi

MISSING_LOCKS=""
while IFS= read -r lock; do
    [[ -z "$lock" ]] && continue
    [[ -f "$REPO_ROOT/$lock" ]] || MISSING_LOCKS="$MISSING_LOCKS $lock"
done <<< "$HOOK_LOCKS"
if [[ -z "$MISSING_LOCKS" ]]; then
    pass "every lock the hook installs exists in the repository"
else
    fail "the hook installs locks that do not exist:$MISSING_LOCKS"
fi

# The marker is named after the lock's file name, so same-named locks in
# different directories would collide and the second would never be installed.
LOCK_NAMES="$(echo "$HOOK_LOCKS" | sed 's|.*/||' | sort)"
if [[ "$(echo "$LOCK_NAMES" | wc -l)" -eq "$(echo "$LOCK_NAMES" | sort -u | wc -l)" ]]; then
    pass "the locks have distinct file names, so their markers cannot collide"
else
    fail "two locks share a file name; their SHA-256 markers would collide"
fi

# ── (h) a session provisions every lock the test suites need ─────────────────
#
# The gap this closes (issue #806) was not a missing dependency but a
# provisioning asymmetry: .github/workflows/build.yml installed
# scripts/requirements.txt before running the Python and shell test suites, and
# nothing on the session side did, so five of those tests failed locally with
# "No module named 'defusedxml'" whatever the change under test was.
#
# Both lists are read from the files themselves, so a lock added to build.yml has
# to be provisioned for sessions too. Only build.yml is compared: it is the
# workflow that runs the test suites a session is expected to reproduce, whereas
# semgrep.yml installs an engine that is deliberately CI-only.
echo ""
echo "=== (h) every lock CI installs to run the tests is installed for sessions ==="
CI_LOCKS="$(sed 's/#.*$//' "$REPO_ROOT/.github/workflows/build.yml" \
    | grep -o -- '-r scripts/[A-Za-z0-9_./-]*\.txt' | sed 's/^-r //' | sort -u)"
if [[ -n "$CI_LOCKS" ]]; then
    pass "build.yml installs locks: $(echo "$CI_LOCKS" | tr '\n' ' ')"
else
    fail "no requirements lock found in .github/workflows/build.yml (did the install steps move?)"
fi

UNPROVISIONED=""
while IFS= read -r lock; do
    [[ -z "$lock" ]] && continue
    echo "$HOOK_LOCKS" | grep -qx -- "$lock" || UNPROVISIONED="$UNPROVISIONED $lock"
done <<< "$CI_LOCKS"
if [[ -z "$UNPROVISIONED" ]]; then
    pass "the session-start hook installs every lock build.yml installs"
else
    fail "build.yml installs locks the session-start hook does not:$UNPROVISIONED"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "test_install_pinned_requirements.sh: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]] && exit 0
exit 1
