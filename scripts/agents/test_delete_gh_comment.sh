#!/usr/bin/env bash
# test_delete_gh_comment.sh: Shell-based tests for delete_gh_comment.sh.
#
# Runs the script against a fake `curl` on PATH instead of the real one, so no
# live GitHub token or network is needed. The fake curl records the method and
# URL it was invoked with, writes a controllable body to the file named by
# -o, and prints a controllable status code to stdout (mirroring how the real
# `curl -w '%{http_code}'` behaves once -o diverts the response body away).
#
# Covers:
#   (a) Wrong argument count -> usage error, exit 1, curl not invoked
#   (b) -h/--help prints usage, exit 1
#   (c) GITHUB_TOKEN unset -> error, exit 1, curl not invoked
#   (d) Non-numeric comment_id -> error, exit 1, curl not invoked
#   (e) 204 response -> success message, exit 0, correct DELETE URL used
#   (f) 404 response -> "not found" error, exit 1
#   (g) 403 response -> "forbidden" error with response body, exit 1
#   (h) 401 response -> "unauthorized" error, exit 1
#   (i) Unexpected status (500) -> generic error with response body, exit 1
#
# Always exits 0 on success, non-zero on failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DELETE="$SCRIPT_DIR/delete_gh_comment.sh"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ── Fake curl ──────────────────────────────────────────────────────────────
# Records "<method> <url>" lines to $CURL_LOG, writes $MOCK_BODY to the file
# named by -o (if any), and prints $MOCK_HTTP_STATUS (default 204) to stdout
# with no trailing newline, matching `curl -w '%{http_code}'`.
FAKE_BIN="$TMP/bin"
mkdir -p "$FAKE_BIN"
cat > "$FAKE_BIN/curl" <<'FAKE'
#!/usr/bin/env bash
method="GET"; url=""; outfile=""; prev=""
for a in "$@"; do
  case "$prev" in
    -X) method="$a" ;;
    -o) outfile="$a" ;;
  esac
  case "$a" in
    https://*) url="$a" ;;
  esac
  prev="$a"
done
echo "$method $url" >> "$CURL_LOG"
if [[ -n "$outfile" ]]; then
  printf '%s' "${MOCK_BODY:-}" > "$outfile"
fi
printf '%s' "${MOCK_HTTP_STATUS:-204}"
FAKE
chmod +x "$FAKE_BIN/curl"

CURL_LOG="$TMP/curl.log"

# Run DELETE with the fake curl on PATH. Args: <status> <body> <script-args...>
# Sets RC to the exit code and OUT/ERR to captured stdout/stderr.
run() {
  local status="$1" body="$2"
  shift 2
  : > "$CURL_LOG"
  local out_file="$TMP/out.txt" err_file="$TMP/err.txt"
  PATH="$FAKE_BIN:$PATH" \
    CURL_LOG="$CURL_LOG" MOCK_HTTP_STATUS="$status" MOCK_BODY="$body" \
    GITHUB_TOKEN="${GITHUB_TOKEN:-fake-token}" \
    bash "$DELETE" "$@" > "$out_file" 2> "$err_file"
  RC=$?
  OUT="$(cat "$out_file")"
  ERR="$(cat "$err_file")"
}

# ── (a) Wrong argument count -> usage error, exit 1, curl not invoked ────────
echo ""
echo "=== (a) wrong argument count -> usage error ==="
run 204 "" aunger gallery-button-for-pixel-camera
if [[ "$RC" -eq 1 ]]; then pass "missing comment_id exits 1"; else fail "expected exit 1, got $RC"; fi
if echo "$ERR" | grep -q "Usage:"; then pass "usage message printed on stderr"; else fail "expected usage message, got: $ERR"; fi
if [[ ! -s "$CURL_LOG" ]]; then pass "curl not invoked for bad argument count"; else fail "curl was unexpectedly invoked: $(cat "$CURL_LOG")"; fi

# ── (b) -h/--help prints usage, exit 1 ───────────────────────────────────────
echo ""
echo "=== (b) -h/--help prints usage ==="
run 204 "" -h
if [[ "$RC" -eq 1 ]]; then pass "-h exits 1"; else fail "expected exit 1, got $RC"; fi
if echo "$OUT" | grep -q "Usage:"; then pass "-h prints usage text"; else fail "expected usage text, got: $OUT"; fi

# ── (c) GITHUB_TOKEN unset -> error, exit 1, curl not invoked ───────────────
echo ""
echo "=== (c) GITHUB_TOKEN unset -> error ==="
: > "$CURL_LOG"
env -u GITHUB_TOKEN PATH="$FAKE_BIN:$PATH" CURL_LOG="$CURL_LOG" MOCK_HTTP_STATUS=204 \
  bash "$DELETE" aunger gallery-button-for-pixel-camera 123 > "$TMP/out.txt" 2> "$TMP/err.txt"
