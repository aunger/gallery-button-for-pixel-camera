#!/usr/bin/env bash
# test_detect_launch_retry.sh -- Shell-based tests for detect_launch_retry.sh.
#
# The detection/exit-code tests (a)-(g) run without GITHUB_ACTIONS=true, so the
# issue-filing and ::warning path is not exercised by them.
#
# The notification tests (h)-(m) DO exercise that GITHUB_ACTIONS-gated path. They
# run the script with GITHUB_ACTIONS=true but with a fake `curl` on PATH instead
# of the real one, so no live GitHub token or network is needed. The fake curl
# serves a canned issue-state response for the GET, records the PATCH (reopen),
# and captures the POST request body so the test can assert on the comment JSON.
# This gives end-to-end coverage of the reopen-if-closed branch and the
# comment-body construction (issue #463), the exact path where the two review
# bugs lived (Bug 1: backslash-n not rendering as newlines; Bug 2: raw logcat
# interpolated into JSON without escaping).
#
# Covers:
#   (a) Attempt-1-only logcat (the steady state across 26+ runs) -> no signal, exit 0
#   (b) A retry "am start attempt 2/3" line -> signal, exit 10
#   (c) A "overlay active on attempt 2" recovery line -> signal, exit 10
#   (d) A "overlay still inactive after ... attempts" exhaustion Log.w -> signal, exit 10
#   (e) A "first-launch teardown race" Log.w -> signal, exit 10
#   (f) Reads from stdin when no path argument is given
#   (g) "attempt 1/3" and "overlay active on attempt 1" alone never match
#   (h) Closed issue -> reopen PATCH {"state":"open"} is sent, then a comment POST
#   (i) Open issue -> no reopen PATCH, comment POST still sent
#   (j) Comment body has real newlines and an intact code fence (Bug 1)
#   (k) Logcat with quotes, backslashes, tabs -> valid, correctly escaped JSON (Bug 2)
#   (l) No GITHUB_RUN_ID -> shorter comment body, still valid JSON
#   (m) GITHUB_ACTIONS unset -> no curl is invoked at all
#
# Always exits 0 on success, non-zero on failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECT="$SCRIPT_DIR/detect_launch_retry.sh"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

# Run DETECT on the given input file, capturing exit code into RC.
run_file() {
  bash "$DETECT" "$1" > /dev/null 2>&1
  RC=$?
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ── Fake curl, used by the notification tests (h)-(m) ─────────────────────────
# Stands in for the real curl on PATH. It distinguishes the three calls the
# script makes by their method/URL:
#   - GET  issues/364           -> prints a canned issue JSON whose "state" is
#                                  taken from $FAKE_ISSUE_STATE (default "open").
#   - PATCH issues/364          -> appends "PATCH <url>" and the -d body to
#                                  $CURL_LOG (this is the reopen call).
#   - POST issues/364/comments  -> appends "POST <url>" to $CURL_LOG and writes
#                                  the -d body (the comment JSON) to $POST_BODY_FILE.
# It always succeeds so the script's `curl -fsSL` calls do not trip set -e.
FAKE_BIN="$TMP/bin"
mkdir -p "$FAKE_BIN"
cat > "$FAKE_BIN/curl" <<'FAKE'
#!/usr/bin/env bash
method="GET"; data=""; url=""; prev=""
for a in "$@"; do
  case "$prev" in
    -X) method="$a" ;;
    -d) data="$a" ;;
  esac
  case "$a" in
    https://*) url="$a" ;;
  esac
  prev="$a"
done
if [[ "$method" == "PATCH" ]]; then
  { echo "PATCH $url"; echo "$data"; } >> "$CURL_LOG"
  exit 0
fi
if [[ "$method" == "POST" ]]; then
  echo "POST $url" >> "$CURL_LOG"
  printf '%s' "$data" > "$POST_BODY_FILE"
  exit 0
fi
echo "GET $url" >> "$CURL_LOG"
printf '{"number":364,"state":"%s","title":"watch item"}' "${FAKE_ISSUE_STATE:-open}"
exit 0
FAKE
chmod +x "$FAKE_BIN/curl"

