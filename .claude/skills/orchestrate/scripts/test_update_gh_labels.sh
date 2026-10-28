#!/usr/bin/env bash
# test_update_gh_labels.sh: Shell-based tests for update_gh_labels.sh.
#
# Runs the script against a fake `curl` on PATH instead of the real one, so no
# live GitHub token or network is needed. The fake curl records "<method>
# <url>" (and, when present, "DATA: <-d body>") lines to $CURL_LOG in call
# order, then pops one status code and one response body off two queue files
# ($STATUS_QUEUE / $BODY_QUEUE, one line per curl invocation the test expects)
# to answer that call -- mirroring how the real `curl -w '%{http_code}'`
# behaves once -o diverts the response body away. This lets a single test
# case script a whole sequence of calls (e.g. two DELETEs then one POST) with
# an independent status/body per call.
#
# Covers:
#   (a) Wrong argument count -> usage error, exit 1, curl not invoked
#   (b) -h/--help prints usage, exit 1
#   (c) GITHUB_TOKEN unset -> error, exit 1, curl not invoked
#   (d) Non-numeric issue number -> error, exit 1, curl not invoked
#   (e) --add with no label value -> usage error, exit 1
#   (f) --remove with no label value -> usage error, exit 1
#   (g) No --add/--remove at all -> error, exit 1, curl not invoked
#   (h) Unrecognized flag -> error, exit 1, curl not invoked
#   (i) Same label in --add and --remove -> error, exit 1, curl not invoked
#   (j) --remove success (200) -> success message, exit 0, correct DELETE URL,
#       label with a space is percent-encoded in the URL
#   (k) --remove 404 -> "already absent" message, exit 0 (idempotent, not a failure)
#   (l) --remove 403 -> forbidden error with body, exit 1
#   (m) --remove 401 -> unauthorized error, exit 1
#   (n) --remove unexpected status (500) -> generic error with body, exit 1
#   (o) --add success (200) -> success message, exit 0, correct POST URL and
#       JSON body {"labels":[...]} for multiple --add flags
#   (p) --add 404 -> error, exit 1
#   (q) Combined --remove X --add Y, both succeed -> DELETE issued before
#       POST, exit 0
#   (r) Combined call where remove succeeds but add fails -> both calls are
#       still made (no early abort mid-script), overall exit 1
#
# Always exits 0 on success, non-zero on failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UPDATE="$SCRIPT_DIR/update_gh_labels.sh"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ── Fake curl ──────────────────────────────────────────────────────────────
FAKE_BIN="$TMP/bin"
mkdir -p "$FAKE_BIN"
cat > "$FAKE_BIN/curl" <<'FAKE'
#!/usr/bin/env bash
method="GET"; url=""; outfile=""; data=""; prev=""
for a in "$@"; do
  case "$prev" in
    -X) method="$a" ;;
    -o) outfile="$a" ;;
    -d) data="$a" ;;
  esac
  case "$a" in
    https://*) url="$a" ;;
  esac
  prev="$a"
done
{
  echo "$method $url"
  if [[ -n "$data" ]]; then
    echo "DATA: $data"
  fi
} >> "$CURL_LOG"

STATUS="$(head -n1 "$STATUS_QUEUE")"
sed -i '1d' "$STATUS_QUEUE"
BODY="$(head -n1 "$BODY_QUEUE")"
sed -i '1d' "$BODY_QUEUE"

if [[ -n "$outfile" ]]; then
  printf '%s' "$BODY" > "$outfile"
fi
printf '%s' "$STATUS"
FAKE
chmod +x "$FAKE_BIN/curl"

CURL_LOG="$TMP/curl.log"
STATUS_QUEUE="$TMP/status.queue"
BODY_QUEUE="$TMP/body.queue"

# enqueue <status> [body]: append one call's canned response to the queues.
enqueue() {
  echo "$1" >> "$STATUS_QUEUE"
  echo "${2:-}" >> "$BODY_QUEUE"
}

# Run UPDATE with the fake curl on PATH, consuming the queued responses in
# order. Args: the script's own arguments. Sets RC/OUT/ERR.
run() {
  local out_file="$TMP/out.txt" err_file="$TMP/err.txt"
  PATH="$FAKE_BIN:$PATH" \
    CURL_LOG="$CURL_LOG" STATUS_QUEUE="$STATUS_QUEUE" BODY_QUEUE="$BODY_QUEUE" \
    GITHUB_TOKEN="${GITHUB_TOKEN:-fake-token}" \
    bash "$UPDATE" "$@" > "$out_file" 2> "$err_file"
  RC=$?
  OUT="$(cat "$out_file")"
  ERR="$(cat "$err_file")"
}