RC=$?
OUT="$(cat "$TMP/out.txt")"; ERR="$(cat "$TMP/err.txt")"
if [[ "$RC" -eq 1 ]]; then pass "missing GITHUB_TOKEN exits 1"; else fail "expected exit 1, got $RC"; fi
if echo "$ERR" | grep -q "GITHUB_TOKEN"; then pass "error mentions GITHUB_TOKEN"; else fail "expected GITHUB_TOKEN error, got: $ERR"; fi
if [[ ! -s "$CURL_LOG" ]]; then pass "curl not invoked without GITHUB_TOKEN"; else fail "curl was unexpectedly invoked: $(cat "$CURL_LOG")"; fi

# ── (d) Non-numeric comment_id -> error, exit 1, curl not invoked ──────────────
echo ""
echo "=== (d) non-numeric comment_id -> error ==="
run 204 "" aunger gallery-button-for-pixel-camera abc123
if [[ "$RC" -eq 1 ]]; then pass "non-numeric comment_id exits 1"; else fail "expected exit 1, got $RC"; fi
if echo "$ERR" | grep -q "numeric"; then pass "error mentions comment_id must be numeric"; else fail "expected numeric error, got: $ERR"; fi
if [[ ! -s "$CURL_LOG" ]]; then pass "curl not invoked for non-numeric comment_id"; else fail "curl was unexpectedly invoked: $(cat "$CURL_LOG")"; fi

# ── (e) 204 response -> success, exit 0, correct URL ─────────────────────────
echo ""
echo "=== (e) 204 response -> success ==="
run 204 "" aunger gallery-button-for-pixel-camera 4940940270
if [[ "$RC" -eq 0 ]]; then pass "204 response exits 0"; else fail "expected exit 0, got $RC: $ERR"; fi
if echo "$OUT" | grep -q "Deleted comment 4940940270"; then
  pass "success message names the deleted comment"
else
  fail "expected success message, got: $OUT"
fi
if grep -q "^DELETE https://api.github.com/repos/aunger/gallery-button-for-pixel-camera/issues/comments/4940940270$" "$CURL_LOG"; then
  pass "DELETE issued against the correct issues/comments URL"
else
  fail "unexpected curl invocation: $(cat "$CURL_LOG")"
fi

# ── (f) 404 response -> "not found" error, exit 1 ────────────────────────────
echo ""
echo "=== (f) 404 response -> not found error ==="
run 404 "" aunger gallery-button-for-pixel-camera 999
if [[ "$RC" -eq 1 ]]; then pass "404 response exits 1"; else fail "expected exit 1, got $RC"; fi
if echo "$ERR" | grep -q "not found"; then pass "error message mentions not found"; else fail "expected not-found error, got: $ERR"; fi

# ── (g) 403 response -> "forbidden" error with body, exit 1 ──────────────────
echo ""
echo "=== (g) 403 response -> forbidden error with body ==="
run 403 '{"message":"Resource not accessible"}' aunger gallery-button-for-pixel-camera 999
if [[ "$RC" -eq 1 ]]; then pass "403 response exits 1"; else fail "expected exit 1, got $RC"; fi
if echo "$ERR" | grep -q "forbidden"; then pass "error message mentions forbidden"; else fail "expected forbidden error, got: $ERR"; fi
if echo "$ERR" | grep -q "Resource not accessible"; then
  pass "response body surfaced on stderr"
else
  fail "expected response body in stderr, got: $ERR"
fi

# ── (h) 401 response -> "unauthorized" error, exit 1 ─────────────────────────
echo ""
echo "=== (h) 401 response -> unauthorized error ==="
run 401 "" aunger gallery-button-for-pixel-camera 999
if [[ "$RC" -eq 1 ]]; then pass "401 response exits 1"; else fail "expected exit 1, got $RC"; fi
if echo "$ERR" | grep -q "unauthorized"; then pass "error message mentions unauthorized"; else fail "expected unauthorized error, got: $ERR"; fi

# ── (i) Unexpected status -> generic error with body, exit 1 ─────────────────
echo ""
echo "=== (i) unexpected status (500) -> generic error with body ==="
run 500 '{"message":"Internal Server Error"}' aunger gallery-button-for-pixel-camera 999
if [[ "$RC" -eq 1 ]]; then pass "500 response exits 1"; else fail "expected exit 1, got $RC"; fi
if echo "$ERR" | grep -q "unexpected HTTP status 500"; then
  pass "error message reports the unexpected status code"
else
  fail "expected unexpected-status error, got: $ERR"
fi
if echo "$ERR" | grep -q "Internal Server Error"; then
  pass "response body surfaced on stderr for unexpected status"
else
  fail "expected response body in stderr, got: $ERR"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed."
if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