# Run DETECT against the live notification path with the fake curl on PATH.
# Args: <logcat-file> <issue-state: open|closed> [run-id]
# Resets the curl log and post-body file, sets RC to the script's exit code, and
# leaves $CURL_LOG / $POST_BODY_FILE for the caller to inspect.
CURL_LOG="$TMP/curl.log"
POST_BODY_FILE="$TMP/post-body.json"
run_notify() {
  : > "$CURL_LOG"
  rm -f "$POST_BODY_FILE"
  local logcat="$1" state="$2" run_id="${3:-}"
  PATH="$FAKE_BIN:$PATH" \
    CURL_LOG="$CURL_LOG" POST_BODY_FILE="$POST_BODY_FILE" \
    GITHUB_ACTIONS=true \
    GITHUB_TOKEN=fake-token \
    GITHUB_REPOSITORY=aunger/gallery-button-for-pixel-camera \
    GITHUB_SERVER_URL=https://github.com \
    GITHUB_RUN_ID="$run_id" \
    FAKE_ISSUE_STATE="$state" \
    bash "$DETECT" "$logcat" > /dev/null 2>&1
  RC=$?
}

# ── (a) Attempt-1-only logcat -> no signal, exit 0 ───────────────────────────
echo ""
echo "=== (a) Attempt-1-only logcat -> no signal, exit 0 ==="
cat > "$TMP/a.txt" <<'EOF'
07:28:58.933  TestRunner: started: overlayAppearsWhenViewfinderOpens
07:29:01.315  GB4PC_E2E: launchPixelCamera: am start attempt 1/3
07:29:03.287  GB4PC_E2E: launchPixelCamera: overlay active on attempt 1
07:29:03.306  TestRunner: finished: overlayAppearsWhenViewfinderOpens
EOF
run_file "$TMP/a.txt"
if [[ "$RC" -eq 0 ]]; then pass "attempt-1-only exits 0"; else fail "expected exit 0, got $RC"; fi

# ── (b) "am start attempt 2/3" -> signal, exit 10 ────────────────────────────
echo ""
echo "=== (b) am start attempt 2/3 -> signal, exit 10 ==="
cat > "$TMP/b.txt" <<'EOF'
07:29:01.315  GB4PC_E2E: launchPixelCamera: am start attempt 1/3
07:29:05.000  GB4PC_E2E: launchPixelCamera: am start attempt 2/3
07:29:06.000  GB4PC_E2E: launchPixelCamera: overlay active on attempt 2
EOF
run_file "$TMP/b.txt"
if [[ "$RC" -eq 10 ]]; then pass "attempt 2/3 exits 10"; else fail "expected exit 10, got $RC"; fi

# ── (c) "overlay active on attempt 2" recovery -> signal, exit 10 ────────────
echo ""
echo "=== (c) overlay active on attempt 2 -> signal, exit 10 ==="
cat > "$TMP/c.txt" <<'EOF'
07:29:06.000  GB4PC_E2E: launchPixelCamera: overlay active on attempt 2
EOF
run_file "$TMP/c.txt"
if [[ "$RC" -eq 10 ]]; then pass "recovery line exits 10"; else fail "expected exit 10, got $RC"; fi

# ── (d) exhaustion Log.w -> signal, exit 10 ──────────────────────────────────
echo ""
echo "=== (d) overlay still inactive after N attempts -> signal, exit 10 ==="
cat > "$TMP/d.txt" <<'EOF'
07:29:10.000  GB4PC_E2E: launchPixelCamera: overlay still inactive after 3 attempts; letting the caller's own assertion fail
EOF
run_file "$TMP/d.txt"
if [[ "$RC" -eq 10 ]]; then pass "exhaustion line exits 10"; else fail "expected exit 10, got $RC"; fi