# reset: clear the curl log and both queues before a fresh test case.
reset() {
  : > "$CURL_LOG"
  : > "$STATUS_QUEUE"
  : > "$BODY_QUEUE"
}

# ── (a) Wrong argument count -> usage error, exit 1, curl not invoked ────────
echo ""
echo "=== (a) wrong argument count -> usage error ==="
reset
run aunger gallery-button-for-pixel-camera
if [[ "$RC" -eq 1 ]]; then pass "missing issue number exits 1"; else fail "expected exit 1, got $RC"; fi
if echo "$ERR" | grep -q "Usage:"; then pass "usage message printed on stderr"; else fail "expected usage message, got: $ERR"; fi
if [[ ! -s "$CURL_LOG" ]]; then pass "curl not invoked for bad argument count"; else fail "curl was unexpectedly invoked: $(cat "$CURL_LOG")"; fi

# ── (b) -h/--help prints usage, exit 1 ───────────────────────────────────────
echo ""
echo "=== (b) -h/--help prints usage ==="
reset
run -h
if [[ "$RC" -eq 1 ]]; then pass "-h exits 1"; else fail "expected exit 1, got $RC"; fi
if echo "$OUT" | grep -q "Usage:"; then pass "-h prints usage text"; else fail "expected usage text, got: $OUT"; fi

# ── (c) GITHUB_TOKEN unset -> error, exit 1, curl not invoked ───────────────
echo ""
echo "=== (c) GITHUB_TOKEN unset -> error ==="
reset
env -u GITHUB_TOKEN PATH="$FAKE_BIN:$PATH" CURL_LOG="$CURL_LOG" STATUS_QUEUE="$STATUS_QUEUE" BODY_QUEUE="$BODY_QUEUE" \
  bash "$UPDATE" aunger gallery-button-for-pixel-camera 710 --remove orchestrate > "$TMP/out.txt" 2> "$TMP/err.txt"
RC=$?
OUT="$(cat "$TMP/out.txt")"; ERR="$(cat "$TMP/err.txt")"
if [[ "$RC" -eq 1 ]]; then pass "missing GITHUB_TOKEN exits 1"; else fail "expected exit 1, got $RC"; fi
if echo "$ERR" | grep -q "GITHUB_TOKEN"; then pass "error mentions GITHUB_TOKEN"; else fail "expected GITHUB_TOKEN error, got: $ERR"; fi
if [[ ! -s "$CURL_LOG" ]]; then pass "curl not invoked without GITHUB_TOKEN"; else fail "curl was unexpectedly invoked: $(cat "$CURL_LOG")"; fi

# ── (d) Non-numeric issue number -> error, exit 1, curl not invoked ─────────
echo ""
echo "=== (d) non-numeric issue number -> error ==="
reset
run aunger gallery-button-for-pixel-camera abc123 --add foo
if [[ "$RC" -eq 1 ]]; then pass "non-numeric issue number exits 1"; else fail "expected exit 1, got $RC"; fi
if echo "$ERR" | grep -q "numeric"; then pass "error mentions issue number must be numeric"; else fail "expected numeric error, got: $ERR"; fi
if [[ ! -s "$CURL_LOG" ]]; then pass "curl not invoked for non-numeric issue number"; else fail "curl was unexpectedly invoked: $(cat "$CURL_LOG")"; fi

# ── (e) --add with no label value -> usage error ────────────────────────────
echo ""
echo "=== (e) --add with no label value -> usage error ==="
reset
run aunger gallery-button-for-pixel-camera 710 --add
if [[ "$RC" -eq 1 ]]; then pass "--add with no value exits 1"; else fail "expected exit 1, got $RC"; fi
if echo "$ERR" | grep -q -- "--add requires a label"; then pass "error names --add"; else fail "expected --add error, got: $ERR"; fi

# ── (f) --remove with no label value -> usage error ─────────────────────────
echo ""
echo "=== (f) --remove with no label value -> usage error ==="
reset
run aunger gallery-button-for-pixel-camera 710 --remove
if [[ "$RC" -eq 1 ]]; then pass "--remove with no value exits 1"; else fail "expected exit 1, got $RC"; fi
if echo "$ERR" | grep -q -- "--remove requires a label"; then pass "error names --remove"; else fail "expected --remove error, got: $ERR"; fi

