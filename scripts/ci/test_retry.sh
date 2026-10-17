#!/usr/bin/env bash
# test_retry.sh: Shell-based tests for retry.sh.
#
# The command under retry is a fake that counts its runs in a file and fails
# until a chosen run, so each test controls exactly when it succeeds. `sleep`
# is replaced on PATH by a fake that records its argument and returns at once.
#
# Covers:
#   (a) Success on the first attempt -> exit 0, one run, no sleep
#   (b) Success on the last attempt -> exit 0, every attempt run, a sleep of
#       the given delay between each pair, a ::warning per retried failure
#   (c) Failure on every attempt -> the last attempt's exit status, no sleep
#       after the last attempt, one ::error
#   (d) Arguments reach the command unchanged, including spaces and `;`
#   (e) One attempt -> a failure is not retried
#   (f) Usage errors, leading zeros among them -> exit 2 and the command
#       never runs
#
# Always exits 0 on success, non-zero on failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RETRY="$SCRIPT_DIR/retry.sh"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
expect_eq() {
  if [[ "$2" == "$3" ]]; then pass "$1"; else fail "$1: expected '$2', got '$3'"; fi
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

BIN="$TMP/bin"
mkdir -p "$BIN"

# fake_cmd: fails with FAKE_STATUS until run number FAKE_SUCCEED_ON (never, if
# 0), then exits 0. Each run appends its arguments, one per line, to
# $TMP/args and bumps the count in $TMP/runs.
cat > "$BIN/fake_cmd" <<'EOF'
#!/usr/bin/env bash
runs=$(( $(cat "$FAKE_DIR/runs" 2>/dev/null || echo 0) + 1 ))
echo "$runs" > "$FAKE_DIR/runs"
printf '%s\n' "$@" >> "$FAKE_DIR/args"
if [[ "$FAKE_SUCCEED_ON" -ne 0 && "$runs" -ge "$FAKE_SUCCEED_ON" ]]; then exit 0; fi
exit "$FAKE_STATUS"
EOF
cat > "$BIN/sleep" <<'EOF'
#!/usr/bin/env bash
echo "$1" >> "$FAKE_DIR/sleeps"
EOF
chmod +x "$BIN/fake_cmd" "$BIN/sleep"
export PATH="$BIN:$PATH" FAKE_DIR="$TMP"

# run_retry SUCCEED_ON STATUS RETRY-ARGS...: resets the fakes, runs retry.sh,
# and sets RC, OUT (stderr), RUNS and SLEEPS (space-separated delays).
run_retry() {
  rm -f "$TMP/runs" "$TMP/args"
  : > "$TMP/sleeps"
  export FAKE_SUCCEED_ON="$1" FAKE_STATUS="$2"
  shift 2
  RC=0
  OUT="$(bash "$RETRY" "$@" 2>&1 >/dev/null)" || RC=$?
  RUNS="$(cat "$TMP/runs" 2>/dev/null || echo 0)"
  SLEEPS="$(tr '\n' ' ' < "$TMP/sleeps" | sed 's/ $//')"
}

count() { grep -c -- "$1" <<<"$OUT" || true; }

# ── (a) Success on the first attempt ─────────────────────────────────────────
echo ""
echo "=== (a) success on the first attempt ==="
run_retry 1 1 3 30 fake_cmd
expect_eq "exit status" 0 "$RC"
expect_eq "runs" 1 "$RUNS"
expect_eq "sleeps" "" "$SLEEPS"
expect_eq "no warning or error" "" "$OUT"

# ── (b) Success on the last attempt ──────────────────────────────────────────
echo ""
echo "=== (b) success on the last attempt ==="
run_retry 3 1 3 30 fake_cmd
expect_eq "exit status" 0 "$RC"
expect_eq "runs" 3 "$RUNS"
expect_eq "sleeps" "30 30" "$SLEEPS"
expect_eq "warnings" 2 "$(count '^::warning::fake_cmd failed with exit 1 on attempt [12] of 3; retrying in 30s$')"
expect_eq "errors" 0 "$(count '::error::')"

# ── (c) Failure on every attempt ─────────────────────────────────────────────
echo ""
echo "=== (c) failure on every attempt ==="
run_retry 0 7 3 5 fake_cmd
expect_eq "exit status is the last attempt's" 7 "$RC"
expect_eq "runs" 3 "$RUNS"
expect_eq "no sleep after the last attempt" "5 5" "$SLEEPS"
expect_eq "warnings" 2 "$(count '^::warning::')"
expect_eq "error" 1 "$(count '^::error::fake_cmd failed with exit 7 on attempt 3 of 3; giving up$')"

# ── (d) Arguments reach the command unchanged ────────────────────────────────
echo ""
echo "=== (d) arguments reach the command unchanged ==="
run_retry 2 1 2 0 fake_cmd --install "emulator" "system-images;android-35;google_apis;x86_64" "a b"
expected="$(printf '%s\n' --install emulator "system-images;android-35;google_apis;x86_64" "a b")"
expect_eq "arguments on each attempt" "$expected"$'\n'"$expected" "$(cat "$TMP/args")"
expect_eq "zero delay is passed to sleep" "0" "$SLEEPS"

# ── (e) One attempt ──────────────────────────────────────────────────────────
echo ""
echo "=== (e) one attempt ==="
run_retry 2 4 1 30 fake_cmd
expect_eq "exit status" 4 "$RC"
expect_eq "runs" 1 "$RUNS"
expect_eq "sleeps" "" "$SLEEPS"

# ── (f) Usage errors ─────────────────────────────────────────────────────────
echo ""
echo "=== (f) usage errors ==="
for args in "3 30" "0 30 fake_cmd" "x 30 fake_cmd" "3 -1 fake_cmd" "3 1.5 fake_cmd" \
  "010 30 fake_cmd" "08 30 fake_cmd" "3 010 fake_cmd"; do
  # shellcheck disable=SC2086 # word splitting is the point
  run_retry 1 1 $args
  expect_eq "'$args' exits 2" 2 "$RC"
  expect_eq "'$args' runs nothing" 0 "$RUNS"
done

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
