#!/usr/bin/env python3
"""test_ci_monitor.py — Tests for ci_monitor.py.

Ports the 19 cases from the former test_ci_monitor.sh, calling the parser
functions directly (no subprocess shims). Plus a mocked-HTTP smoke test that
exercises the request helper without touching the network.

Covers:
  (a) Signal 1 step parser: all-success build-and-test emits exactly the 3 test-step lines
  (b) Signal 1 step parser: a genuine failure step is emitted
  (c) Signal 1 step parser: successful setup steps and skipped conditional steps are suppressed
  (d) Signal 1 step parser: deduplication across two iterations (same step not re-emitted)
  (e) Signal 2 artifact parser: FAIL with multi-line trace is emitted with indented trace
  (f) Signal 2 artifact parser: all-PASS artifact emits nothing
  (g) Signal 2 artifact parser: deduplication by suite#name across two calls

No network calls required; no GITHUB_TOKEN needed.
Always exits 0 on success, non-zero on failure.
"""

import json
import os
import sys
import unittest.mock

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

# ── Summary ────────────────────────────────────────────────────────────────────
print("\nResults: %d passed, %d failed." % (PASS, FAIL))
if FAIL > 0:
    sys.exit(1)
