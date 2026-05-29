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

No network calls required; no GITHUB_TOKEN needed.
Always exits 0 on success, non-zero on failure.
"""

import collections
import io
import json
import os
import sys
import unittest.mock
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


# ── Summary ────────────────────────────────────────────────────────────────────
print("\nResults: %d passed, %d failed." % (PASS, FAIL))
if FAIL > 0:
    sys.exit(1)