# ── (g) No --add/--remove at all -> error, exit 1, curl not invoked ─────────
echo ""
echo "=== (g) no --add/--remove -> error ==="
reset
run aunger gallery-button-for-pixel-camera 710
if [[ "$RC" -eq 1 ]]; then pass "no --add/--remove exits 1"; else fail "expected exit 1, got $RC"; fi
if echo "$ERR" | grep -q "at least one --add or --remove"; then pass "error explains at least one flag is required"; else fail "expected requirement error, got: $ERR"; fi
if [[ ! -s "$CURL_LOG" ]]; then pass "curl not invoked with no flags"; else fail "curl was unexpectedly invoked: $(cat "$CURL_LOG")"; fi

# ── (h) Unrecognized flag -> error, exit 1, curl not invoked ────────────────
echo ""
echo "=== (h) unrecognized flag -> error ==="
reset
run aunger gallery-button-for-pixel-camera 710 --bogus foo
if [[ "$RC" -eq 1 ]]; then pass "unrecognized flag exits 1"; else fail "expected exit 1, got $RC"; fi
if echo "$ERR" | grep -q "unrecognized argument"; then pass "error names the unrecognized argument"; else fail "expected unrecognized-argument error, got: $ERR"; fi
if [[ ! -s "$CURL_LOG" ]]; then pass "curl not invoked for unrecognized flag"; else fail "curl was unexpectedly invoked: $(cat "$CURL_LOG")"; fi

# ── (i) Same label in --add and --remove -> error, exit 1, curl not invoked ─
echo ""
echo "=== (i) label in both --add and --remove -> error ==="
reset
run aunger gallery-button-for-pixel-camera 710 --add orchestrating --remove orchestrating
if [[ "$RC" -eq 1 ]]; then pass "conflicting add/remove exits 1"; else fail "expected exit 1, got $RC"; fi
if echo "$ERR" | grep -q "cannot be both added and removed"; then pass "error explains the conflict"; else fail "expected conflict error, got: $ERR"; fi
if [[ ! -s "$CURL_LOG" ]]; then pass "curl not invoked for conflicting flags"; else fail "curl was unexpectedly invoked: $(cat "$CURL_LOG")"; fi

# ── (j) --remove success (200), label with a space is percent-encoded ───────
echo ""
echo "=== (j) --remove 200 -> success, URL percent-encodes the label ==="
reset
enqueue 200 ""
run aunger gallery-button-for-pixel-camera 710 --remove "verification needed"
if [[ "$RC" -eq 0 ]]; then pass "200 response exits 0"; else fail "expected exit 0, got $RC: $ERR"; fi
if echo "$OUT" | grep -q "Removed label 'verification needed'"; then
  pass "success message names the removed label"
else
  fail "expected success message, got: $OUT"
fi
if grep -q "^DELETE https://api.github.com/repos/aunger/gallery-button-for-pixel-camera/issues/710/labels/verification%20needed$" "$CURL_LOG"; then
  pass "DELETE issued against the correct percent-encoded labels URL"
else
  fail "unexpected curl invocation: $(cat "$CURL_LOG")"
fi

# ── (k) --remove 404 -> already-absent message, exit 0 (idempotent) ─────────
echo ""
echo "=== (k) --remove 404 -> already absent, exit 0 ==="
reset
enqueue 404 ""
run aunger gallery-button-for-pixel-camera 710 --remove orchestrate
if [[ "$RC" -eq 0 ]]; then pass "404 on removal exits 0 (idempotent)"; else fail "expected exit 0, got $RC: $ERR"; fi
if echo "$OUT" | grep -q "already absent"; then pass "output notes the label was already absent"; else fail "expected already-absent message, got: $OUT"; fi

# ── (l) --remove 403 -> forbidden error with body, exit 1 ───────────────────
echo ""
echo "=== (l) --remove 403 -> forbidden error with body ==="
reset
enqueue 403 '{"message":"Resource not accessible"}'
run aunger gallery-button-for-pixel-camera 710 --remove orchestrate
if [[ "$RC" -eq 1 ]]; then pass "403 response exits 1"; else fail "expected exit 1, got $RC"; fi
if echo "$ERR" | grep -q "forbidden"; then pass "error message mentions forbidden"; else fail "expected forbidden error, got: $ERR"; fi
if echo "$ERR" | grep -q "Resource not accessible"; then pass "response body surfaced on stderr"; else fail "expected response body in stderr, got: $ERR"; fi

# ── (m) --remove 401 -> unauthorized error, exit 1 ───────────────────────────
echo ""
echo "=== (m) --remove 401 -> unauthorized error ==="
reset
enqueue 401 ""
run aunger gallery-button-for-pixel-camera 710 --remove orchestrate
if [[ "$RC" -eq 1 ]]; then pass "401 response exits 1"; else fail "expected exit 1, got $RC"; fi
if echo "$ERR" | grep -q "unauthorized"; then pass "error message mentions unauthorized"; else fail "expected unauthorized error, got: $ERR"; fi

