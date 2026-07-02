#!/usr/bin/env bash
# test_check_non_docs_changes.sh -- Shell-based tests for check_non_docs_changes.sh.
#
# Builds small throwaway git repos in a temp dir so each test controls the
# exact commit graph (and, for the no-merge-base case, forces the two graphs
# to be genuinely disjoint) rather than depending on this repo's real history.
#
# Covers:
#   (a) Base and head identical -> "false"
#   (b) Only a .md file changed -> "false"
#   (c) A non-.md file changed -> "true"
#   (d) A mix of .md and non-.md changes -> "true"
#   (e) No merge base (disjoint histories, e.g. a rewritten base branch) ->
#       "true", with a ::warning on stderr
#   (f) HEAD_REF defaults to HEAD when omitted
#
# Always exits 0 on success, non-zero on failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECK="$SCRIPT_DIR/check_non_docs_changes.sh"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Isolate git from the ambient environment: a fixed identity means the tests
# do not depend on global git config being present on the runner.
export GIT_AUTHOR_NAME="Test" GIT_AUTHOR_EMAIL="test@example.com"
export GIT_COMMITTER_NAME="Test" GIT_COMMITTER_EMAIL="test@example.com"

REPO="$TMP/repo"
mkdir -p "$REPO"
git -C "$REPO" init -q -b main
git -C "$REPO" commit -q --allow-empty -m "base commit"
BASE_SHA="$(git -C "$REPO" rev-parse HEAD)"

# ── (a) Base and head identical -> "false" ───────────────────────────────────
echo ""
echo "=== (a) base and head identical -> false ==="
OUT="$(cd "$REPO" && "$CHECK" "$BASE_SHA" HEAD)"
if [[ "$OUT" == "false" ]]; then pass "identical refs -> false"; else fail "expected false, got '$OUT'"; fi

# ── (b) Only a .md file changed -> "false" ────────────────────────────────────
echo ""
echo "=== (b) only a .md file changed -> false ==="
echo "hello" > "$REPO/README.md"
git -C "$REPO" add README.md
git -C "$REPO" commit -q -m "docs only"
OUT="$(cd "$REPO" && "$CHECK" "$BASE_SHA" HEAD)"
if [[ "$OUT" == "false" ]]; then pass "docs-only change -> false"; else fail "expected false, got '$OUT'"; fi

# ── (c) A non-.md file changed -> "true" ──────────────────────────────────────
echo ""
echo "=== (c) a non-.md file changed -> true ==="
echo "content" > "$REPO/app.txt"
git -C "$REPO" add app.txt
git -C "$REPO" commit -q -m "code change"
OUT="$(cd "$REPO" && "$CHECK" "$BASE_SHA" HEAD)"
if [[ "$OUT" == "true" ]]; then pass "non-docs change -> true"; else fail "expected true, got '$OUT'"; fi

# ── (d) A mix of .md and non-.md changes -> "true" ────────────────────────────
echo ""
echo "=== (d) a mix of .md and non-.md changes -> true ==="
echo "more docs" >> "$REPO/README.md"
echo "more code" >> "$REPO/app.txt"
git -C "$REPO" add README.md app.txt
git -C "$REPO" commit -q -m "mixed change"
OUT="$(cd "$REPO" && "$CHECK" "$BASE_SHA" HEAD)"
if [[ "$OUT" == "true" ]]; then pass "mixed change -> true"; else fail "expected true, got '$OUT'"; fi

# ── (e) No merge base -> "true", with a ::warning ─────────────────────────────
echo ""
echo "=== (e) no merge base (disjoint histories) -> true, with a warning ==="
ORPHAN="$TMP/orphan"
mkdir -p "$ORPHAN"
git -C "$ORPHAN" init -q -b main
echo "unrelated code" > "$ORPHAN/other.txt"
git -C "$ORPHAN" add other.txt
git -C "$ORPHAN" commit -q -m "disjoint history"
ORPHAN_SHA="$(git -C "$ORPHAN" rev-parse HEAD)"
# Import the orphan's objects into the main repo without linking histories, so
# the two SHAs genuinely share no common ancestor within one repo.
git -C "$REPO" fetch -q "$ORPHAN" main:refs/heads/orphan-main
STDERR_FILE="$TMP/stderr.txt"
OUT="$(cd "$REPO" && "$CHECK" "$ORPHAN_SHA" HEAD 2> "$STDERR_FILE")"
if [[ "$OUT" == "true" ]]; then pass "no merge base -> true (fail safe)"; else fail "expected true, got '$OUT'"; fi
if grep -q '::warning::' "$STDERR_FILE"; then
  pass "no merge base emits a ::warning::"
else
  fail "expected a ::warning:: on stderr, got: $(cat "$STDERR_FILE")"
fi

# ── (f) HEAD_REF defaults to HEAD when omitted ────────────────────────────────
echo ""
echo "=== (f) head-ref argument is optional, defaults to HEAD ==="
OUT="$(cd "$REPO" && "$CHECK" "$BASE_SHA")"
if [[ "$OUT" == "true" ]]; then pass "omitted head-ref defaults to HEAD"; else fail "expected true, got '$OUT'"; fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed."
if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