# ── (e) "first-launch teardown race" Log.w -> signal, exit 10 ────────────────
echo ""
echo "=== (e) first-launch teardown race -> signal, exit 10 ==="
cat > "$TMP/e.txt" <<'EOF'
07:29:05.000  GB4PC_E2E: launchPixelCamera: overlay still inactive after 30000 ms on attempt 1 (first-launch teardown race); re-issuing am start
EOF
run_file "$TMP/e.txt"
if [[ "$RC" -eq 10 ]]; then pass "teardown-race line exits 10"; else fail "expected exit 10, got $RC"; fi

# ── (f) reads from stdin when no path argument is given ──────────────────────
echo ""
echo "=== (f) reads from stdin when no argument ==="
OUT="$(printf '%s\n' 'GB4PC_E2E: launchPixelCamera: am start attempt 2/3' | bash "$DETECT" 2>&1)"
RC=$?
if [[ "$RC" -eq 10 ]] && echo "$OUT" | grep -q "attempt 2/3"; then
  pass "stdin path detected and exits 10"
else
  fail "stdin path: expected exit 10 with match, got rc=$RC out='$OUT'"
fi

# ── (g) attempt 1 lines alone never match ────────────────────────────────────
echo ""
echo "=== (g) attempt 1 lines alone never match ==="
OUT="$(printf '%s\n%s\n' \
  'GB4PC_E2E: launchPixelCamera: am start attempt 1/3' \
  'GB4PC_E2E: launchPixelCamera: overlay active on attempt 1' | bash "$DETECT" 2>&1)"
RC=$?
if [[ "$RC" -eq 0 ]]; then pass "attempt 1 lines do not trigger"; else fail "expected exit 0, got $RC out='$OUT'"; fi

# ── (h) closed issue -> reopen PATCH is sent, then a comment POST ─────────────
echo ""
echo "=== (h) closed issue -> reopen PATCH then comment POST ==="
printf '%s\n' '07:29:05.000  GB4PC_E2E: launchPixelCamera: am start attempt 2/3' > "$TMP/h.txt"
run_notify "$TMP/h.txt" closed 12345
if [[ "$RC" -eq 10 ]]; then pass "notification path exits 10"; else fail "expected exit 10, got $RC"; fi
if grep -q '^PATCH .*/issues/364$' "$CURL_LOG"; then
  pass "reopen PATCH sent for closed issue"
else
  fail "expected a PATCH to issues/364, log was: $(cat "$CURL_LOG")"
fi
if grep -qF '{"state":"open"}' "$CURL_LOG"; then
  pass "reopen PATCH body is {\"state\":\"open\"}"
else
  fail "expected reopen body {\"state\":\"open\"}, log was: $(cat "$CURL_LOG")"
fi
if grep -q '^POST .*/issues/364/comments$' "$CURL_LOG"; then
  pass "comment POST sent"
else
  fail "expected a POST to issues/364/comments, log was: $(cat "$CURL_LOG")"
fi

# ── (i) open issue -> no reopen PATCH, comment POST still sent ─────────────────
echo ""
echo "=== (i) open issue -> no reopen PATCH, comment POST still sent ==="
run_notify "$TMP/h.txt" open 12345
if grep -q '^PATCH ' "$CURL_LOG"; then
  fail "unexpected PATCH for an already-open issue: $(cat "$CURL_LOG")"
else
  pass "no reopen PATCH when issue already open"
fi
if grep -q '^POST .*/issues/364/comments$' "$CURL_LOG"; then
  pass "comment POST still sent when issue already open"
else
  fail "expected a comment POST, log was: $(cat "$CURL_LOG")"
fi

# ── (j) comment body has real newlines and an intact code fence (Bug 1) ───────
echo ""
echo "=== (j) comment body has real newlines and an intact code fence (Bug 1) ==="
run_notify "$TMP/h.txt" open 12345
# jq -r '.body' fails outright if the JSON is malformed, so a successful parse
# is itself part of the assertion.
BODY="$(jq -r '.body' "$POST_BODY_FILE" 2>/dev/null)"
PARSE_RC=$?
if [[ "$PARSE_RC" -ne 0 ]]; then
  fail "comment JSON did not parse: $(cat "$POST_BODY_FILE")"
elif [[ "$(printf '%s' "$BODY" | wc -l)" -ge 3 ]]; then
  pass "comment body contains real newlines (multi-line)"