# ── (n) --remove unexpected status (500) -> generic error with body ─────────
echo ""
echo "=== (n) --remove 500 -> generic error with body ==="
reset
enqueue 500 '{"message":"Internal Server Error"}'
run aunger gallery-button-for-pixel-camera 710 --remove orchestrate
if [[ "$RC" -eq 1 ]]; then pass "500 response exits 1"; else fail "expected exit 1, got $RC"; fi
if echo "$ERR" | grep -q "unexpected HTTP status 500"; then pass "error reports the unexpected status code"; else fail "expected unexpected-status error, got: $ERR"; fi
if echo "$ERR" | grep -q "Internal Server Error"; then pass "response body surfaced on stderr"; else fail "expected response body in stderr, got: $ERR"; fi

# ── (o) --add success (200), JSON body carries every --add label ────────────
echo ""
echo "=== (o) --add 200 -> success, correct POST URL and JSON body ==="
reset
enqueue 200 ""
run aunger gallery-button-for-pixel-camera 710 --add "changes done" --add verified
if [[ "$RC" -eq 0 ]]; then pass "200 response exits 0"; else fail "expected exit 0, got $RC: $ERR"; fi
if echo "$OUT" | grep -q "Added label"; then pass "success message reported"; else fail "expected success message, got: $OUT"; fi
if grep -q "^POST https://api.github.com/repos/aunger/gallery-button-for-pixel-camera/issues/710/labels$" "$CURL_LOG"; then
  pass "POST issued against the correct labels URL"
else
  fail "unexpected curl invocation: $(cat "$CURL_LOG")"
fi
DATA_LINE="$(grep '^DATA: ' "$CURL_LOG" | sed 's/^DATA: //')"
if echo "$DATA_LINE" | jq -e '.labels == ["changes done", "verified"]' > /dev/null 2>&1; then
  pass "POST body carries both add labels in order, unescaped correctly"
else
  fail "unexpected POST body: $DATA_LINE"
fi

# ── (p) --add 404 -> error, exit 1 ───────────────────────────────────────────
echo ""
echo "=== (p) --add 404 -> error ==="
reset
enqueue 404 ""
run aunger gallery-button-for-pixel-camera 710 --add verified
if [[ "$RC" -eq 1 ]]; then pass "404 on add exits 1"; else fail "expected exit 1, got $RC"; fi
if echo "$ERR" | grep -q "not found"; then pass "error mentions not found"; else fail "expected not-found error, got: $ERR"; fi

# ── (q) Combined --remove/--add: DELETE issued before POST, both succeed ────
echo ""
echo "=== (q) combined --remove/--add -> DELETE before POST, exit 0 ==="
reset
enqueue 200 ""
enqueue 200 ""
run aunger gallery-button-for-pixel-camera 710 --remove orchestrate --add orchestrating
if [[ "$RC" -eq 0 ]]; then pass "combined call exits 0"; else fail "expected exit 0, got $RC: $ERR"; fi
CALLS="$(grep -E '^(DELETE|POST) ' "$CURL_LOG")"
FIRST_METHOD="$(echo "$CALLS" | head -n1 | cut -d' ' -f1)"
SECOND_METHOD="$(echo "$CALLS" | sed -n '2p' | cut -d' ' -f1)"
if [[ "$FIRST_METHOD" == "DELETE" && "$SECOND_METHOD" == "POST" ]]; then
  pass "DELETE (remove) is issued before POST (add)"
else
  fail "expected DELETE then POST, got: $CALLS"
fi

# ── (r) Combined call: remove succeeds, add fails -> both calls still made ──
echo ""
echo "=== (r) combined --remove/--add, add fails -> remove still runs, overall exit 1 ==="
reset
enqueue 200 ""
enqueue 403 '{"message":"Resource not accessible"}'
run aunger gallery-button-for-pixel-camera 710 --remove orchestrate --add orchestrating
if [[ "$RC" -eq 1 ]]; then pass "overall call exits 1 when the add leg fails"; else fail "expected exit 1, got $RC: $ERR"; fi
if echo "$OUT" | grep -q "Removed label 'orchestrate'"; then
  pass "the remove leg still completed and reported success"
else
  fail "expected the remove leg to have run, got: $OUT"
fi
CALL_COUNT="$(grep -cE '^(DELETE|POST) ' "$CURL_LOG")"
if [[ "$CALL_COUNT" -eq 2 ]]; then
  pass "both the DELETE and POST calls were made (no early abort)"
else
  fail "expected 2 calls, got $CALL_COUNT: $(cat "$CURL_LOG")"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed."
if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
