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
  (o) Gap D: real subprocess prints its own PID and SIGTERM to that PID stops it
  (p) #258 fidelity: a real-shaped testresults-* artifact zip drives step+FAIL through main()
  (q) #259 real clock: heartbeat fires only after real >SILENCE_SECONDS silence; output resets it
  (r) #260 outcome filters: parse_fails obeys outcome_filters for FAIL/PASS/SKIP
  (s) #260 CLI flags: _parse_outcome_filters and main() pass filter flags through
  (t) #402 Gap E: drain_then_print surfaces a step/FAIL that lags one poll behind Blocked
  (u) #402 Gap E (review): drain_then_print's bounded retry recovers a two-poll lag
  (v) #415: Clear from parse_check_result (no checks) breaks the loop exactly once

No network calls required; no GITHUB_TOKEN needed.
Always exits 0 on success, non-zero on failure.
"""

import collections
import io
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import unittest.mock
import urllib.error
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "ci_monitor"))

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
# skips the zip call, then drain_then_print (Gap E) re-polls runs/jobs/artifacts
# up to DRAIN_MAX_ATTEMPTS times before printing the terminal line. Every drain
# attempt here finds the step/artifact already seen (nothing new), so all
# DRAIN_MAX_ATTEMPTS=3 attempts run. 6 + 5 + 3*3 = 20 entries; the deque must be
# exactly drained.
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
    # drain_then_print (Gap E) — up to DRAIN_MAX_ATTEMPTS extra signal polls
    # before the terminal line; all attempts find nothing new here.
    {"workflow_runs": [{"id": 555, "status": "in_progress"}]},  # drain attempt 1: runs
    JOBS_FAIL,          # drain attempt 1: jobs -> nothing new
    ARTS_JSON,          # drain attempt 1: artifacts -> nothing new
    {"workflow_runs": [{"id": 555, "status": "in_progress"}]},  # drain attempt 2: runs
    JOBS_FAIL,          # drain attempt 2: jobs -> nothing new
    ARTS_JSON,          # drain attempt 2: artifacts -> nothing new
    {"workflow_runs": [{"id": 555, "status": "in_progress"}]},  # drain attempt 3: runs
    JOBS_FAIL,          # drain attempt 3: jobs -> nothing new
    ARTS_JSON,          # drain attempt 3: artifacts -> nothing new
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
no_new_line_i = "PR#285: drain poll found no new diagnostic signals"
check(no_new_line_i in lines_i and lines_i.index(no_new_line_i) < lines_i.index(blocked_line_i),
      "drain poll found nothing new -> flagged immediately before terminal Blocked",
      "'drain poll found no new diagnostic signals' missing or misordered; output: %r" % out_i)
check(len(side_effects_i) == 0,
      "all 20 mocked requests consumed (zip skipped in iteration 2 and all 3 drain attempts)",
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
# Iteration 13 also triggers drain_then_print (Gap E), which re-polls
# runs/jobs/artifacts up to DRAIN_MAX_ATTEMPTS times (3 extra requests per
# attempt, no zip) before the terminal; every attempt finds nothing new here.
jobs_for_iter = [JOBS_STEP7 if n == 7 else JOBS_EMPTY for n in range(1, 14)]
checks_for_iter = [CHECK_BL if n == 13 else CHECK_IP for n in range(1, 14)]

req_j = collections.deque()
for n in range(13):
    req_j.append(PR_J)                 # pulls -> sha
    req_j.append(checks_for_iter[n])   # check-runs
    req_j.append(RUNS_J)               # runs -> run_id
    req_j.append(jobs_for_iter[n])     # jobs
    req_j.append(ARTS_EMPTY)           # artifacts (no zip)
# drain_then_print (Gap E) on the terminal Blocked iteration: DRAIN_MAX_ATTEMPTS
# attempts, each finding nothing new (step already seen, artifacts empty).
for _ in range(3):
    req_j.append(RUNS_J)                   # runs -> run_id
    req_j.append(JOBS_EMPTY)               # jobs -> step already seen, nothing new
    req_j.append(ARTS_EMPTY)               # artifacts (no zip)


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
no_new_line_j = "PR#285: drain poll found no new diagnostic signals"
check(no_new_line_j in lines_j and lines_j.index(no_new_line_j) < bl_idx,
      "drain poll found nothing new -> flagged immediately before terminal Blocked",
      "'drain poll found no new diagnostic signals' missing or misordered; output: %r" % out_j)
check(len(req_j) == 0,
      "all mocked requests consumed (65 + 9 drain entries drained)",
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
# Blocked; artifact already seen so no zip call. Then drain_then_print (Gap E)
# re-polls runs/jobs/artifacts up to DRAIN_MAX_ATTEMPTS times (3 each),
# everything already seen on every attempt, no zip.
# 5 + 6 + 5 + 3*3 = 25, drained.
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
    # drain_then_print (Gap E) — up to DRAIN_MAX_ATTEMPTS extra signal polls
    # before the terminal line; all attempts find nothing new here.
    RUNS_M, JOBS_UNIT_FAIL_M, ARTS_UNIT_M,  # drain attempt 1
    RUNS_M, JOBS_UNIT_FAIL_M, ARTS_UNIT_M,  # drain attempt 2
    RUNS_M, JOBS_UNIT_FAIL_M, ARTS_UNIT_M,  # drain attempt 3
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
no_new_line_m = "PR#272: drain poll found no new diagnostic signals"
check(no_new_line_m in lines_m and lines_m.index(no_new_line_m) < lines_m.index(blocked_line_m),
      "drain poll found nothing new -> flagged immediately before terminal Blocked",
      "'drain poll found no new diagnostic signals' missing or misordered; output: %r" % out_m)
check(len(side_effects_m) == 0,
      "all 25 mocked requests consumed (zip only on poll 2)",
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


# ── (o) Gap D: real subprocess advertises its PID and dies on SIGTERM ──────────
print("\n=== (o) Gap D: ci_monitor.py prints its real PID and stops on SIGTERM ===")

# The issue's Gap D viability check ("the script's $$ equals the real, killable
# PID, and kill -TERM is delivered") was carried as a manual step. Automate it:
# spawn the real script as a child process, read the advisory PID line, send
# SIGTERM to that exact PID, and confirm the process exits via the signal. A
# bogus token + the unreachable real API_BASE means the loop never gets past the
# SHA fetch, so it stays alive (sleeping) until we signal it — no network needed.
_MONITOR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ci_monitor", "ci_monitor.py")
_proc = subprocess.Popen(
    [sys.executable, _MONITOR_PATH, "--pr", "1"],
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    env={**os.environ, "GITHUB_TOKEN": "not-a-real-token"},
    text=True,
)


def _read_pid_line(proc, holder):
    holder["line"] = proc.stdout.readline()


_holder = {"line": ""}
_t = threading.Thread(target=_read_pid_line, args=(_proc, _holder))
_t.start()
_t.join(timeout=10)

advisory = _holder.get("line", "")
_m = re.search(r"monitor PID (\d+)", advisory)
check(_m is not None,
      "subprocess emits a 'monitor PID <n>' advisory line at startup",
      "no PID advisory line; got %r" % advisory)
check("SIGTERM" in advisory,
      "advisory names SIGTERM as the stop signal",
      "advisory does not mention SIGTERM; got %r" % advisory)

printed_pid = int(_m.group(1)) if _m else -1
check(printed_pid == _proc.pid,
      "printed PID equals the real, killable subprocess PID",
      "printed PID %r != real PID %r" % (printed_pid, _proc.pid))

# Send SIGTERM to the advertised PID and confirm the process actually stops.
try:
    os.kill(_proc.pid, signal.SIGTERM)
except OSError as e:  # pragma: no cover - only if the process already vanished
    _fail("could not deliver SIGTERM to PID %d: %s" % (_proc.pid, e))

try:
    _rc = _proc.wait(timeout=10)
    check(_rc != 0,
          "SIGTERM to the advertised PID stops the monitor (non-zero exit)",
          "process exited 0 unexpectedly (rc=%r)" % _rc)
    # On a default-disposition SIGTERM, Popen reports the negative signal number.
    check(_rc == -signal.SIGTERM,
          "process terminated by SIGTERM (rc == -SIGTERM)",
          "expected rc == %d; got %r" % (-signal.SIGTERM, _rc))
except subprocess.TimeoutExpired:
    _proc.kill()
    _proc.wait(timeout=10)
    _fail("subprocess did not exit within 10s of SIGTERM")


# ── (p) #258 fidelity: a real-shaped artifact zip drives step + FAIL ───────────
print("\n=== (p) #258 fidelity: real-shaped testresults-unit artifact -> step + FAIL via main() ===")

# Groups (i)/(m) prove the signal logic with a hand-rolled zip whose entry is
# named 'testresults.ndjson'. This group closes the live-verification gap from
# #258 by reproducing the artifact exactly as build.yml emits it:
#   - `... | tee >(grep '^##GB4PC_TEST##' > results/unit.ndjson)` writes only
#     marker-prefixed lines into results/unit.ndjson.
#   - `upload-artifact` with `path: results/unit.ndjson` zips it under the
#     basename entry 'unit.ndjson' inside an artifact named 'testresults-unit'.
# Driving main() through that exact shape confirms extract_ndjson_lines (which
# matches *.ndjson) and parse_fails read a genuine pipeline artifact, with the
# step delta and FAIL both emitted before the terminal and deduped across polls.
REAL_UNIT_NDJSON = [
    '##GB4PC_TEST## {"suite":"com.gb4pc.unit.GalleryButtonTest","name":"renders_icon","outcome":"FAIL","ms":7,"msg":"java.lang.AssertionError: icon not tinted","trace":"java.lang.AssertionError: icon not tinted\\n\\tat org.junit.Assert.fail(Assert.java:89)\\n\\tat com.gb4pc.unit.GalleryButtonTest.renders_icon(GalleryButtonTest.kt:58)"}',
    '##GB4PC_TEST## {"suite":"com.gb4pc.unit.GalleryButtonTest","name":"reads_pref","outcome":"PASS","ms":2,"msg":"","trace":""}',
]
_buf = io.BytesIO()
with zipfile.ZipFile(_buf, "w", zipfile.ZIP_DEFLATED) as _zf:
    # Basename entry, exactly as actions/upload-artifact stores a single-file path.
    _zf.writestr("unit.ndjson", "\n".join(REAL_UNIT_NDJSON))
REAL_UNIT_ZIP = _buf.getvalue()

PR_P = {"head": {"sha": "deadc0de"}}
CHECK_IP_P = {"total_count": 1, "check_runs": [{"status": "in_progress", "conclusion": None}]}
CHECK_BL_P = {"total_count": 1, "check_runs": [{"status": "completed", "conclusion": "failure"}]}
RUNS_P = {"workflow_runs": [{"id": 4242, "status": "in_progress"}]}
JOBS_UNIT_FAIL_P = {
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
# The artifact name mirrors build.yml's 'testresults-unit'; parse_new_artifacts
# keys on the 'testresults-' prefix and id, so the real name is exercised.
ARTS_REAL_UNIT_P = {"artifacts": [{"id": 4243, "name": "testresults-unit", "expired": False}]}

# Poll 1 (5): step delta, artifact not yet present. Poll 2 (6): step seen,
# artifact appears -> real-shaped zip downloaded -> FAIL emitted. Poll 3 (5):
# terminal Blocked, artifact already seen so no zip call. Then drain_then_print
# (Gap E) re-polls runs/jobs/artifacts up to DRAIN_MAX_ATTEMPTS times (3 each),
# already seen on every attempt, no zip.
# 5 + 6 + 5 + 3*3 = 25.
side_effects_p = collections.deque([
    PR_P, CHECK_IP_P, RUNS_P, JOBS_UNIT_FAIL_P, {"artifacts": []},
    PR_P, CHECK_IP_P, RUNS_P, JOBS_UNIT_FAIL_P, ARTS_REAL_UNIT_P, REAL_UNIT_ZIP,
    PR_P, CHECK_BL_P, RUNS_P, JOBS_UNIT_FAIL_P, ARTS_REAL_UNIT_P,
    # drain_then_print (Gap E) — up to DRAIN_MAX_ATTEMPTS extra signal polls
    # before the terminal line; all attempts find nothing new here.
    RUNS_P, JOBS_UNIT_FAIL_P, ARTS_REAL_UNIT_P,  # drain attempt 1
    RUNS_P, JOBS_UNIT_FAIL_P, ARTS_REAL_UNIT_P,  # drain attempt 2
    RUNS_P, JOBS_UNIT_FAIL_P, ARTS_REAL_UNIT_P,  # drain attempt 3
])


def fake_request_p(url, token, raw=False):
    return side_effects_p.popleft()


buf_p = io.StringIO()
with unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_p), \
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=2000.0), \
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None), \
        unittest.mock.patch("sys.stdout", new=buf_p):
    rc_p = ci_monitor.main(["ci_monitor.py", "--pr", "258"])

out_p = buf_p.getvalue()
lines_p = out_p.splitlines()
step_line_p = 'PR#258: step "Build and run unit tests" -> failure'
fail_line_p = "PR#258: FAIL [com.gb4pc.unit.GalleryButtonTest] renders_icon: java.lang.AssertionError: icon not tinted"
blocked_line_p = "PR#258: Blocked"

check(lines_p.count(step_line_p) == 1,
      "step failure line emitted exactly once from real-shaped pipeline",
      "step line count != 1; output: %r" % out_p)
check(lines_p.count(fail_line_p) == 1,
      "FAIL line parsed once from the 'unit.ndjson' artifact entry",
      "FAIL line count != 1; output: %r" % out_p)
check(any(ln.startswith("PR#258:   ") for ln in lines_p),
      "FAIL carries an indented trace line from the real artifact",
      "indented trace missing; output: %r" % out_p)
check(
    step_line_p in lines_p and fail_line_p in lines_p and blocked_line_p in lines_p
    and lines_p.index(step_line_p) < lines_p.index(fail_line_p) < lines_p.index(blocked_line_p),
    "ordering: step, then FAIL, then terminal Blocked — both signals before the job concludes",
    "ordering wrong; lines: %r" % lines_p)
no_new_line_p = "PR#258: drain poll found no new diagnostic signals"
check(no_new_line_p in lines_p and lines_p.index(no_new_line_p) < lines_p.index(blocked_line_p),
      "drain poll found nothing new -> flagged immediately before terminal Blocked",
      "'drain poll found no new diagnostic signals' missing or misordered; output: %r" % out_p)
check(len(side_effects_p) == 0,
      "all 25 mocked requests consumed (zip only on poll 2)",
      "request deque not drained; %d entries left" % len(side_effects_p))
check(rc_p == 0, "main() returned 0", "main() returned %r" % rc_p)


# ── (q) #259 real clock: heartbeat honors real wall-clock silence window ───────
print("\n=== (q) #259 real clock: in_progress fires only after real >SILENCE_SECONDS, output resets it ===")

# Groups (j)/(n) prove the >SILENCE_SECONDS logic with a *fabricated* clock,
# which cannot show the heartbeat is wired to real wall time. This group closes
# the #259 live-verification gap by exercising the genuine path: the real
# time.time() (deliberately NOT patched) gates the heartbeat, with
# SILENCE_SECONDS shrunk to a tiny window so the run is fast.
#
# To stay deterministic under CI timing jitter, every quiet poll sleeps a real
# interval comfortably larger than the window (SLEEP_Q >> WINDOW_Q). So each
# quiet poll *always* crosses the silence window and emits a heartbeat — there is
# no "is 0.24s > 0.30s?" boundary race. The reset property is then proven by a
# poll that emits a step delta: emit_block() sets last_output_ts to real now, and
# the in_progress gate re-reads now immediately after, so now - last_output_ts is
# ~0 (< window) and that poll emits its step but suppresses the heartbeat. A
# heartbeat on a quiet poll, none on the step poll, and a heartbeat again after
# proves real-time suppression and that an emitted line resets the real timer.
WINDOW_Q = 0.05   # silence window (s); real elapsed time gates the heartbeat
SLEEP_Q = 0.25    # per-poll real sleep, comfortably > WINDOW_Q so each quiet poll crosses it

PR_Q = {"head": {"sha": "ab1eca11"}}
CHECK_IP_Q = {"total_count": 1, "check_runs": [{"status": "in_progress", "conclusion": None}]}
CHECK_BL_Q = {"total_count": 1, "check_runs": [{"status": "completed", "conclusion": "failure"}]}
RUNS_Q = {"workflow_runs": [{"id": 31337, "status": "in_progress"}]}
JOBS_EMPTY_Q = {"jobs": [{"name": "build-and-test", "steps": []}]}
JOBS_STEP_Q = {
    "jobs": [
        {
            "name": "build-and-test",
            "steps": [
                {"number": 4, "name": "Build and run unit tests", "status": "completed", "conclusion": "success"},
            ],
        }
    ]
}
ARTS_EMPTY_Q = {"artifacts": []}

# 5 polls (each: pulls, check-runs, runs, jobs, artifacts; then a real sleep).
# main() reads last_output_ts = time.time() at startup, BEFORE poll 1, and each
# poll's silence check runs before that poll's own sleep — so a heartbeat needs a
# prior quiet sleep to have elapsed:
#   poll 1: quiet; now ~= startup (no sleep yet), diff ~0 -> NO heartbeat
#   poll 2: quiet; one SLEEP_Q elapsed (> window)         -> heartbeat #1, resets timer
#   poll 3: emits a step delta -> resets the real timer    -> NO heartbeat this poll
#   poll 4: quiet; one SLEEP_Q elapsed since the step      -> heartbeat #2, resets timer
#   poll 5: Blocked terminal, then drain_then_print (Gap E) re-polls
#           runs/jobs/artifacts up to DRAIN_MAX_ATTEMPTS times (everything
#           already seen on every attempt) before printing the terminal line.
JOBS_SCHEDULE_Q = [JOBS_EMPTY_Q, JOBS_EMPTY_Q, JOBS_STEP_Q, JOBS_EMPTY_Q, JOBS_EMPTY_Q]
CHECK_SCHEDULE_Q = [CHECK_IP_Q, CHECK_IP_Q, CHECK_IP_Q, CHECK_IP_Q, CHECK_BL_Q]

req_q = collections.deque()
for n in range(5):
    req_q.append(PR_Q)
    req_q.append(CHECK_SCHEDULE_Q[n])
    req_q.append(RUNS_Q)
    req_q.append(JOBS_SCHEDULE_Q[n])
    req_q.append(ARTS_EMPTY_Q)
# drain_then_print (Gap E) on the terminal Blocked poll: DRAIN_MAX_ATTEMPTS
# attempts, each finding nothing new.
for _ in range(3):
    req_q.append(RUNS_Q)
    req_q.append(JOBS_EMPTY_Q)
    req_q.append(ARTS_EMPTY_Q)


def fake_request_q(url, token, raw=False):
    return req_q.popleft()


# Capture the genuine time.sleep before patching: ci_monitor.time is the same
# module object as this module's `time`, so patching ci_monitor.time.sleep also
# rebinds time.sleep — calling time.sleep here would re-enter the mock and recurse.
_REAL_SLEEP = time.sleep


def real_sleep_q(_secs):
    # Ignore the script's 30s cadence; sleep a small real interval so the real
    # time.time() advances past WINDOW_Q and the wall-clock gate is exercised.
    _REAL_SLEEP(SLEEP_Q)


buf_q = io.StringIO()
with unittest.mock.patch.object(ci_monitor, "SILENCE_SECONDS", WINDOW_Q), \
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_q), \
        unittest.mock.patch.object(ci_monitor.time, "sleep", side_effect=real_sleep_q), \
        unittest.mock.patch("sys.stdout", new=buf_q):
    # time.time() is intentionally NOT patched: the real clock gates the heartbeat.
    rc_q = ci_monitor.main(["ci_monitor.py", "--pr", "259"])

out_q = buf_q.getvalue()
lines_q = out_q.splitlines()
ip_line_q = "PR#259: in_progress"
step_line_q = 'PR#259: step "Build and run unit tests" -> success'
blocked_line_q = "PR#259: Blocked"

check(lines_q.count(ip_line_q) == 2,
      "exactly two real-clock heartbeats emitted (got %d)" % lines_q.count(ip_line_q),
      "in_progress count != 2 under real clock; output: %r" % out_q)
check(lines_q.count(step_line_q) == 1,
      "the step delta is emitted exactly once",
      "step line count != 1; output: %r" % out_q)
# The step poll emits its delta but no heartbeat (the emission reset the real
# timer to ~now, so the same poll's in_progress gate sees ~0 < window). Exactly
# two heartbeats with the step strictly between them proves the real-time reset:
# without the reset, the step poll would also emit a heartbeat (3 total).
ip_idx_q = [k for k, ln in enumerate(lines_q) if ln == ip_line_q]
step_idx_q = lines_q.index(step_line_q) if step_line_q in lines_q else -1
check(len(ip_idx_q) == 2 and step_idx_q != -1 and ip_idx_q[0] < step_idx_q < ip_idx_q[1],
      "an emitted step line resets the real-time silence timer (no heartbeat on the step poll)",
      "step did not reset the real-time timer; lines: %r" % lines_q)
no_new_line_q = "PR#259: drain poll found no new diagnostic signals"
check(no_new_line_q in lines_q and lines_q.index(no_new_line_q) < lines_q.index(blocked_line_q),
      "drain poll found nothing new -> flagged immediately before terminal Blocked",
      "'drain poll found no new diagnostic signals' missing or misordered; output: %r" % out_q)
check(lines_q.count(blocked_line_q) == 1 and lines_q[-1] == blocked_line_q,
      "Blocked terminal emitted once as the final line",
      "terminal Blocked wrong; output: %r" % out_q)
check(len(req_q) == 0,
      "all real-clock poll requests consumed",
      "request deque not drained; %d entries left" % len(req_q))
check(rc_q == 0, "main() returned 0", "main() returned %r" % rc_q)


# ── (r) #260 outcome filters: parse_fails with explicit outcome_filters ────────
print("\n=== (r) #260 outcome filters: parse_fails obeys outcome_filters for FAIL/PASS/SKIP ===")

FILTER_NDJSON = [
    '##GB4PC_TEST## {"suite":"com.gb4pc.unit.FooTest","name":"test_fail","outcome":"FAIL","ms":1,"msg":"boom","trace":""}',
    '##GB4PC_TEST## {"suite":"com.gb4pc.unit.FooTest","name":"test_pass","outcome":"PASS","ms":2,"msg":"","trace":""}',
    '##GB4PC_TEST## {"suite":"com.gb4pc.unit.FooTest","name":"test_skip","outcome":"SKIP","ms":0,"msg":"","trace":""}',
    '##GB4PC_TEST## {"suite":"com.gb4pc.unit.BarTest","name":"test_fail_bar","outcome":"FAIL","ms":3,"msg":"kaboom","trace":""}',
    '##GB4PC_TEST## {"suite":"com.gb4pc.unit.BarTest","name":"test_pass_bar","outcome":"PASS","ms":4,"msg":"","trace":""}',
    '##GB4PC_TEST## {"suite":"com.gb4pc.unit.BarTest","name":"test_skip_bar","outcome":"SKIP","ms":0,"msg":"","trace":""}',
]

# Default behavior: all FAIL, all SKIP, no PASS
out_r_default = ci_monitor.parse_fails(FILTER_NDJSON, set())
out_r_default_str = "\n".join(out_r_default)
check("FAIL [com.gb4pc.unit.FooTest] test_fail:" in out_r_default_str,
      "default: FAIL marker emitted", "default: FAIL marker missing; output: %r" % out_r_default)
check("FAIL [com.gb4pc.unit.BarTest] test_fail_bar:" in out_r_default_str,
      "default: FAIL marker for BarTest emitted", "default: BarTest FAIL missing; output: %r" % out_r_default)
check("SKIP [com.gb4pc.unit.FooTest] test_skip:" in out_r_default_str,
      "default: SKIP marker emitted", "default: SKIP marker missing; output: %r" % out_r_default)
check("SKIP [com.gb4pc.unit.BarTest] test_skip_bar:" in out_r_default_str,
      "default: SKIP marker for BarTest emitted", "default: BarTest SKIP missing; output: %r" % out_r_default)
check("PASS" not in out_r_default_str,
      "default: no PASS markers emitted", "default: unexpected PASS in output: %r" % out_r_default)

# --no-include-fail: suppress all FAIL
out_r_nofail = ci_monitor.parse_fails(
    FILTER_NDJSON, set(),
    outcome_filters={"FAIL": (False, None), "SKIP": (True, None), "PASS": (False, None)},
)
out_r_nofail_str = "\n".join(out_r_nofail)
check("FAIL" not in out_r_nofail_str,
      "--no-include-fail: no FAIL emitted", "--no-include-fail: FAIL in output: %r" % out_r_nofail)
check("SKIP [com.gb4pc.unit.FooTest] test_skip:" in out_r_nofail_str,
      "--no-include-fail: SKIP still emitted", "--no-include-fail: SKIP missing; output: %r" % out_r_nofail)

# --no-include-skip: suppress all SKIP
out_r_noskip = ci_monitor.parse_fails(
    FILTER_NDJSON, set(),
    outcome_filters={"FAIL": (True, None), "SKIP": (False, None), "PASS": (False, None)},
)
out_r_noskip_str = "\n".join(out_r_noskip)
check("SKIP" not in out_r_noskip_str,
      "--no-include-skip: no SKIP emitted", "--no-include-skip: SKIP in output: %r" % out_r_noskip)
check("FAIL [com.gb4pc.unit.FooTest] test_fail:" in out_r_noskip_str,
      "--no-include-skip: FAIL still emitted", "--no-include-skip: FAIL missing; output: %r" % out_r_noskip)

# --include-pass '' (no pattern): all PASS markers emitted
out_r_allpass = ci_monitor.parse_fails(
    FILTER_NDJSON, set(),
    outcome_filters={"FAIL": (True, None), "SKIP": (True, None), "PASS": (True, None)},
)
out_r_allpass_str = "\n".join(out_r_allpass)
check("PASS [com.gb4pc.unit.FooTest] test_pass:" in out_r_allpass_str,
      "--include-pass: PASS marker emitted", "--include-pass: PASS missing; output: %r" % out_r_allpass)
check("PASS [com.gb4pc.unit.BarTest] test_pass_bar:" in out_r_allpass_str,
      "--include-pass: PASS marker for BarTest emitted", "--include-pass: BarTest PASS missing; output: %r" % out_r_allpass)

# --include-pass with a pattern: only matching passes emitted
out_r_patpass = ci_monitor.parse_fails(
    FILTER_NDJSON, set(),
    outcome_filters={"FAIL": (False, None), "SKIP": (False, None), "PASS": (True, "test_pass$")},
)
out_r_patpass_str = "\n".join(out_r_patpass)
check("PASS [com.gb4pc.unit.FooTest] test_pass:" in out_r_patpass_str,
      "--include-pass with pattern: matching PASS emitted",
      "--include-pass pattern: matching PASS missing; output: %r" % out_r_patpass)
check("PASS [com.gb4pc.unit.BarTest] test_pass_bar:" not in out_r_patpass_str,
      "--include-pass with pattern: non-matching PASS suppressed",
      "--include-pass pattern: non-matching PASS leaked; output: %r" % out_r_patpass)

# --include-fail with a pattern: only matching FAIL emitted
out_r_patfail = ci_monitor.parse_fails(
    FILTER_NDJSON, set(),
    outcome_filters={"FAIL": (True, "test_fail$"), "SKIP": (False, None), "PASS": (False, None)},
)
out_r_patfail_str = "\n".join(out_r_patfail)
check("FAIL [com.gb4pc.unit.FooTest] test_fail:" in out_r_patfail_str,
      "--include-fail with pattern: matching FAIL emitted",
      "--include-fail pattern: matching FAIL missing; output: %r" % out_r_patfail)
check("FAIL [com.gb4pc.unit.BarTest] test_fail_bar:" not in out_r_patfail_str,
      "--include-fail with pattern: non-matching FAIL suppressed",
      "--include-fail pattern: non-matching FAIL leaked; output: %r" % out_r_patfail)

# --include-skip with a pattern: only matching SKIP emitted
out_r_patskip = ci_monitor.parse_fails(
    FILTER_NDJSON, set(),
    outcome_filters={"FAIL": (False, None), "SKIP": (True, "test_skip$"), "PASS": (False, None)},
)
out_r_patskip_str = "\n".join(out_r_patskip)
check("SKIP [com.gb4pc.unit.FooTest] test_skip:" in out_r_patskip_str,
      "--include-skip with pattern: matching SKIP emitted",
      "--include-skip pattern: matching SKIP missing; output: %r" % out_r_patskip)
check("SKIP [com.gb4pc.unit.BarTest] test_skip_bar:" not in out_r_patskip_str,
      "--include-skip with pattern: non-matching SKIP suppressed",
      "--include-skip pattern: non-matching SKIP leaked; output: %r" % out_r_patskip)

# SKIP stays labeled SKIP, never relabeled as PASS (even with --include-pass '')
out_r_labelcheck = ci_monitor.parse_fails(
    FILTER_NDJSON, set(),
    outcome_filters={"FAIL": (False, None), "SKIP": (True, None), "PASS": (True, None)},
)
out_r_labelcheck_str = "\n".join(out_r_labelcheck)
check(all(not ln.startswith("PASS") for ln in out_r_labelcheck_str.splitlines()
          if "test_skip" in ln),
      "SKIP marker is never relabeled as PASS",
      "SKIP was relabeled as PASS; output: %r" % out_r_labelcheck)
check(any(ln.startswith("SKIP") for ln in out_r_labelcheck_str.splitlines()),
      "SKIP outcome retains SKIP label in output",
      "SKIP label missing; output: %r" % out_r_labelcheck)

# ── (s) #260 CLI flags: _parse_outcome_filters and main() respect filter flags ─
print("\n=== (s) #260 CLI flags: _parse_outcome_filters and main() pass filter flags through ===")

# _parse_outcome_filters: defaults (no flags)
import argparse as _argparse

def _make_args(**kw):
    """Build a namespace with the six flag attributes, defaulting to 'not supplied'."""
    defaults = {
        "include_fail": None, "no_include_fail": False,
        "include_skip": None, "no_include_skip": False,
        "include_pass": None, "no_include_pass": False,
    }
    defaults.update(kw)
    return _argparse.Namespace(**defaults)

# Defaults: all FAIL, all SKIP, no PASS
filters_default = ci_monitor._parse_outcome_filters(_make_args())
check(filters_default["FAIL"] == (True, None),
      "_parse_outcome_filters default FAIL=(True,None)",
      "_parse_outcome_filters FAIL default wrong; got %r" % (filters_default["FAIL"],))
check(filters_default["SKIP"] == (True, None),
      "_parse_outcome_filters default SKIP=(True,None)",
      "_parse_outcome_filters SKIP default wrong; got %r" % (filters_default["SKIP"],))
check(filters_default["PASS"] == (False, None),
      "_parse_outcome_filters default PASS=(False,None)",
      "_parse_outcome_filters PASS default wrong; got %r" % (filters_default["PASS"],))

# --no-include-fail
filters_nofail = ci_monitor._parse_outcome_filters(_make_args(no_include_fail=True))
check(filters_nofail["FAIL"] == (False, None),
      "_parse_outcome_filters --no-include-fail -> FAIL=(False,None)",
      "_parse_outcome_filters --no-include-fail wrong; got %r" % (filters_nofail["FAIL"],))

# --no-include-skip
filters_noskip = ci_monitor._parse_outcome_filters(_make_args(no_include_skip=True))
check(filters_noskip["SKIP"] == (False, None),
      "_parse_outcome_filters --no-include-skip -> SKIP=(False,None)",
      "_parse_outcome_filters --no-include-skip wrong; got %r" % (filters_noskip["SKIP"],))

# --no-include-pass (explicit form of default)
filters_nopass = ci_monitor._parse_outcome_filters(_make_args(no_include_pass=True))
check(filters_nopass["PASS"] == (False, None),
      "_parse_outcome_filters --no-include-pass -> PASS=(False,None)",
      "_parse_outcome_filters --no-include-pass wrong; got %r" % (filters_nopass["PASS"],))

# --include-pass '' (const, no pattern -> match all)
filters_allpass = ci_monitor._parse_outcome_filters(_make_args(include_pass=""))
check(filters_allpass["PASS"] == (True, None),
      "_parse_outcome_filters --include-pass '' -> PASS=(True,None)",
      "_parse_outcome_filters --include-pass '' wrong; got %r" % (filters_allpass["PASS"],))

# --include-pass 'MyTest' (pattern)
filters_patpass = ci_monitor._parse_outcome_filters(_make_args(include_pass="MyTest"))
check(filters_patpass["PASS"] == (True, "MyTest"),
      "_parse_outcome_filters --include-pass 'MyTest' -> PASS=(True,'MyTest')",
      "_parse_outcome_filters --include-pass pattern wrong; got %r" % (filters_patpass["PASS"],))

# --include-fail '' (supplied with no pattern -> match all)
filters_allfail = ci_monitor._parse_outcome_filters(_make_args(include_fail=""))
check(filters_allfail["FAIL"] == (True, None),
      "_parse_outcome_filters --include-fail '' -> FAIL=(True,None)",
      "_parse_outcome_filters --include-fail '' wrong; got %r" % (filters_allfail["FAIL"],))

# --include-fail 'Foo' (pattern)
filters_patfail = ci_monitor._parse_outcome_filters(_make_args(include_fail="Foo"))
check(filters_patfail["FAIL"] == (True, "Foo"),
      "_parse_outcome_filters --include-fail 'Foo' -> FAIL=(True,'Foo')",
      "_parse_outcome_filters --include-fail pattern wrong; got %r" % (filters_patfail["FAIL"],))

# main() with --include-pass '' emits PASS lines, not just FAIL/SKIP
# Reuse side-effect fixtures from earlier: two polls, in_progress then Blocked.
# The artifact contains one FAIL, one PASS, one SKIP. We expect FAIL+PASS+SKIP all
# emitted (since --include-pass '' is supplied), but only once each.
FILTER_NDJSON_MIXED = [
    '##GB4PC_TEST## {"suite":"com.gb4pc.unit.MixTest","name":"test_fail","outcome":"FAIL","ms":1,"msg":"oops","trace":""}',
    '##GB4PC_TEST## {"suite":"com.gb4pc.unit.MixTest","name":"test_pass","outcome":"PASS","ms":2,"msg":"","trace":""}',
    '##GB4PC_TEST## {"suite":"com.gb4pc.unit.MixTest","name":"test_skip","outcome":"SKIP","ms":0,"msg":"","trace":""}',
]
ZIP_MIXED = make_zip_ndjson(FILTER_NDJSON_MIXED)

PR_S = {"head": {"sha": "5ce5c0de"}}
CHECK_IP_S = {"total_count": 1, "check_runs": [{"status": "in_progress", "conclusion": None}]}
CHECK_BL_S = {"total_count": 1, "check_runs": [{"status": "completed", "conclusion": "failure"}]}
RUNS_S = {"workflow_runs": [{"id": 1111, "status": "in_progress"}]}
JOBS_EMPTY_S = {"jobs": [{"name": "build-and-test", "steps": []}]}
ARTS_MIX_S = {"artifacts": [{"id": 2222, "name": "testresults-mix", "expired": False}]}

# Poll 1 (6): artifact available, zip downloaded; poll 2 (5): terminal Blocked,
# then drain_then_print (Gap E) re-polls runs/jobs/artifacts up to
# DRAIN_MAX_ATTEMPTS times (3 each), artifact already seen on every attempt so
# no zip call.
side_effects_s = collections.deque([
    PR_S, CHECK_IP_S, RUNS_S, JOBS_EMPTY_S, ARTS_MIX_S, ZIP_MIXED,
    PR_S, CHECK_BL_S, RUNS_S, JOBS_EMPTY_S, ARTS_MIX_S,
    RUNS_S, JOBS_EMPTY_S, ARTS_MIX_S,  # drain attempt 1
    RUNS_S, JOBS_EMPTY_S, ARTS_MIX_S,  # drain attempt 2
    RUNS_S, JOBS_EMPTY_S, ARTS_MIX_S,  # drain attempt 3
])


def fake_request_s(url, token, raw=False):
    return side_effects_s.popleft()


buf_s = io.StringIO()
with unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_s), \
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=3000.0), \
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None), \
        unittest.mock.patch("sys.stdout", new=buf_s):
    rc_s = ci_monitor.main(["ci_monitor.py", "--pr", "260", "--include-pass", ""])

out_s = buf_s.getvalue()
lines_s = out_s.splitlines()
check("PR#260: FAIL [com.gb4pc.unit.MixTest] test_fail: oops" in lines_s,
      "main() --include-pass '': FAIL line emitted",
      "main() --include-pass '': FAIL missing; output: %r" % out_s)
check(any("PR#260: PASS [com.gb4pc.unit.MixTest] test_pass:" in ln for ln in lines_s),
      "main() --include-pass '': PASS line emitted",
      "main() --include-pass '': PASS missing; output: %r" % out_s)
check(any("PR#260: SKIP [com.gb4pc.unit.MixTest] test_skip:" in ln for ln in lines_s),
      "main() --include-pass '': SKIP line emitted and stays labeled SKIP",
      "main() --include-pass '': SKIP missing or mislabeled; output: %r" % out_s)
no_new_line_s = "PR#260: drain poll found no new diagnostic signals"
check(no_new_line_s in lines_s,
      "main() --include-pass '': drain poll found nothing new -> flagged before terminal",
      "main() --include-pass '': 'drain poll found no new diagnostic signals' missing; output: %r" % out_s)
check(len(side_effects_s) == 0,
      "main() --include-pass '': all 20 mocked requests consumed",
      "request deque not drained; %d entries left" % len(side_effects_s))
check(rc_s == 0, "main() --include-pass '' returned 0", "main() returned %r" % rc_s)

# main() with no flags: FAIL+SKIP emitted, no PASS
side_effects_s2 = collections.deque([
    PR_S, CHECK_IP_S, RUNS_S, JOBS_EMPTY_S, ARTS_MIX_S, ZIP_MIXED,
    PR_S, CHECK_BL_S, RUNS_S, JOBS_EMPTY_S, ARTS_MIX_S,
    RUNS_S, JOBS_EMPTY_S, ARTS_MIX_S,  # drain attempt 1
    RUNS_S, JOBS_EMPTY_S, ARTS_MIX_S,  # drain attempt 2
    RUNS_S, JOBS_EMPTY_S, ARTS_MIX_S,  # drain attempt 3
])


def fake_request_s2(url, token, raw=False):
    return side_effects_s2.popleft()


buf_s2 = io.StringIO()
with unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_s2), \
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=3000.0), \
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None), \
        unittest.mock.patch("sys.stdout", new=buf_s2):
    rc_s2 = ci_monitor.main(["ci_monitor.py", "--pr", "260"])

out_s2 = buf_s2.getvalue()
lines_s2 = out_s2.splitlines()
check(any("FAIL [com.gb4pc.unit.MixTest] test_fail:" in ln for ln in lines_s2),
      "main() no flags: FAIL emitted",
      "main() no flags: FAIL missing; output: %r" % out_s2)
check(any("SKIP [com.gb4pc.unit.MixTest] test_skip:" in ln for ln in lines_s2),
      "main() no flags: SKIP emitted",
      "main() no flags: SKIP missing; output: %r" % out_s2)
check(not any("PASS" in ln for ln in lines_s2 if not ln.startswith("monitor PID")),
      "main() no flags: no PASS emitted",
      "main() no flags: unexpected PASS; output: %r" % out_s2)
check(len(side_effects_s2) == 0,
      "main() no flags: all 20 mocked requests consumed",
      "request deque not drained; %d entries left" % len(side_effects_s2))

# main() with --no-include-fail: only SKIP emitted (no FAIL, no PASS)
# The trailing entries cover drain_then_print's (Gap E) up to DRAIN_MAX_ATTEMPTS
# extra signal polls before the terminal line; the artifact is already seen on
# every attempt so no zip call.
side_effects_s3 = collections.deque([
    PR_S, CHECK_IP_S, RUNS_S, JOBS_EMPTY_S, ARTS_MIX_S, ZIP_MIXED,
    PR_S, CHECK_BL_S, RUNS_S, JOBS_EMPTY_S, ARTS_MIX_S,
    RUNS_S, JOBS_EMPTY_S, ARTS_MIX_S,  # drain attempt 1
    RUNS_S, JOBS_EMPTY_S, ARTS_MIX_S,  # drain attempt 2
    RUNS_S, JOBS_EMPTY_S, ARTS_MIX_S,  # drain attempt 3
])


def fake_request_s3(url, token, raw=False):
    return side_effects_s3.popleft()


buf_s3 = io.StringIO()
with unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_s3), \
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=3000.0), \
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None), \
        unittest.mock.patch("sys.stdout", new=buf_s3):
    rc_s3 = ci_monitor.main(["ci_monitor.py", "--pr", "260", "--no-include-fail"])

out_s3 = buf_s3.getvalue()
lines_s3 = out_s3.splitlines()
check(not any("FAIL" in ln for ln in lines_s3 if "PR#260:" in ln),
      "main() --no-include-fail: no FAIL emitted",
      "main() --no-include-fail: FAIL leaked; output: %r" % out_s3)
check(any("SKIP [com.gb4pc.unit.MixTest] test_skip:" in ln for ln in lines_s3),
      "main() --no-include-fail: SKIP still emitted",
      "main() --no-include-fail: SKIP missing; output: %r" % out_s3)

# main() with --no-include-skip: only FAIL emitted (no SKIP, no PASS)
# The trailing entries cover drain_then_print's (Gap E) up to DRAIN_MAX_ATTEMPTS
# extra signal polls before the terminal line; the artifact is already seen on
# every attempt so no zip call.
side_effects_s4 = collections.deque([
    PR_S, CHECK_IP_S, RUNS_S, JOBS_EMPTY_S, ARTS_MIX_S, ZIP_MIXED,
    PR_S, CHECK_BL_S, RUNS_S, JOBS_EMPTY_S, ARTS_MIX_S,
    RUNS_S, JOBS_EMPTY_S, ARTS_MIX_S,  # drain attempt 1
    RUNS_S, JOBS_EMPTY_S, ARTS_MIX_S,  # drain attempt 2
    RUNS_S, JOBS_EMPTY_S, ARTS_MIX_S,  # drain attempt 3
])


def fake_request_s4(url, token, raw=False):
    return side_effects_s4.popleft()


buf_s4 = io.StringIO()
with unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_s4), \
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=3000.0), \
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None), \
        unittest.mock.patch("sys.stdout", new=buf_s4):
    rc_s4 = ci_monitor.main(["ci_monitor.py", "--pr", "260", "--no-include-skip"])

out_s4 = buf_s4.getvalue()
lines_s4 = out_s4.splitlines()
check(not any("SKIP" in ln for ln in lines_s4 if "PR#260:" in ln),
      "main() --no-include-skip: no SKIP emitted",
      "main() --no-include-skip: SKIP leaked; output: %r" % out_s4)
check(any("FAIL [com.gb4pc.unit.MixTest] test_fail:" in ln for ln in lines_s4),
      "main() --no-include-skip: FAIL still emitted",
      "main() --no-include-skip: FAIL missing; output: %r" % out_s4)


# ── (t) Gap E (#402): drain_then_print surfaces a step/FAIL that lags behind
#       the Blocked terminal by exactly one poll ─────────────────────────────
print("\n=== (t) Gap E (#402): drain poll surfaces step+FAIL that lag behind Blocked ===")

# Reproduces Run B/E/G from issue #402: check-runs flips straight from
# in_progress to failure (Blocked) on poll 1, while /actions/runs/{id}/jobs
# still shows the failing "Gate on test failures" step as not-yet-completed
# and the testresults-* artifact is not yet listed. Without the drain, poll 1
# would emit Blocked with zero step/FAIL lines. With drain_then_print, the
# same poll's terminal line is followed by one extra signal poll
# (DRAIN_DELAY_SECONDS later) where the jobs/artifacts endpoints have caught
# up, surfacing the step failure and FAIL marker before the terminal line.
PR_T = {"head": {"sha": "9001dead"}}
CHECK_BL_T = {"total_count": 1, "check_runs": [{"status": "completed", "conclusion": "failure"}]}
RUNS_T = {"workflow_runs": [{"id": 9001, "status": "completed"}]}
JOBS_EMPTY_T = {"jobs": [{"name": "build-and-test", "steps": []}]}
JOBS_GATE_FAIL_T = {
    "jobs": [
        {
            "name": "build-and-test",
            "steps": [
                {"number": 1, "name": "Set up job", "status": "completed", "conclusion": "success"},
                {"number": 4, "name": "Build and run unit tests", "status": "completed", "conclusion": "success"},
                {"number": 5, "name": "Run PixelCameraOverlayE2ETest", "status": "completed", "conclusion": "success"},
                {"number": 6, "name": "Run GalleryButtonVisualE2ETest", "status": "completed", "conclusion": "success"},
                {"number": 7, "name": "Gate on test failures", "status": "completed", "conclusion": "failure"},
            ],
        }
    ]
}
ARTS_EMPTY_T = {"artifacts": []}
ARTS_E2E_T = {"artifacts": [{"id": 5005, "name": "testresults-e2e-gallery", "expired": False}]}
ZIP_E2E_T = make_zip_ndjson([
    '##GB4PC_TEST## {"suite":"com.gb4pc.e2e.GalleryButtonVisualE2ETest","name":"test1a","outcome":"FAIL","ms":9,"msg":"java.lang.AssertionError: button not green","trace":""}',
])

# Poll 1 (5): check-runs already Blocked (terminal), but jobs/artifacts not yet
# caught up -> no step/FAIL lines from poll_signals. drain_then_print then
# sleeps DRAIN_DELAY_SECONDS and re-polls runs/jobs/artifacts (3 + zip): jobs
# now shows the failing gate step and the artifact is listed -> step + FAIL
# emitted, then the Blocked terminal line. 5 + 3 + 1 = 9.
side_effects_t = collections.deque([
    PR_T,             # pulls -> sha
    CHECK_BL_T,       # check-runs -> Blocked (terminal), decided on poll 1
    RUNS_T,           # runs -> run_id
    JOBS_EMPTY_T,     # jobs -> not yet caught up, nothing new
    ARTS_EMPTY_T,     # artifacts -> not yet listed
    # drain_then_print (Gap E): one extra signal poll, now caught up
    RUNS_T,           # runs -> run_id
    JOBS_GATE_FAIL_T, # jobs -> "Gate on test failures" -> failure
    ARTS_E2E_T,       # artifacts -> testresults-e2e-gallery now listed
    ZIP_E2E_T,        # zip (raw) -> FAIL line for test1a
])


def fake_request_t(url, token, raw=False):
    return side_effects_t.popleft()


buf_t = io.StringIO()
with unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_t), \
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=4000.0), \
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None), \
        unittest.mock.patch("sys.stdout", new=buf_t):
    rc_t = ci_monitor.main(["ci_monitor.py", "--pr", "402"])

out_t = buf_t.getvalue()
lines_t = out_t.splitlines()
gate_step_line_t = 'PR#402: step "Gate on test failures" -> failure'
fail_line_t = "PR#402: FAIL [com.gb4pc.e2e.GalleryButtonVisualE2ETest] test1a: java.lang.AssertionError: button not green"
blocked_line_t = "PR#402: Blocked"

check(gate_step_line_t in lines_t,
      "drain poll surfaces the lagging 'Gate on test failures' step failure",
      "gate step failure line missing; output: %r" % out_t)
check(fail_line_t in lines_t,
      "drain poll surfaces the lagging per-test FAIL marker",
      "FAIL line missing; output: %r" % out_t)
check(lines_t.count(blocked_line_t) == 1,
      "Blocked terminal line emitted exactly once",
      "Blocked terminal line count != 1; output: %r" % out_t)
check(
    gate_step_line_t in lines_t and fail_line_t in lines_t and blocked_line_t in lines_t
    and lines_t.index(gate_step_line_t) < lines_t.index(blocked_line_t)
    and lines_t.index(fail_line_t) < lines_t.index(blocked_line_t),
    "ordering: drained step and FAIL lines precede the terminal Blocked line",
    "ordering wrong; lines: %r" % lines_t)
check(len(side_effects_t) == 0,
      "all 9 mocked requests consumed (drain poll downloads the newly-listed zip)",
      "request deque not drained; %d entries left" % len(side_effects_t))
check(rc_t == 0, "main() returned 0", "main() returned %r" % rc_t)


# ── (u) Gap E (#402 review): drain_then_print's bounded retry recovers a
#       two-poll lag (Run G shape) that a single drain attempt would miss ──────
print("\n=== (u) Gap E (#402 review): drain attempt 2 surfaces step+FAIL after attempt 1 finds nothing ===")

# A reviewer concern on PR #408 was that a single DRAIN_DELAY_SECONDS re-poll
# only covers a one-poll lag (Runs B/C/E/F/T), not a longer lag like Run G's. This
# group reproduces a two-poll lag: check-runs flips to failure (Blocked) on poll
# 1, drain attempt 1 still finds jobs/artifacts not caught up (nothing new), and
# only drain attempt 2 sees the failing gate step and the FAIL marker. With
# DRAIN_MAX_ATTEMPTS=3, attempt 2 still runs and surfaces both before the
# terminal line, and the "drain poll found no new diagnostic signals" line is
# NOT printed (drain attempt 2 found something new).
PR_U = {"head": {"sha": "900110ng"}}
CHECK_BL_U = {"total_count": 1, "check_runs": [{"status": "completed", "conclusion": "failure"}]}
RUNS_U = {"workflow_runs": [{"id": 9002, "status": "completed"}]}
JOBS_EMPTY_U = {"jobs": [{"name": "build-and-test", "steps": []}]}
JOBS_GATE_FAIL_U = {
    "jobs": [
        {
            "name": "build-and-test",
            "steps": [
                {"number": 1, "name": "Set up job", "status": "completed", "conclusion": "success"},
                {"number": 4, "name": "Build and run unit tests", "status": "completed", "conclusion": "success"},
                {"number": 5, "name": "Run PixelCameraOverlayE2ETest", "status": "completed", "conclusion": "success"},
                {"number": 6, "name": "Run GalleryButtonVisualE2ETest", "status": "completed", "conclusion": "success"},
                {"number": 7, "name": "Gate on test failures", "status": "completed", "conclusion": "failure"},
            ],
        }
    ]
}
ARTS_EMPTY_U = {"artifacts": []}
ARTS_E2E_U = {"artifacts": [{"id": 5006, "name": "testresults-e2e-gallery", "expired": False}]}
ZIP_E2E_U = make_zip_ndjson([
    '##GB4PC_TEST## {"suite":"com.gb4pc.e2e.GalleryButtonVisualE2ETest","name":"test1a","outcome":"FAIL","ms":9,"msg":"java.lang.AssertionError: button not green","trace":""}',
])

# Poll 1 (5): check-runs already Blocked (terminal), jobs/artifacts not caught
# up. Drain attempt 1 (3): still nothing new. Drain attempt 2 (3 + zip): jobs
# now shows the failing gate step and the artifact is listed -> step + FAIL
# emitted, drain stops (DRAIN_MAX_ATTEMPTS allows up to 3, but 2 suffices).
# 5 + 3 + 4 = 12.
side_effects_u = collections.deque([
    PR_U,             # pulls -> sha
    CHECK_BL_U,       # check-runs -> Blocked (terminal), decided on poll 1
    RUNS_U,           # runs -> run_id
    JOBS_EMPTY_U,     # jobs -> not yet caught up, nothing new
    ARTS_EMPTY_U,     # artifacts -> not yet listed
    # drain attempt 1 — still not caught up
    RUNS_U,           # runs -> run_id
    JOBS_EMPTY_U,     # jobs -> still not yet caught up, nothing new
    ARTS_EMPTY_U,     # artifacts -> still not yet listed
    # drain attempt 2 — now caught up
    RUNS_U,           # runs -> run_id
    JOBS_GATE_FAIL_U, # jobs -> "Gate on test failures" -> failure
    ARTS_E2E_U,       # artifacts -> testresults-e2e-gallery now listed
    ZIP_E2E_U,        # zip (raw) -> FAIL line for test1a
])


def fake_request_u(url, token, raw=False):
    return side_effects_u.popleft()


buf_u = io.StringIO()
with unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_u), \
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=4100.0), \
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None), \
        unittest.mock.patch("sys.stdout", new=buf_u):
    rc_u = ci_monitor.main(["ci_monitor.py", "--pr", "402"])

out_u = buf_u.getvalue()
lines_u = out_u.splitlines()
gate_step_line_u = 'PR#402: step "Gate on test failures" -> failure'
fail_line_u = "PR#402: FAIL [com.gb4pc.e2e.GalleryButtonVisualE2ETest] test1a: java.lang.AssertionError: button not green"
blocked_line_u = "PR#402: Blocked"
no_new_line_u = "PR#402: drain poll found no new diagnostic signals"

check(gate_step_line_u in lines_u,
      "drain attempt 2 surfaces the lagging 'Gate on test failures' step failure",
      "gate step failure line missing; output: %r" % out_u)
check(fail_line_u in lines_u,
      "drain attempt 2 surfaces the lagging per-test FAIL marker",
      "FAIL line missing; output: %r" % out_u)
check(lines_u.count(blocked_line_u) == 1,
      "Blocked terminal line emitted exactly once",
      "Blocked terminal line count != 1; output: %r" % out_u)
check(
    gate_step_line_u in lines_u and fail_line_u in lines_u and blocked_line_u in lines_u
    and lines_u.index(gate_step_line_u) < lines_u.index(blocked_line_u)
    and lines_u.index(fail_line_u) < lines_u.index(blocked_line_u),
    "ordering: drained step and FAIL lines (from attempt 2) precede the terminal Blocked line",
    "ordering wrong; lines: %r" % lines_u)
check(no_new_line_u not in lines_u,
      "drain attempt 2 found new signals -> 'drain poll found no new diagnostic signals' NOT printed",
      "unexpected 'drain poll found no new diagnostic signals'; output: %r" % out_u)
check(len(side_effects_u) == 0,
      "all 12 mocked requests consumed (drain attempt 1 empty, attempt 2 downloads the zip)",
      "request deque not drained; %d entries left" % len(side_effects_u))
check(rc_u == 0, "main() returned 0", "main() returned %r" % rc_u)


# ── (v) #415: Clear from parse_check_result (no checks) breaks the loop ───────
print("\n=== (v) #415: Clear (no check runs) emits exactly one Clear terminal and exits ===")

# Reproduces issue #415: when /commits/{sha}/check-runs reports total_count==0
# (no CI checks registered), parse_check_result returns 'Clear'. Before the fix,
# this fell through to the elif result is not None: catch-all which printed the
# Clear line but did NOT break, causing the script to loop and re-print 'Clear'
# on every subsequent poll until the 30-minute timeout.
#
# After the fix, the main loop detects result == "Clear" and breaks immediately
# after printing the terminal line exactly once, without a drain (no failing
# signals exist when there are no check runs).
#
# Scenario: poll 1 fetches the SHA (open PR), check-runs returns no checks
# (total_count=0 -> parse_check_result='Clear') -> terminal Clear emitted, loop
# exits. Per-iteration request order: pulls (sha), check-runs; poll_signals is
# called but finds no run_id (no workflow_runs) so issues only the runs request
# (1 extra call) before check-runs result is evaluated and the loop breaks.
# 2 + 1 = 3 entries in the deque.
PR_V = {"head": {"sha": "00c1ea12"}, "merged": False, "state": "open"}
CHECK_CLEAR_V = {"total_count": 0, "check_runs": []}
RUNS_EMPTY_V = {"workflow_runs": []}

side_effects_v = collections.deque([
    PR_V,           # pulls -> sha, terminal == '' (open)
    CHECK_CLEAR_V,  # check-runs -> total_count=0 -> Clear (terminal)
    RUNS_EMPTY_V,   # runs -> no run_id -> poll_signals returns False
])


def fake_request_v(url, token, raw=False):
    return side_effects_v.popleft()


buf_v = io.StringIO()
with unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_v), \
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=5000.0), \
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None), \
        unittest.mock.patch("sys.stdout", new=buf_v):
    rc_v = ci_monitor.main(["ci_monitor.py", "--pr", "415"])

out_v = buf_v.getvalue()
lines_v = out_v.splitlines()
clear_lines_v = [ln for ln in lines_v if ln.startswith("PR#415: Clear")]

check(len(clear_lines_v) == 1,
      "Clear (no check runs) emitted exactly once (got %d)" % len(clear_lines_v),
      "Clear line count != 1; output: %r" % out_v)
check(len(side_effects_v) == 0,
      "all 3 mocked requests consumed (loop exits after first Clear)",
      "request deque not drained; %d entries left" % len(side_effects_v))
check(rc_v == 0, "main() returned 0", "main() returned %r" % rc_v)


# ── Summary ────────────────────────────────────────────────────────────────────
print("\nResults: %d passed, %d failed." % (PASS, FAIL))
if FAIL > 0:
    sys.exit(1)