else
  fail "comment body has no real line breaks (Bug 1 regression): $BODY"
fi
# The code fence must be on its own lines: a literal backslash-n would have left
# the fence glued to surrounding text on a single line.
FENCE='```'
if printf '%s\n' "$BODY" | grep -qx "$FENCE"; then
  pass "code fence appears on its own line"
else
  fail "code fence is not on its own line (Bug 1 regression): $BODY"
fi
if ! printf '%s' "$BODY" | grep -q '\\n'; then
  pass "comment body contains no literal backslash-n"
else
  fail "comment body contains literal backslash-n (Bug 1 regression): $BODY"
fi
# The run link is rendered with the run id and URL.
if printf '%s' "$BODY" | grep -qF '[run 12345](https://github.com/aunger/gallery-button-for-pixel-camera/actions/runs/12345)'; then
  pass "comment body links to the triggering run"
else
  fail "comment body is missing the run link: $BODY"
fi

# ── (k) logcat with quotes, backslashes, tabs -> valid, escaped JSON (Bug 2) ──
echo ""
echo "=== (k) logcat with quotes/backslashes/tabs -> valid escaped JSON (Bug 2) ==="
# A logcat line carrying every character class that broke the old hand-rolled
# JSON: a double quote, a backslash, and a literal tab.
printf '%s\n' 'GB4PC_E2E: am start attempt 2/3 "quoted" back\slash	tabbed' > "$TMP/k.txt"
run_notify "$TMP/k.txt" open 12345
if jq -e . "$POST_BODY_FILE" > /dev/null 2>&1; then
  pass "comment JSON is well-formed despite quotes/backslashes/tabs"
else
  fail "comment JSON is malformed (Bug 2 regression): $(cat "$POST_BODY_FILE")"
fi
# The raw logcat must round-trip through the JSON unchanged.
BODY="$(jq -r '.body' "$POST_BODY_FILE" 2>/dev/null)"
if printf '%s' "$BODY" | grep -qF 'am start attempt 2/3 "quoted" back\slash	tabbed'; then
  pass "logcat with special characters round-trips through the JSON intact"
else
  fail "logcat content was corrupted in the JSON body: $BODY"
fi

# ── (l) no GITHUB_RUN_ID -> shorter comment body, still valid JSON ────────────
echo ""
echo "=== (l) no GITHUB_RUN_ID -> shorter comment body, still valid JSON ==="
run_notify "$TMP/h.txt" open ""
if jq -e . "$POST_BODY_FILE" > /dev/null 2>&1; then
  pass "comment JSON is well-formed without a run id"
else
  fail "comment JSON is malformed without a run id: $(cat "$POST_BODY_FILE")"
fi
BODY="$(jq -r '.body' "$POST_BODY_FILE" 2>/dev/null)"
if ! printf '%s' "$BODY" | grep -q 'run'; then
  pass "comment body omits the run link when no run id is set"
else
  fail "comment body unexpectedly mentions a run when no run id is set: $BODY"
fi

# ── (m) GITHUB_ACTIONS unset -> no curl is invoked at all ─────────────────────
echo ""
echo "=== (m) GITHUB_ACTIONS unset -> no curl is invoked ==="
: > "$CURL_LOG"
rm -f "$POST_BODY_FILE"
# Run with the fake curl on PATH but without GITHUB_ACTIONS: the gated block
# must not execute, so the fake curl must never be touched.
PATH="$FAKE_BIN:$PATH" CURL_LOG="$CURL_LOG" POST_BODY_FILE="$POST_BODY_FILE" \
  bash "$DETECT" "$TMP/h.txt" > /dev/null 2>&1
RC=$?
if [[ "$RC" -eq 10 ]]; then pass "detection still exits 10 with GITHUB_ACTIONS unset"; else fail "expected exit 10, got $RC"; fi
if [[ -s "$CURL_LOG" ]]; then
  fail "curl was invoked even though GITHUB_ACTIONS was unset: $(cat "$CURL_LOG")"
else
  pass "no curl invoked when GITHUB_ACTIONS is unset"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed."
if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
