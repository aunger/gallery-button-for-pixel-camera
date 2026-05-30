#!/usr/bin/env python3
"""test_ci_monitor.py — Tests for ci_monitor.py.

Calls the parser functions directly (no subprocess shims). Plus a mocked-HTTP
smoke test that exercises the request helper without touching the network.

Covers:
  (a) Signal 1 step parser: all-success build-and-test emits exactly the 3 test-step lines
  (b) Signal 1 step parser: a genuine failure step is emitted
  (c) Signal 1 step parser: successful setup steps and skipped conditional steps are suppressed
  (d) Signal 1 step parser: deduplication across two iterations (same step not re-emitted)
  (e) Signal 2 artifact parser: FAIL with multi-line trace is emitted with indented trace
  (f) Signal 2 artifact parser: all-PASS artifact emits nothing
  (g) Signal 2 artifact parser: deduplication by suite#name across two calls
  (h) Mocked HTTP: _request returns parsed JSON without touching the network
  (i) main(): failing unit test signals (step failure + FAIL) emit once before terminal
  (j) main(): in_progress heartbeat fires after >120s and resets on emission
  (k) Gap A: parse_pr_terminal() maps merged/closed/open + main() terminates on merged
  (l) Gap C: fetch_pr_with_retry() retry/backoff and _retry_after_seconds() header parsing
  (m) main(): staggered step-then-FAIL emitted once each before terminal, heartbeat suppressed
  (n) main(): quiet passing PR emits two adjacent heartbeats then a single Clear terminal

No network calls required; no GITHUB_TOKEN needed.
Always exits 0 on success, non-zero on failure.
"""

import collections
import io
import json
import os
import sys
import unittest.mock
import urllib.error
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ci_monitor  # noqa: E402

PASS = 0
FAIL = 0


def _pass(msg):
    global PASS
    print("  PASS: %s" % msg)
    PASS += 1


def _fail(msg):
    global FAIL
    print("  FAIL: %s" % msg)
    FAIL += 1


def check(cond, ok_msg, bad_msg):
    if cond:
        _pass(ok_msg)
    else:
        _fail(bad_msg)


def make_zip_ndjson(lines):
    """Return the bytes of a zip archive containing one testresults.ndjson entry."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("testresults.ndjson", "\n".join(lines))
    return buf.getvalue()


# ── Fixtures ────────────────────────────────────────────────────────────────────

ALL_SUCCESS_JOBS = {
    "jobs": [
        {
            "name": "build-and-test",
            "steps": [
                {"number": 1, "name": "Set up job", "status": "completed", "conclusion": "success"},
                {"number": 2, "name": "Checkout", "status": "completed", "conclusion": "success"},
                {"number": 3, "name": "Set up JDK", "status": "completed", "conclusion": "success"},
                {"number": 4, "name": "Build and run unit tests", "status": "completed", "conclusion": "success"},
                {"number": 5, "name": "Run PixelCameraOverlayE2ETest", "status": "completed", "conclusion": "success"},
                {"number": 6, "name": "Run GalleryButtonVisualE2ETest", "status": "completed", "conclusion": "success"},
                {"number": 7, "name": "Upload test results on failure", "status": "completed", "conclusion": "skipped"},
                {"number": 8, "name": "Complete job", "status": "completed", "conclusion": "success"},
            ],
        }
    ]
}

FAILURE_JOBS = {
    "jobs": [
        {
            "name": "build-and-test",
            "steps": [
                {"number": 1, "name": "Set up job", "status": "completed", "conclusion": "success"},
                {"number": 2, "name": "Download AVD", "status": "completed", "conclusion": "failure"},
            ],
        }
    ]
}

FAIL_NDJSON = [
    'some prefix ##GB4PC_TEST## {"suite":"com.gb4pc.e2e.GalleryButtonVisualE2ETest","name":"test1a","outcome":"FAIL","ms":0,"msg":"java.lang.AssertionError: expected button visible","trace":"java.lang.AssertionError: expected button visible\\n\\tat org.junit.Assert.fail(Assert.java:89)\\n\\tat com.gb4pc.e2e.GalleryButtonVisualE2ETest.test1a(GalleryButtonVisualE2ETest.kt:42)"}',
    '##GB4PC_TEST## {"suite":"com.gb4pc.e2e.GalleryButtonVisualE2ETest","name":"test1b","outcome":"PASS","ms":120,"msg":"","trace":""}',
    '##GB4PC_TEST## {"suite":"com.gb4pc.e2e.PixelCameraOverlayE2ETest","name":"test2a","outcome":"PASS","ms":200,"msg":"","trace":""}',
    '##GB4PC_TEST## {"suite":"com.gb4pc.e2e.PixelCameraOverlayE2ETest","name":"test2b","outcome":"PASS","ms":150,"msg":"","trace":""}',
]

PASS_ONLY_NDJSON = [
    '##GB4PC_TEST## {"suite":"com.gb4pc.e2e.GalleryButtonVisualE2ETest","name":"test3a","outcome":"PASS","ms":100,"msg":"","trace":""}',
    '##GB4PC_TEST## {"suite":"com.gb4pc.e2e.GalleryButtonVisualE2ETest","name":"test3b","outcome":"PASS","ms":110,"msg":"","trace":""}',
]


# ── (a) All-success: exactly 3 test-step lines emitted ─────────────────────────
print("\n=== (a) Signal 1: all-success build-and-test emits exactly 3 test-step lines ===")
out_a = ci_monitor.parse_steps(ALL_SUCCESS_JOBS, set())
step_lines_a = [ln for ln in out_a if ln.startswith("step ")]
check(len(step_lines_a) == 3, "emits exactly 3 step lines (got %d)" % len(step_lines_a),
      "expected 3 step lines, got %d; output: %r" % (len(step_lines_a), out_a))
check(any('step "Build and run unit tests" -> success' == ln for ln in out_a),
      "unit tests step line present", "unit tests step line missing; output: %r" % out_a)
check(any('step "Run PixelCameraOverlayE2ETest" -> success' == ln for ln in out_a),
      "PixelCameraOverlayE2ETest step line present",
      "PixelCameraOverlayE2ETest step line missing; output: %r" % out_a)
check(any('step "Run GalleryButtonVisualE2ETest" -> success' == ln for ln in out_a),
      "GalleryButtonVisualE2ETest step line present",
      "GalleryButtonVisualE2ETest step line missing; output: %r" % out_a)

# ── (b) Genuine failure step is emitted ────────────────────────────────────────
print("\n=== (b) Signal 1: a genuine failure step is emitted ===")
out_b = ci_monitor.parse_steps(FAILURE_JOBS, set())
check(any('step "Download AVD" -> failure' == ln for ln in out_b),
      "failed step 'Download AVD' is emitted", "failed step not emitted; output: %r" % out_b)
check(not any("Set up job" in ln for ln in out_b),
      "successful setup step 'Set up job' correctly suppressed",
      "successful setup step 'Set up job' should NOT be emitted; output: %r" % out_b)

# ── (c) Setup and skipped conditional steps are suppressed ─────────────────────
print("\n=== (c) Signal 1: successful setup steps and skipped conditional steps are suppressed ===")
out_c = ci_monitor.parse_steps(ALL_SUCCESS_JOBS, set())
check(not any('"Set up job"' in ln for ln in out_c), "'Set up job' suppressed",
      "'Set up job' should be suppressed; output: %r" % out_c)
check(not any('"Checkout"' in ln for ln in out_c), "'Checkout' suppressed",
      "'Checkout' should be suppressed; output: %r" % out_c)
check(not any('"Set up JDK"' in ln for ln in out_c), "'Set up JDK' suppressed",
      "'Set up JDK' should be suppressed; output: %r" % out_c)
check(not any('"Upload test results on failure"' in ln for ln in out_c),
      "'Upload test results on failure' (skipped) suppressed",
      "'Upload test results on failure' (skipped) should be suppressed; output: %r" % out_c)
check(not any('"Complete job"' in ln for ln in out_c), "'Complete job' suppressed",
      "'Complete job' should be suppressed; output: %r" % out_c)

# ── (d) Deduplication across two iterations ─────────────────────────────────────
print("\n=== (d) Signal 1: deduplication — same steps not re-emitted on second iteration ===")
seen_d = set()
out_d1 = ci_monitor.parse_steps(ALL_SUCCESS_JOBS, seen_d)
out_d2 = ci_monitor.parse_steps(ALL_SUCCESS_JOBS, seen_d)
check(out_d2 == [], "second iteration emits nothing (all steps already seen)",
      "second iteration re-emitted steps: %r" % out_d2)
step_lines_d1 = [ln for ln in out_d1 if ln.startswith("step ")]
check(len(step_lines_d1) == 3, "first iteration still emitted 3 step lines before dedup kicks in",
      "first iteration emitted %d step lines (expected 3)" % len(step_lines_d1))

# ── (e) FAIL with multi-line trace emitted with indented trace ──────────────────
print("\n=== (e) Signal 2: FAIL with multi-line trace is emitted with indented trace ===")
out_e = ci_monitor.parse_fails(FAIL_NDJSON, set())
joined_e = "\n".join(out_e)
check("FAIL [com.gb4pc.e2e.GalleryButtonVisualE2ETest] test1a:" in joined_e,
      "FAIL line for test1a emitted", "FAIL line for test1a not found; output: %r" % out_e)
check("java.lang.AssertionError: expected button visible" in joined_e,
      "failure message present in output", "failure message missing; output: %r" % out_e)
check(any(ln.startswith("  ") for ln in joined_e.split("\n")),
      "trace lines are indented", "trace lines are not indented; output: %r" % out_e)

# ── (f) All-PASS artifact emits nothing ────────────────────────────────────────
print("\n=== (f) Signal 2: all-PASS artifact emits nothing ===")
out_f = ci_monitor.parse_fails(PASS_ONLY_NDJSON, set())
check(out_f == [], "all-PASS artifact produces no output",
      "all-PASS artifact unexpectedly produced output: %r" % out_f)

# ── (g) Deduplication by suite#name across two calls ───────────────────────────
print("\n=== (g) Signal 2: deduplication by suite#name across two calls ===")
seen_g = set()
out_g1 = ci_monitor.parse_fails(FAIL_NDJSON, seen_g)
check(any("FAIL [com.gb4pc.e2e.GalleryButtonVisualE2ETest] test1a:" in ln for ln in out_g1),
      "first call emits FAIL", "first call did not emit FAIL; output: %r" % out_g1)
out_g2 = ci_monitor.parse_fails(FAIL_NDJSON, seen_g)
check(out_g2 == [], "second call produces no output (FAIL already seen)",
      "second call re-emitted already-seen FAIL: %r" % out_g2)

# ── (h) Mocked HTTP: _request parses JSON without touching the network ─────────
print("\n=== (h) Mocked HTTP: _request returns parsed JSON, no network ===")


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


with unittest.mock.patch.object(
    ci_monitor.urllib.request, "urlopen",
    return_value=_FakeResp(json.dumps({"head": {"sha": "deadbeef"}}).encode()),
):
    got = ci_monitor._request("https://example.invalid/pulls/1", "tok")
check(ci_monitor.parse_pr_sha(got) == "deadbeef",
      "mocked /pulls response parses head.sha",
      "mocked /pulls response did not parse; got: %r" % got)

# ── (i) main(): failing unit test signals emitted once, before terminal ────────
print("\n=== (i) main(): step failure + FAIL emitted exactly once, before terminal Blocked ===")

PR_JSON = {"head": {"sha": "cafef00d"}}
CHECK_INPROGRESS = {
    "total_count": 1,
    "check_runs": [{"status": "in_progress", "conclusion": None}],
}
CHECK_BLOCKED = {
    "total_count": 1,
    "check_runs": [{"status": "completed", "conclusion": "failure"}],
}
JOBS_FAIL = {
    "jobs": [
        {
            "name": "build-and-test",
            "steps": [
                {"number": 1, "name": "Set up job", "status": "completed", "conclusion": "success"},
                {"number": 4, "name": "Build and run unit tests", "status": "completed", "conclusion": "failure"},
            ],
        }
    ]
}
ARTS_JSON = {
    "artifacts": [
        {"id": 9001, "name": "testresults-unit", "expired": False}
    ]
}
ZIP_BYTES = make_zip_ndjson([
    '##GB4PC_TEST## {"suite":"com.gb4pc.unit.GalleryButtonTest","name":"testClick","outcome":"FAIL","ms":3,"msg":"java.lang.AssertionError: boom","trace":"java.lang.AssertionError: boom\\n\\tat com.gb4pc.unit.GalleryButtonTest.testClick(GalleryButtonTest.kt:21)"}',
])

# Per iteration the request order is: pulls (sha), check-runs, runs, jobs,
# artifacts, [zip per new artifact]. Iteration 1 (in_progress) downloads the
# zip; iteration 2 (Blocked, terminal) finds the artifact already seen and
# skips the zip call. 6 + 5 = 11 entries; the deque must be exactly drained.
side_effects_i = collections.deque([
    # iteration 1
    PR_JSON,            # pulls -> sha
    CHECK_INPROGRESS,   # check-runs -> in_progress
    {"workflow_runs": [{"id": 555, "status": "in_progress"}]},  # runs -> run_id
    JOBS_FAIL,          # jobs -> step failure
    ARTS_JSON,          # artifacts -> one new artifact
    ZIP_BYTES,          # zip (raw) -> FAIL line
    # iteration 2
    PR_JSON,            # pulls -> sha
    CHECK_BLOCKED,      # check-runs -> Blocked (terminal)
    {"workflow_runs": [{"id": 555, "status": "in_progress"}]},  # runs -> run_id
    JOBS_FAIL,          # jobs -> step already seen, nothing new
    ARTS_JSON,          # artifacts -> artifact already seen, no zip call
])


def fake_request_i(url, token, raw=False):
    return side_effects_i.popleft()


buf_i = io.StringIO()
with unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_i), \
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=1000.0), \
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None), \
        unittest.mock.patch("sys.stdout", new=buf_i):
    rc_i = ci_monitor.main(["ci_monitor.py", "--pr", "285"])

out_i = buf_i.getvalue()
lines_i = out_i.splitlines()
step_line_i = 'PR#285: step "Build and run unit tests" -> failure'
fail_line_i = "PR#285: FAIL [com.gb4pc.unit.GalleryButtonTest] testClick: java.lang.AssertionError: boom"
blocked_line_i = "PR#285: Blocked"

check(lines_i.count(step_line_i) == 1,
      "step failure line emitted exactly once",
      "step failure line count != 1; output: %r" % out_i)
check(lines_i.count(fail_line_i) == 1,
      "FAIL line emitted exactly once",
      "FAIL line count != 1; output: %r" % out_i)
check(lines_i.count(blocked_line_i) == 1,
      "Blocked terminal line emitted exactly once",
      "Blocked terminal line count != 1; output: %r" % out_i)
check(step_line_i in lines_i and blocked_line_i in lines_i
      and lines_i.index(step_line_i) < lines_i.index(blocked_line_i),
      "step failure line precedes terminal Blocked",
      "step failure line not before Blocked; output: %r" % out_i)
check(fail_line_i in lines_i and blocked_line_i in lines_i
      and lines_i.index(fail_line_i) < lines_i.index(blocked_line_i),
      "FAIL line precedes terminal Blocked",
      "FAIL line not before Blocked; output: %r" % out_i)
check(len(side_effects_i) == 0,
      "all 11 mocked requests consumed (zip skipped in iteration 2)",
      "request deque not drained; %d entries left" % len(side_effects_i))
check(rc_i == 0, "main() returned 0", "main() returned %r" % rc_i)


# ── (j) main(): in_progress heartbeat fires after >120s, resets on emission ────
print("\n=== (j) main(): in_progress heartbeat suppressed until >120s, resets on emission ===")

PR_J = {"head": {"sha": "abad1dea"}}
CHECK_IP = {
    "total_count": 1,
    "check_runs": [{"status": "in_progress", "conclusion": None}],
}
CHECK_BL = {
    "total_count": 1,
    "check_runs": [{"status": "completed", "conclusion": "failure"}],
}
RUNS_J = {"workflow_runs": [{"id": 777, "status": "in_progress"}]}
JOBS_EMPTY = {"jobs": [{"name": "build-and-test", "steps": []}]}
JOBS_STEP7 = {
    "jobs": [
        {
            "name": "build-and-test",
            "steps": [
                {"number": 7, "name": "Build and run unit tests", "status": "completed", "conclusion": "success"},
            ],
        }
    ]
}
ARTS_EMPTY = {"artifacts": []}

# Each iteration issues exactly 5 requests (pulls, check-runs, runs, jobs,
# artifacts); no zip is ever downloaded (artifacts empty). 13 iterations -> 65,
# plus the single pre-loop clock startup read makes the time deque 66 entries.
# The 13 iterations supply check-runs in_progress for 1..12 and Blocked at 13.
jobs_for_iter = [JOBS_STEP7 if n == 7 else JOBS_EMPTY for n in range(1, 14)]
checks_for_iter = [CHECK_BL if n == 13 else CHECK_IP for n in range(1, 14)]

req_j = collections.deque()
for n in range(13):
    req_j.append(PR_J)                 # pulls -> sha
    req_j.append(checks_for_iter[n])   # check-runs
    req_j.append(RUNS_J)               # runs -> run_id
    req_j.append(jobs_for_iter[n])     # jobs
    req_j.append(ARTS_EMPTY)           # artifacts (no zip)


def fake_request_j(url, token, raw=False):
    return req_j.popleft()


# Clock schedule. time.time() is read once before the loop (t=0), then once per
# iteration in the in_progress branch. time.sleep(30) advances the clock by 30s.
# Heartbeat fires only when now - last_output_ts > 120. The deque-as-list of 66
# clock reads is indexed by a counter; sleep bumps the wall clock, and each
# time.time() read returns the current wall clock value.
clock = {"t": 0.0}


def fake_time_j():
    return clock["t"]


def fake_sleep_j(secs):
    clock["t"] += secs


buf_j = io.StringIO()
with unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_j), \
        unittest.mock.patch.object(ci_monitor.time, "time", side_effect=fake_time_j), \
        unittest.mock.patch.object(ci_monitor.time, "sleep", side_effect=fake_sleep_j), \
        unittest.mock.patch("sys.stdout", new=buf_j):
    rc_j = ci_monitor.main(["ci_monitor.py", "--pr", "285"])

out_j = buf_j.getvalue()
lines_j = out_j.splitlines()
ip_line = "PR#285: in_progress"
step_line_j = 'PR#285: step "Build and run unit tests" -> success'
blocked_line_j = "PR#285: Blocked"

check(lines_j.count(ip_line) == 2,
      "in_progress heartbeat emitted exactly twice (got %d)" % lines_j.count(ip_line),
      "in_progress count != 2; output: %r" % out_j)
check(lines_j.count(step_line_j) == 1,
      "step line emitted exactly once",
      "step line count != 1; output: %r" % out_j)
check(lines_j.count(blocked_line_j) == 1,
      "Blocked terminal line emitted exactly once",
      "Blocked terminal line count != 1; output: %r" % out_j)

ip_idx = [k for k, ln in enumerate(lines_j) if ln == ip_line]
step_idx = lines_j.index(step_line_j) if step_line_j in lines_j else -1
bl_idx = lines_j.index(blocked_line_j) if blocked_line_j in lines_j else -1
check(len(ip_idx) == 2 and step_idx != -1 and bl_idx != -1
      and ip_idx[0] < step_idx < ip_idx[1] < bl_idx,
      "ordering: first in_progress, step, second in_progress, Blocked",
      "ordering wrong; lines: %r" % lines_j)
check(len(req_j) == 0,
      "all mocked requests consumed (65 entries drained)",
      "request deque not drained; %d entries left" % len(req_j))
check(rc_j == 0, "main() returned 0", "main() returned %r" % rc_j)


# ── (k) Gap A: closed/merged PR termination ────────────────────────────────────
print("\n=== (k) Gap A: parse_pr_terminal maps merged/closed/open ===")

check(ci_monitor.parse_pr_terminal({"merged": True, "state": "closed"}) == "Merged",
      "parse_pr_terminal returns 'Merged' when merged is true",
      "expected 'Merged'; got %r" % ci_monitor.parse_pr_terminal({"merged": True, "state": "closed"}))
check(ci_monitor.parse_pr_terminal({"merged": False, "state": "closed"}) == "Closed",
      "parse_pr_terminal returns 'Closed' when state is closed (not merged)",
      "expected 'Closed'; got %r" % ci_monitor.parse_pr_terminal({"merged": False, "state": "closed"}))
check(ci_monitor.parse_pr_terminal({"merged": False, "state": "open"}) == "",
      "parse_pr_terminal returns '' when PR is open",
      "expected ''; got %r" % ci_monitor.parse_pr_terminal({"merged": False, "state": "open"}))

# Integration: iteration 1 is in_progress, iteration 2 the PR is merged. The
# terminal check runs right after the SHA fetch (before check-runs), so on
# iteration 2 main() emits 'PR#N: Merged' and breaks without issuing the
# check-runs/runs/jobs/artifacts calls. Per-iteration request order is:
#   pulls (sha), check-runs, runs, jobs, artifacts, [zip per new artifact].
# Iteration 1 (open + in_progress, empty artifacts) issues 5 requests; iteration
# 2 short-circuits after the single pulls fetch. 5 + 1 = 6 entries, drained.
print("\n=== (k) main(): merged PR emits terminal 'Merged' and exits cleanly ===")

PR_OPEN_K = {"head": {"sha": "feedface"}, "merged": False, "state": "open"}
PR_MERGED_K = {"head": {"sha": "feedface"}, "merged": True, "state": "closed"}
CHECK_IP_K = {"total_count": 1, "check_runs": [{"status": "in_progress", "conclusion": None}]}
RUNS_K = {"workflow_runs": [{"id": 888, "status": "in_progress"}]}
JOBS_EMPTY_K = {"jobs": [{"name": "build-and-test", "steps": []}]}
ARTS_EMPTY_K = {"artifacts": []}

side_effects_k = collections.deque([
    # iteration 1 — open, in_progress (no heartbeat: clock frozen at start)
    PR_OPEN_K,        # pulls -> sha, terminal == ''
    CHECK_IP_K,       # check-runs -> in_progress
    RUNS_K,           # runs -> run_id
    JOBS_EMPTY_K,     # jobs -> nothing
    ARTS_EMPTY_K,     # artifacts -> nothing
    # iteration 2 — merged: terminal short-circuit before check-runs
    PR_MERGED_K,      # pulls -> sha, terminal == 'Merged' -> break
])


def fake_request_k(url, token, raw=False):
    return side_effects_k.popleft()


buf_k = io.StringIO()
with unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_k), \
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=1000.0), \
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None), \
        unittest.mock.patch("sys.stdout", new=buf_k):
    rc_k = ci_monitor.main(["ci_monitor.py", "--pr", "290"])

out_k = buf_k.getvalue()
lines_k = out_k.splitlines()
merged_line_k = "PR#290: Merged"
check(lines_k.count(merged_line_k) == 1,
      "Merged terminal line emitted exactly once",
      "Merged terminal line count != 1; output: %r" % out_k)
check(not any("still computing" in ln for ln in lines_k),
      "no 'still computing' spin while terminating on merged",
      "unexpected 'still computing' line; output: %r" % out_k)
check(len(side_effects_k) == 0,
      "all 6 mocked requests consumed (merged short-circuits iteration 2)",
      "request deque not drained; %d entries left" % len(side_effects_k))
check(rc_k == 0, "main() returned 0 after merged terminal", "main() returned %r" % rc_k)


# ── (l) Gap C: SHA-fetch retry/backoff ─────────────────────────────────────────
print("\n=== (l) Gap C: _retry_after_seconds parses rate-limit headers ===")


class _FakeHTTPError(urllib.error.HTTPError):
    """An HTTPError with controllable code and headers, no real response body."""

    def __init__(self, code, headers):
        # Bypass HTTPError.__init__'s fp/url plumbing; set just what we read.
        # _retry_after_seconds only inspects .code and .headers.
        self.code = code
        self.headers = headers


# Retry-After header (delta seconds).
ra = ci_monitor._retry_after_seconds(_FakeHTTPError(429, {"Retry-After": "42"}), 1000.0)
check(ra == 42, "Retry-After header parsed as 42s",
      "expected 42; got %r" % ra)

# X-RateLimit-Reset header (Unix timestamp -> delta from now).
xr = ci_monitor._retry_after_seconds(
    _FakeHTTPError(403, {"X-RateLimit-Reset": "1100"}), 1000.0)
check(xr == 100, "X-RateLimit-Reset parsed as (reset - now) = 100s",
      "expected 100; got %r" % xr)

# Clamp to 300s ceiling (huge reset far in the future).
clamp = ci_monitor._retry_after_seconds(
    _FakeHTTPError(403, {"X-RateLimit-Reset": "100000"}), 1000.0)
check(clamp == 300, "huge backoff clamped to 300s max",
      "expected 300; got %r" % clamp)

# Non-rate-limit status returns None (no backoff hint).
none_status = ci_monitor._retry_after_seconds(
    _FakeHTTPError(500, {"Retry-After": "10"}), 1000.0)
check(none_status is None, "non-403/429 status yields no hint (None)",
      "expected None; got %r" % none_status)

# Rate-limit status but no usable header returns None.
none_hdr = ci_monitor._retry_after_seconds(_FakeHTTPError(429, {}), 1000.0)
check(none_hdr is None, "rate-limit status without headers yields None",
      "expected None; got %r" % none_hdr)

# Invalid Retry-After value falls through to None (no X-RateLimit-Reset).
bad_ra = ci_monitor._retry_after_seconds(
    _FakeHTTPError(429, {"Retry-After": "soon"}), 1000.0)
check(bad_ra is None, "non-numeric Retry-After yields None",
      "expected None; got %r" % bad_ra)

print("\n=== (l) fetch_pr_with_retry: first-try success, retry-then-success, all-fail ===")

PR_RETRY = {"head": {"sha": "0ddba11"}}

# Success on the first try: one _request call, no sleep.
calls_first = {"n": 0}


def req_first(url, token, raw=False):
    calls_first["n"] += 1
    return PR_RETRY


with unittest.mock.patch.object(ci_monitor, "_request", side_effect=req_first), \
        unittest.mock.patch.object(ci_monitor.time, "sleep", side_effect=AssertionError("should not sleep")), \
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=1000.0):
    got_first = ci_monitor.fetch_pr_with_retry("290", "tok")
check(got_first == PR_RETRY and calls_first["n"] == 1,
      "fetch_pr_with_retry succeeds on first try with no sleep",
      "first-try fetch wrong; got %r after %d calls" % (got_first, calls_first["n"]))

# Transient URLError twice, then success. Backoff sleeps recorded.
err = urllib.error.URLError("temporary blip")
retry_results = collections.deque([None, None, PR_RETRY])
slept = []


def req_retry(url, token, raw=False):
    r = retry_results.popleft()
    # Emulate _request recording the last transient error on failure.
    ci_monitor._request.last_error = None if r is not None else err
    return r


with unittest.mock.patch.object(ci_monitor, "_request", side_effect=req_retry), \
        unittest.mock.patch.object(ci_monitor.time, "sleep", side_effect=slept.append), \
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=1000.0):
    got_retry = ci_monitor.fetch_pr_with_retry("290", "tok", attempts=3, base_delay=2)
check(got_retry == PR_RETRY,
      "fetch_pr_with_retry retries transient failures and eventually succeeds",
      "retry-then-success wrong; got %r" % got_retry)
check(slept == [2, 4],
      "exponential backoff sleeps were 2s then 4s before success",
      "backoff schedule wrong; slept %r" % slept)
check(len(retry_results) == 0, "all retry responses consumed",
      "retry deque not drained; %d left" % len(retry_results))

# Every attempt fails -> returns None after `attempts` tries, sleeps attempts-1 times.
fail_calls = {"n": 0}
slept_fail = []


def req_allfail(url, token, raw=False):
    fail_calls["n"] += 1
    ci_monitor._request.last_error = err
    return None


with unittest.mock.patch.object(ci_monitor, "_request", side_effect=req_allfail), \
        unittest.mock.patch.object(ci_monitor.time, "sleep", side_effect=slept_fail.append), \
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=1000.0):
    got_fail = ci_monitor.fetch_pr_with_retry("290", "tok", attempts=3, base_delay=2)
check(got_fail is None,
      "fetch_pr_with_retry returns None when every attempt fails",
      "expected None; got %r" % got_fail)
check(fail_calls["n"] == 3,
      "fetch_pr_with_retry made exactly `attempts` (3) requests",
      "expected 3 requests; got %d" % fail_calls["n"])
check(slept_fail == [2, 4],
      "slept between the 3 failed attempts (2s, 4s), not after the last",
      "fail backoff schedule wrong; slept %r" % slept_fail)

# Rate-limit hint overrides exponential backoff: 403 with Retry-After=7.
rl_err = _FakeHTTPError(429, {"Retry-After": "7"})
rl_results = collections.deque([None, PR_RETRY])
slept_rl = []


def req_ratelimit(url, token, raw=False):
    r = rl_results.popleft()
    ci_monitor._request.last_error = None if r is not None else rl_err
    return r


with unittest.mock.patch.object(ci_monitor, "_request", side_effect=req_ratelimit), \
        unittest.mock.patch.object(ci_monitor.time, "sleep", side_effect=slept_rl.append), \
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=1000.0):
    got_rl = ci_monitor.fetch_pr_with_retry("290", "tok", attempts=3, base_delay=2)
check(got_rl == PR_RETRY and slept_rl == [7],
      "rate-limit Retry-After hint (7s) honored over exponential backoff",
      "rate-limit backoff wrong; got %r, slept %r" % (got_rl, slept_rl))


# ── (m) main(): staggered step then FAIL, each once, before terminal Blocked ───
print("\n=== (m) main(): step (poll 1) then FAIL (poll 2) emitted once each, before terminal; no heartbeat ===")

# A step delta and a per-test FAIL arrive on separate polls; each signal resets
# the silence timer, so with a 30s-per-poll advancing clock the 120s heartbeat
# threshold is never crossed and no in_progress line is emitted. Per-poll request
# order: pulls (sha), check-runs, runs, jobs, artifacts, [zip per new artifact].
PR_M = {"head": {"sha": "5ca1ab1e"}}
CHECK_IP_M = {"total_count": 1, "check_runs": [{"status": "in_progress", "conclusion": None}]}
CHECK_BL_M = {"total_count": 1, "check_runs": [{"status": "completed", "conclusion": "failure"}]}
RUNS_M = {"workflow_runs": [{"id": 606, "status": "in_progress"}]}
JOBS_UNIT_FAIL_M = {
    "jobs": [
        {
            "name": "build-and-test",
            "steps": [
                {"number": 1, "name": "Set up job", "status": "completed", "conclusion": "success"},
                {"number": 4, "name": "Build and run unit tests", "status": "completed", "conclusion": "failure"},
            ],
        }
    ]
}
ARTS_EMPTY_M = {"artifacts": []}
ARTS_UNIT_M = {"artifacts": [{"id": 7007, "name": "testresults-unit", "expired": False}]}
ZIP_UNIT_M = make_zip_ndjson([
    '##GB4PC_TEST## {"suite":"com.gb4pc.unit.GalleryButtonTest","name":"testIcon","outcome":"FAIL","ms":4,"msg":"java.lang.AssertionError: kaboom","trace":"java.lang.AssertionError: kaboom\\n\\tat com.gb4pc.unit.GalleryButtonTest.testIcon(GalleryButtonTest.kt:33)"}',
])

# Poll 1 (5): step delta only (artifacts empty). Poll 2 (6): step already seen,
# artifact appears -> zip downloaded -> FAIL emitted. Poll 3 terminal (5):
# Blocked; artifact already seen so no zip call. 5 + 6 + 5 = 16, drained.
side_effects_m = collections.deque([
    # poll 1 — step delta only
    PR_M,               # pulls -> sha
    CHECK_IP_M,         # check-runs -> in_progress
    RUNS_M,             # runs -> run_id
    JOBS_UNIT_FAIL_M,   # jobs -> step "Build and run unit tests" -> failure
    ARTS_EMPTY_M,       # artifacts -> none yet
    # poll 2 — FAIL detail
    PR_M,               # pulls -> sha
    CHECK_IP_M,         # check-runs -> in_progress
    RUNS_M,             # runs -> run_id
    JOBS_UNIT_FAIL_M,   # jobs -> step already seen, nothing new
    ARTS_UNIT_M,        # artifacts -> one new artifact
    ZIP_UNIT_M,         # zip (raw) -> FAIL line with trace
    # poll 3 — terminal Blocked
    PR_M,               # pulls -> sha
    CHECK_BL_M,         # check-runs -> Blocked (terminal)
    RUNS_M,             # runs -> run_id
    JOBS_UNIT_FAIL_M,   # jobs -> step already seen, nothing new
    ARTS_UNIT_M,        # artifacts -> artifact already seen, no zip call
])


def fake_request_m(url, token, raw=False):
    return side_effects_m.popleft()


clock_m = {"t": 0.0}


def fake_time_m():
    return clock_m["t"]


def fake_sleep_m(secs):
    clock_m["t"] += secs


buf_m = io.StringIO()
with unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_m), \
        unittest.mock.patch.object(ci_monitor.time, "time", side_effect=fake_time_m), \
        unittest.mock.patch.object(ci_monitor.time, "sleep", side_effect=fake_sleep_m), \
        unittest.mock.patch("sys.stdout", new=buf_m):
    rc_m = ci_monitor.main(["ci_monitor.py", "--pr", "272"])

out_m = buf_m.getvalue()
lines_m = out_m.splitlines()
step_line_m = 'PR#272: step "Build and run unit tests" -> failure'
fail_line_m = "PR#272: FAIL [com.gb4pc.unit.GalleryButtonTest] testIcon: java.lang.AssertionError: kaboom"
blocked_line_m = "PR#272: Blocked"
ip_line_m = "PR#272: in_progress"

check(lines_m.count(step_line_m) == 1,
      "step failure line emitted exactly once",
      "step failure line count != 1; output: %r" % out_m)
check(lines_m.count(fail_line_m) == 1,
      "FAIL line emitted exactly once",
      "FAIL line count != 1; output: %r" % out_m)
check(lines_m.count(blocked_line_m) == 1,
      "Blocked terminal line emitted exactly once",
      "Blocked terminal line count != 1; output: %r" % out_m)
check(lines_m.count(ip_line_m) == 0,
      "no in_progress heartbeat (each signal resets the 120s timer)",
      "unexpected in_progress heartbeat; output: %r" % out_m)
ordered_m = (
    step_line_m in lines_m and fail_line_m in lines_m and blocked_line_m in lines_m
    and lines_m.index(step_line_m) < lines_m.index(fail_line_m) < lines_m.index(blocked_line_m)
)
check(ordered_m,
      "ordering: step delta, then FAIL, then terminal Blocked",
      "ordering wrong; lines: %r" % lines_m)
check(any(ln.startswith("PR#272:   ") for ln in lines_m),
      "FAIL carries an indented trace line",
      "indented trace line missing; output: %r" % out_m)
check(len(side_effects_m) == 0,
      "all 16 mocked requests consumed (zip only on poll 2)",
      "request deque not drained; %d entries left" % len(side_effects_m))
check(rc_m == 0, "main() returned 0", "main() returned %r" % rc_m)


# ── (n) main(): quiet passing PR — two adjacent heartbeats then a single Clear ──
print("\n=== (n) main(): quiet polls emit two adjacent in_progress heartbeats, then Clear ===")

# Quiet in_progress polls produce no step/FAIL output, so the only PR#N lines
# before the terminal are the heartbeats, which fire only after >120s of
# silence. With the 30s advancing clock, the boundary is crossed twice across
# enough polls; the heartbeats are therefore adjacent in the output (no other
# PR#N line between them). The final all_passed poll emits Clear.
PR_N = {"head": {"sha": "c0ffee11"}}
CHECK_IP_N = {"total_count": 1, "check_runs": [{"status": "in_progress", "conclusion": None}]}
CHECK_PASS_N = {"total_count": 1, "check_runs": [{"status": "completed", "conclusion": "success"}]}
RUNS_N = {"workflow_runs": [{"id": 909, "status": "in_progress"}]}
JOBS_EMPTY_N = {"jobs": [{"name": "build-and-test", "steps": []}]}
ARTS_EMPTY_N = {"artifacts": []}
MPR_CLEAN_N = {"head": {"sha": "c0ffee11"}, "merged": False, "state": "open", "mergeable_state": "clean"}

# 11 quiet in_progress polls (5 requests each) then a final all_passed poll. The
# heartbeat fires only when now - last_output_ts > 120: first at poll 6 (t=150,
# >120 since start), which resets the timer, then again at poll 11 (t=300,
# >150+120). No quiet poll emits anything else, so the two heartbeats land on
# adjacent output lines. The all_passed terminal poll issues 6 requests: the
# usual 5 plus the mergeable-state /pulls fetch (mpr_json). 11*5 + 6 = 61
# entries, drained.
req_n = collections.deque()
for _ in range(11):
    req_n.append(PR_N)          # pulls -> sha
    req_n.append(CHECK_IP_N)    # check-runs -> in_progress
    req_n.append(RUNS_N)        # runs -> run_id
    req_n.append(JOBS_EMPTY_N)  # jobs -> nothing
    req_n.append(ARTS_EMPTY_N)  # artifacts -> nothing
# final all_passed poll
req_n.append(PR_N)              # pulls -> sha
req_n.append(CHECK_PASS_N)      # check-runs -> all_passed
req_n.append(RUNS_N)            # runs -> run_id
req_n.append(JOBS_EMPTY_N)      # jobs -> nothing
req_n.append(ARTS_EMPTY_N)      # artifacts -> nothing
req_n.append(MPR_CLEAN_N)       # pulls (mergeable_state) -> clean -> Clear


def fake_request_n(url, token, raw=False):
    return req_n.popleft()


clock_n = {"t": 0.0}


def fake_time_n():
    return clock_n["t"]


def fake_sleep_n(secs):
    clock_n["t"] += secs


buf_n = io.StringIO()
with unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_n), \
        unittest.mock.patch.object(ci_monitor.time, "time", side_effect=fake_time_n), \
        unittest.mock.patch.object(ci_monitor.time, "sleep", side_effect=fake_sleep_n), \
        unittest.mock.patch("sys.stdout", new=buf_n):
    rc_n = ci_monitor.main(["ci_monitor.py", "--pr", "272"])

out_n = buf_n.getvalue()
lines_n = out_n.splitlines()
heartbeat_line_n = "PR#272: in_progress"
clear_lines_n = [ln for ln in lines_n if ln.startswith("PR#272: Clear")]

hb_idx = [k for k, ln in enumerate(lines_n) if ln == heartbeat_line_n]
check(len(hb_idx) == 2,
      "exactly two in_progress heartbeats emitted (got %d)" % len(hb_idx),
      "in_progress count != 2; output: %r" % out_n)
check(len(hb_idx) == 2 and hb_idx[1] - hb_idx[0] == 1,
      "the two heartbeats are adjacent (no PR# line between them)",
      "heartbeats not adjacent; indices %r; output: %r" % (hb_idx, out_n))
pr_lines_n = [ln for ln in lines_n if ln.startswith("PR#272:")]
check(len(pr_lines_n) > 0 and pr_lines_n[0] == heartbeat_line_n,
      "first PR# line is a heartbeat (no earlier PR# output)",
      "first PR# line is not the heartbeat; output: %r" % out_n)
check(len(clear_lines_n) == 1,
      "exactly one Clear terminal line emitted",
      "Clear line count != 1; output: %r" % out_n)
check(len(clear_lines_n) == 1 and lines_n[-1] == clear_lines_n[0],
      "Clear is the last PR# line",
      "Clear is not the last line; output: %r" % out_n)
check(len(req_n) == 0,
      "all 61 mocked requests consumed (terminal poll includes mpr fetch)",
      "request deque not drained; %d entries left" % len(req_n))
check(rc_n == 0, "main() returned 0", "main() returned %r" % rc_n)


# ── Summary ────────────────────────────────────────────────────────────────────
print("\nResults: %d passed, %d failed." % (PASS, FAIL))
if FAIL > 0:
    sys.exit(1)
