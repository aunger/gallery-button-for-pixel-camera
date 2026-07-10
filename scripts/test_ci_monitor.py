#!/usr/bin/env python3
"""test_ci_monitor.py: Tests for ci_monitor.py.

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
  (p) #258 fidelity: a real-shaped ci-monitor-feed-* artifact zip drives step+FAIL through main()
  (q) #259 real clock: heartbeat fires only after real >SILENCE_SECONDS silence; output resets it
  (r) #260 outcome filters: parse_fails obeys outcome_filters for FAIL/PASS/SKIP
  (s) #260 CLI flags: _parse_outcome_filters and main() pass filter flags through
  (t) #402 Gap E: drain_then_print surfaces a step/FAIL that lags one poll behind Blocked
  (u) #402 Gap E (review): drain_then_print's bounded retry recovers a two-poll lag
  (v) #415: Clear from parse_check_result (no checks) breaks the loop exactly once
  (w) #419: two endpoints settle on different drain attempts; both signals surfaced
  (x) #500 parse_actions_targets: derives (run_id, job_id) pairs from check-runs data
  (y) #500 parse_steps: filters reported steps by job id
  (z) #500 parse_steps: configurable interesting_step_regex; genuine failures unconditional
  (aa) #500 parse_new_artifacts: configurable artifact_name_regex
  (ab) #500 parse_fails: dual-marker back-compat and the ##TEST## default behavior
  (ac) #500 load_config: present/absent/invalid/partial/bad-regex fallbacks; repo config
  (ad) #499/#500: the failing build run is tracked among multiple Actions checks (wiring b)
  (ae) #500 doc-sync: no legacy hardcoded workflow/job/marker literals remain
  (af) #500 poll_signals: a run-only target does not widen another run's job-id filter
  (al) #619: importing this module directly runs no checks and has no side effects
  (am) #603 parse_run_result: classifies --run-id status/conclusion into
       in_progress/all_passed/Blocked/Infra
  (an) #603 parse_commit_sha: reads the top-level sha from a /commits/{ref} response
  (ao) #603 argparse: exactly one of --pr/--sha/--run-id/--branch is required;
       two or none is rejected
  (ap) #603 main(): --sha mode Clear and Blocked paths, no mergeable_state gating
  (aq) #603 main(): --branch mode re-resolves the head SHA each poll; Clear and Blocked
  (ar) #603 main(): --run-id mode scopes verdict/diagnostics to the run object itself,
       never fetching /commits/{sha}/check-runs
  (as) #603 regression: fetch_pr_with_retry delegates to fetch_with_retry; --pr's
       output format is unchanged by the multi-mode refactor

No network calls required; no GITHUB_TOKEN needed.
Run this file directly to execute the suite: exits 0 on success, non-zero on failure.
Importing it as a module (for example, to reuse check_runs_payload()) runs no checks.
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
                {
                    "number": 4,
                    "name": "Build and run unit tests",
                    "status": "completed",
                    "conclusion": "success",
                },
                {
                    "number": 5,
                    "name": "Run PixelCameraOverlayE2ETest",
                    "status": "completed",
                    "conclusion": "success",
                },
                {
                    "number": 6,
                    "name": "Run GalleryButtonVisualE2ETest",
                    "status": "completed",
                    "conclusion": "success",
                },
                {
                    "number": 7,
                    "name": "Upload test results on failure",
                    "status": "completed",
                    "conclusion": "skipped",
                },
                {
                    "number": 8,
                    "name": "Complete job",
                    "status": "completed",
                    "conclusion": "success",
                },
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
                {
                    "number": 2,
                    "name": "Download AVD",
                    "status": "completed",
                    "conclusion": "failure",
                },
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

# This repo's committed config values (scripts/ci_monitor/ci_monitor.config.json).
# The parser-direct tests pass these explicitly so they exercise the same regexes
# main() loads from the config file (issue #500).
REPO_STEP_REGEX = "Build and run unit tests|^Run .*E2ETest$"
REPO_MARKER_REGEX = "##GB4PC_TEST##|##TEST##"
REPO_ARTIFACT_REGEX = "^ci-monitor-feed-"


def check_runs_payload(*pairs):
    """Build a /commits/{sha}/check-runs payload from (run_id, job_id) pairs.

    Each pair becomes a github-actions check run whose details_url encodes the
    run id and (when job_id is not None) the job id, as parse_actions_targets
    reads them. Used for both the combined verdict+diagnostic payload (wiring (a),
    issue #512) and the drain self-fetch payloads, replacing the old
    {"workflow_runs": [...]} fixtures.

    Each entry includes status/conclusion so parse_check_result can iterate the
    full combined payload without KeyError. The github-actions job check runs
    are always completed/success here; the verdict is determined by the
    non-Actions status check run(s) also present in combined payloads.
    """
    runs = []
    for run_id, job_id in pairs:
        url = "https://github.com/%s/%s/actions/runs/%s" % (OWNER_T, REPO_T, run_id)
        if job_id is not None:
            url += "/job/%s" % job_id
        runs.append(
            {
                "app": {"slug": "github-actions"},
                "details_url": url,
                "status": "completed",
                "conclusion": "success",
            }
        )
    return {"total_count": len(runs), "check_runs": runs}


OWNER_T = ci_monitor.OWNER
REPO_T = ci_monitor.REPO


def main() -> int:
    """Run every check (a) through (al) and print PASS/FAIL for each.

    Returns 1 if any check failed, 0 otherwise.
    Only runs when this file is executed directly; see the __main__ guard below.
    The fixtures and helpers above (including check_runs_payload()) stay at module
    scope so they can be imported and reused without running the suite; only the
    check-invocation sequence below--the part with real side effects, such as
    check (o)'s subprocess/SIGTERM and check (q)'s real time.sleep--is gated
    behind this function.
    """
    # ── (a) All-success: exactly 3 test-step lines emitted ─────────────────────────
    print("\n=== (a) Signal 1: all-success build-and-test emits exactly 3 test-step lines ===")
    out_a = ci_monitor.parse_steps(ALL_SUCCESS_JOBS, set(), None, REPO_STEP_REGEX)
    step_lines_a = [ln for ln in out_a if ln.startswith("step ")]
    check(
        len(step_lines_a) == 3,
        "emits exactly 3 step lines (got %d)" % len(step_lines_a),
        "expected 3 step lines, got %d; output: %r" % (len(step_lines_a), out_a),
    )
    check(
        any('step "Build and run unit tests" -> success' == ln for ln in out_a),
        "unit tests step line present",
        "unit tests step line missing; output: %r" % out_a,
    )
    check(
        any('step "Run PixelCameraOverlayE2ETest" -> success' == ln for ln in out_a),
        "PixelCameraOverlayE2ETest step line present",
        "PixelCameraOverlayE2ETest step line missing; output: %r" % out_a,
    )
    check(
        any('step "Run GalleryButtonVisualE2ETest" -> success' == ln for ln in out_a),
        "GalleryButtonVisualE2ETest step line present",
        "GalleryButtonVisualE2ETest step line missing; output: %r" % out_a,
    )

    # ── (b) Genuine failure step is emitted ────────────────────────────────────────
    print("\n=== (b) Signal 1: a genuine failure step is emitted ===")
    out_b = ci_monitor.parse_steps(FAILURE_JOBS, set(), None, REPO_STEP_REGEX)
    check(
        any('step "Download AVD" -> failure' == ln for ln in out_b),
        "failed step 'Download AVD' is emitted",
        "failed step not emitted; output: %r" % out_b,
    )
    check(
        not any("Set up job" in ln for ln in out_b),
        "successful setup step 'Set up job' correctly suppressed",
        "successful setup step 'Set up job' should NOT be emitted; output: %r" % out_b,
    )

    # ── (c) Setup and skipped conditional steps are suppressed ─────────────────────
    print(
        "\n=== (c) Signal 1: successful setup steps and skipped conditional steps are suppressed ==="
    )
    out_c = ci_monitor.parse_steps(ALL_SUCCESS_JOBS, set(), None, REPO_STEP_REGEX)
    check(
        not any('"Set up job"' in ln for ln in out_c),
        "'Set up job' suppressed",
        "'Set up job' should be suppressed; output: %r" % out_c,
    )
    check(
        not any('"Checkout"' in ln for ln in out_c),
        "'Checkout' suppressed",
        "'Checkout' should be suppressed; output: %r" % out_c,
    )
    check(
        not any('"Set up JDK"' in ln for ln in out_c),
        "'Set up JDK' suppressed",
        "'Set up JDK' should be suppressed; output: %r" % out_c,
    )
    check(
        not any('"Upload test results on failure"' in ln for ln in out_c),
        "'Upload test results on failure' (skipped) suppressed",
        "'Upload test results on failure' (skipped) should be suppressed; output: %r" % out_c,
    )
    check(
        not any('"Complete job"' in ln for ln in out_c),
        "'Complete job' suppressed",
        "'Complete job' should be suppressed; output: %r" % out_c,
    )

    # ── (d) Deduplication across two iterations ─────────────────────────────────────
    print("\n=== (d) Signal 1: deduplication: same steps not re-emitted on second iteration ===")
    seen_d = set()
    out_d1 = ci_monitor.parse_steps(ALL_SUCCESS_JOBS, seen_d, None, REPO_STEP_REGEX)
    out_d2 = ci_monitor.parse_steps(ALL_SUCCESS_JOBS, seen_d, None, REPO_STEP_REGEX)
    check(
        out_d2 == [],
        "second iteration emits nothing (all steps already seen)",
        "second iteration re-emitted steps: %r" % out_d2,
    )
    step_lines_d1 = [ln for ln in out_d1 if ln.startswith("step ")]
    check(
        len(step_lines_d1) == 3,
        "first iteration still emitted 3 step lines before dedup kicks in",
        "first iteration emitted %d step lines (expected 3)" % len(step_lines_d1),
    )

    # ── (e) FAIL with multi-line trace emitted with indented trace ──────────────────
    print("\n=== (e) Signal 2: FAIL with multi-line trace is emitted with indented trace ===")
    out_e = ci_monitor.parse_fails(FAIL_NDJSON, set(), test_marker_regex=REPO_MARKER_REGEX)
    joined_e = "\n".join(out_e)
    check(
        "FAIL [com.gb4pc.e2e.GalleryButtonVisualE2ETest] test1a:" in joined_e,
        "FAIL line for test1a emitted",
        "FAIL line for test1a not found; output: %r" % out_e,
    )
    check(
        "java.lang.AssertionError: expected button visible" in joined_e,
        "failure message present in output",
        "failure message missing; output: %r" % out_e,
    )
    check(
        any(ln.startswith("  ") for ln in joined_e.split("\n")),
        "trace lines are indented",
        "trace lines are not indented; output: %r" % out_e,
    )

    # ── (f) All-PASS artifact emits nothing ────────────────────────────────────────
    print("\n=== (f) Signal 2: all-PASS artifact emits nothing ===")
    out_f = ci_monitor.parse_fails(PASS_ONLY_NDJSON, set(), test_marker_regex=REPO_MARKER_REGEX)
    check(
        out_f == [],
        "all-PASS artifact produces no output",
        "all-PASS artifact unexpectedly produced output: %r" % out_f,
    )

    # ── (g) Deduplication by suite#name across two calls ───────────────────────────
    print("\n=== (g) Signal 2: deduplication by suite#name across two calls ===")
    seen_g = set()
    out_g1 = ci_monitor.parse_fails(FAIL_NDJSON, seen_g, test_marker_regex=REPO_MARKER_REGEX)
    check(
        any("FAIL [com.gb4pc.e2e.GalleryButtonVisualE2ETest] test1a:" in ln for ln in out_g1),
        "first call emits FAIL",
        "first call did not emit FAIL; output: %r" % out_g1,
    )
    out_g2 = ci_monitor.parse_fails(FAIL_NDJSON, seen_g, test_marker_regex=REPO_MARKER_REGEX)
    check(
        out_g2 == [],
        "second call produces no output (FAIL already seen)",
        "second call re-emitted already-seen FAIL: %r" % out_g2,
    )

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
        ci_monitor.urllib.request,
        "urlopen",
        return_value=_FakeResp(json.dumps({"head": {"sha": "deadbeef"}}).encode()),
    ):
        got = ci_monitor._request("https://example.invalid/pulls/1", "tok")
    check(
        ci_monitor.parse_pr_sha(got) == "deadbeef",
        "mocked /pulls response parses head.sha",
        "mocked /pulls response did not parse; got: %r" % got,
    )

    # ── (i) main(): failing unit test signals emitted once, before terminal ────────
    print("\n=== (i) main(): step failure + FAIL emitted exactly once, before terminal Blocked ===")

    PR_JSON = {"head": {"sha": "cafef00d"}}
    CHECK_INPROGRESS = {
        "total_count": 1,
        "check_runs": [{"status": "in_progress", "conclusion": None}],
    }
    CHECK_BLOCKED = {
        "total_count": 1,
        "check_runs": [{"name": "build-and-test", "status": "completed", "conclusion": "failure"}],
    }
    JOBS_FAIL = {
        "jobs": [
            {
                "id": 42,
                "name": "build-and-test",
                "steps": [
                    {
                        "number": 1,
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "number": 4,
                        "name": "Build and run unit tests",
                        "status": "completed",
                        "conclusion": "failure",
                    },
                ],
            }
        ]
    }
    ARTS_JSON = {"artifacts": [{"id": 9001, "name": "ci-monitor-feed-unit", "expired": False}]}
    # Drain self-fetch payload: one github-actions check run pointing at run 555,
    # job 42. Under wiring (a), drain attempts still self-fetch (check_json=None).
    DIAG_CHECK_I = check_runs_payload(("555", "42"))
    ZIP_BYTES = make_zip_ndjson(
        [
            '##GB4PC_TEST## {"suite":"com.gb4pc.unit.GalleryButtonTest","name":"testClick","outcome":"FAIL","ms":3,"msg":"java.lang.AssertionError: boom","trace":"java.lang.AssertionError: boom\\n\\tat com.gb4pc.unit.GalleryButtonTest.testClick(GalleryButtonTest.kt:21)"}',
        ]
    )

    # Under wiring (a) the same check-runs payload drives both the verdict and
    # poll_signals. The combined payloads for test (i) include both the status
    # check (in_progress or failure, for parse_check_result) and the Actions check
    # run pointing at run 555/job 42 (for parse_actions_targets / poll_signals).
    CHECK_IP_WITH_RUN_I = {
        "total_count": 2,
        "check_runs": CHECK_INPROGRESS["check_runs"] + DIAG_CHECK_I["check_runs"],
    }
    CHECK_BL_WITH_RUN_I = {
        "total_count": 2,
        "check_runs": CHECK_BLOCKED["check_runs"] + DIAG_CHECK_I["check_runs"],
    }

    # Per iteration the request order under wiring (a) (issue #512) is: pulls (sha),
    # verdict check-runs (reused by poll_signals), jobs, artifacts, [zip per new
    # artifact]. Iteration 1 (in_progress) downloads the zip; iteration 2 (Blocked,
    # terminal) finds the artifact already seen and skips the zip call, then
    # drain_then_print (Gap E) re-polls check-runs/jobs/artifacts up to
    # DRAIN_MAX_ATTEMPTS times before printing the terminal line. Every drain attempt
    # here finds the step/artifact already seen (nothing new), so all
    # DRAIN_MAX_ATTEMPTS=3 attempts run. 5 + 4 + 3*3 = 18 entries; the deque must be
    # exactly drained.
    side_effects_i = collections.deque(
        [
            # iteration 1
            PR_JSON,  # pulls -> sha
            CHECK_IP_WITH_RUN_I,  # verdict check-runs -> in_progress (reused by poll_signals)
            JOBS_FAIL,  # jobs -> step failure
            ARTS_JSON,  # artifacts -> one new artifact
            ZIP_BYTES,  # zip (raw) -> FAIL line
            # iteration 2
            PR_JSON,  # pulls -> sha
            CHECK_BL_WITH_RUN_I,  # verdict check-runs -> Blocked (terminal, reused by poll_signals)
            JOBS_FAIL,  # jobs -> step already seen, nothing new
            ARTS_JSON,  # artifacts -> artifact already seen, no zip call
            # drain_then_print (Gap E): up to DRAIN_MAX_ATTEMPTS extra signal polls
            # before the terminal line; all attempts find nothing new here.
            DIAG_CHECK_I,  # drain attempt 1: diagnostic check-runs
            JOBS_FAIL,  # drain attempt 1: jobs -> nothing new
            ARTS_JSON,  # drain attempt 1: artifacts -> nothing new
            DIAG_CHECK_I,  # drain attempt 2: diagnostic check-runs
            JOBS_FAIL,  # drain attempt 2: jobs -> nothing new
            ARTS_JSON,  # drain attempt 2: artifacts -> nothing new
            DIAG_CHECK_I,  # drain attempt 3: diagnostic check-runs
            JOBS_FAIL,  # drain attempt 3: jobs -> nothing new
            ARTS_JSON,  # drain attempt 3: artifacts -> nothing new
        ]
    )

    def fake_request_i(url, token, raw=False):
        return side_effects_i.popleft()

    buf_i = io.StringIO()
    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_i),
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=1000.0),
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None),
        unittest.mock.patch("sys.stdout", new=buf_i),
    ):
        rc_i = ci_monitor.main(["ci_monitor.py", "--pr", "285"])

    out_i = buf_i.getvalue()
    lines_i = out_i.splitlines()
    step_line_i = 'PR#285: step "Build and run unit tests" -> failure'
    fail_line_i = (
        "PR#285: FAIL [com.gb4pc.unit.GalleryButtonTest] testClick: java.lang.AssertionError: boom"
    )
    # Terminal is now attributed with the blocking check name.
    blocked_line_i = "PR#285: Blocked by: build-and-test"
    summary_hdr_i = "PR#285: summary"

    check(
        lines_i.count(step_line_i) == 1,
        "step failure line emitted exactly once",
        "step failure line count != 1; output: %r" % out_i,
    )
    check(
        lines_i.count(fail_line_i) == 1,
        "FAIL line emitted exactly once",
        "FAIL line count != 1; output: %r" % out_i,
    )
    check(
        lines_i.count(blocked_line_i) == 1,
        "Blocked attributed terminal line emitted exactly once",
        "Blocked attributed terminal line count != 1; output: %r" % out_i,
    )
    check(
        step_line_i in lines_i
        and blocked_line_i in lines_i
        and lines_i.index(step_line_i) < lines_i.index(blocked_line_i),
        "step failure line precedes terminal Blocked",
        "step failure line not before Blocked; output: %r" % out_i,
    )
    check(
        fail_line_i in lines_i
        and blocked_line_i in lines_i
        and lines_i.index(fail_line_i) < lines_i.index(blocked_line_i),
        "FAIL line precedes terminal Blocked",
        "FAIL line not before Blocked; output: %r" % out_i,
    )
    # drain flag is suppressed because the check (build-and-test) is blocking/diagnosed.
    no_new_line_i = "PR#285: drain poll found no new diagnostic signals"
    check(
        no_new_line_i not in lines_i,
        "drain flag suppressed (named blocking check diagnoses the terminal)",
        "'drain poll found no new diagnostic signals' unexpectedly present; output: %r" % out_i,
    )
    # Summary block appears before the terminal.
    check(
        summary_hdr_i in lines_i
        and blocked_line_i in lines_i
        and lines_i.index(summary_hdr_i) < lines_i.index(blocked_line_i),
        "summary header appears before terminal Blocked",
        "summary header missing or after terminal; output: %r" % out_i,
    )
    check(
        any("build-and-test" in ln and "failure" in ln and "[BLOCKING]" in ln for ln in lines_i),
        "summary row for build-and-test shows failure and [BLOCKING]",
        "summary row for build-and-test missing or incorrect; output: %r" % out_i,
    )
    check(
        len(side_effects_i) == 0,
        "all 18 mocked requests consumed (zip skipped in iteration 2 and all 3 drain attempts)",
        "request deque not drained; %d entries left" % len(side_effects_i),
    )
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
        "check_runs": [{"name": "build-and-test", "status": "completed", "conclusion": "failure"}],
    }
    # Drain self-fetch payload: a run-only details_url (no job id), so parse_steps
    # applies no job filter and the unnamed build-and-test job fixtures below still
    # match. Under wiring (a), drain attempts still self-fetch (check_json=None).
    DIAG_CHECK_J = check_runs_payload(("777", None))
    JOBS_EMPTY = {"jobs": [{"name": "build-and-test", "steps": []}]}
    JOBS_STEP7 = {
        "jobs": [
            {
                "name": "build-and-test",
                "steps": [
                    {
                        "number": 7,
                        "name": "Build and run unit tests",
                        "status": "completed",
                        "conclusion": "success",
                    },
                ],
            }
        ]
    }
    ARTS_EMPTY = {"artifacts": []}

    # Under wiring (a) the verdict payload is reused by poll_signals. Combined
    # payloads for test (j) include the Actions check run (run 777, run-only) so
    # parse_actions_targets discovers the target from the same fetch used for the
    # verdict.
    CHECK_IP_WITH_RUN_J = {
        "total_count": 2,
        "check_runs": CHECK_IP["check_runs"] + DIAG_CHECK_J["check_runs"],
    }
    CHECK_BL_WITH_RUN_J = {
        "total_count": 2,
        "check_runs": CHECK_BL["check_runs"] + DIAG_CHECK_J["check_runs"],
    }

    # Each iteration issues exactly 4 requests under wiring (a) (issue #512): pulls,
    # verdict check-runs (reused by poll_signals), jobs, artifacts; no zip is ever
    # downloaded (artifacts empty). 13 iterations -> 52. The 13 iterations supply
    # the verdict check-runs in_progress for 1..12 and Blocked at 13. Iteration 13
    # also triggers drain_then_print (Gap E), which re-polls
    # check-runs/jobs/artifacts up to DRAIN_MAX_ATTEMPTS times (3 extra requests per
    # attempt, no zip) before the terminal; every attempt finds nothing new here.
    jobs_for_iter = [JOBS_STEP7 if n == 7 else JOBS_EMPTY for n in range(1, 14)]
    checks_for_iter = [
        CHECK_BL_WITH_RUN_J if n == 13 else CHECK_IP_WITH_RUN_J for n in range(1, 14)
    ]

    req_j = collections.deque()
    for n in range(13):
        req_j.append(PR_J)  # pulls -> sha
        req_j.append(checks_for_iter[n])  # verdict check-runs (reused by poll_signals)
        req_j.append(jobs_for_iter[n])  # jobs
        req_j.append(ARTS_EMPTY)  # artifacts (no zip)
    # drain_then_print (Gap E) on the terminal Blocked iteration: DRAIN_MAX_ATTEMPTS
    # attempts, each finding nothing new (step already seen, artifacts empty).
    for _ in range(3):
        req_j.append(DIAG_CHECK_J)  # diagnostic check-runs -> run 777
        req_j.append(JOBS_EMPTY)  # jobs -> step already seen, nothing new
        req_j.append(ARTS_EMPTY)  # artifacts (no zip)

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
    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_j),
        unittest.mock.patch.object(ci_monitor.time, "time", side_effect=fake_time_j),
        unittest.mock.patch.object(ci_monitor.time, "sleep", side_effect=fake_sleep_j),
        unittest.mock.patch("sys.stdout", new=buf_j),
    ):
        rc_j = ci_monitor.main(["ci_monitor.py", "--pr", "285"])

    out_j = buf_j.getvalue()
    lines_j = out_j.splitlines()
    ip_line = "PR#285: in_progress"
    step_line_j = 'PR#285: step "Build and run unit tests" -> success'
    # Terminal is now attributed with the blocking check name.
    blocked_line_j = "PR#285: Blocked by: build-and-test"

    check(
        lines_j.count(ip_line) == 2,
        "in_progress heartbeat emitted exactly twice (got %d)" % lines_j.count(ip_line),
        "in_progress count != 2; output: %r" % out_j,
    )
    check(
        lines_j.count(step_line_j) == 1,
        "step line emitted exactly once",
        "step line count != 1; output: %r" % out_j,
    )
    check(
        lines_j.count(blocked_line_j) == 1,
        "Blocked attributed terminal line emitted exactly once",
        "Blocked attributed terminal line count != 1; output: %r" % out_j,
    )

    ip_idx = [k for k, ln in enumerate(lines_j) if ln == ip_line]
    step_idx = lines_j.index(step_line_j) if step_line_j in lines_j else -1
    bl_idx = lines_j.index(blocked_line_j) if blocked_line_j in lines_j else -1
    check(
        len(ip_idx) == 2
        and step_idx != -1
        and bl_idx != -1
        and ip_idx[0] < step_idx < ip_idx[1] < bl_idx,
        "ordering: first in_progress, step, second in_progress, Blocked",
        "ordering wrong; lines: %r" % lines_j,
    )
    # drain flag suppressed because the check (build-and-test) is blocking/diagnosed.
    no_new_line_j = "PR#285: drain poll found no new diagnostic signals"
    check(
        no_new_line_j not in lines_j,
        "drain flag suppressed (named blocking check diagnoses the terminal)",
        "'drain poll found no new diagnostic signals' unexpectedly present; output: %r" % out_j,
    )
    check(
        len(req_j) == 0,
        "all mocked requests consumed (52 + 9 drain entries drained)",
        "request deque not drained; %d entries left" % len(req_j),
    )
    check(rc_j == 0, "main() returned 0", "main() returned %r" % rc_j)

    # ── (k) Gap A: closed/merged PR termination ────────────────────────────────────
    print("\n=== (k) Gap A: parse_pr_terminal maps merged/closed/open ===")

    check(
        ci_monitor.parse_pr_terminal({"merged": True, "state": "closed"}) == "Merged",
        "parse_pr_terminal returns 'Merged' when merged is true",
        "expected 'Merged'; got %r"
        % ci_monitor.parse_pr_terminal({"merged": True, "state": "closed"}),
    )
    check(
        ci_monitor.parse_pr_terminal({"merged": False, "state": "closed"}) == "Closed",
        "parse_pr_terminal returns 'Closed' when state is closed (not merged)",
        "expected 'Closed'; got %r"
        % ci_monitor.parse_pr_terminal({"merged": False, "state": "closed"}),
    )
    check(
        ci_monitor.parse_pr_terminal({"merged": False, "state": "open"}) == "",
        "parse_pr_terminal returns '' when PR is open",
        "expected ''; got %r" % ci_monitor.parse_pr_terminal({"merged": False, "state": "open"}),
    )

    # Integration: iteration 1 is in_progress, iteration 2 the PR is merged. The
    # terminal check runs right after the SHA fetch (before check-runs), so on
    # iteration 2 main() emits 'PR#N: Merged' and breaks without issuing the
    # check-runs/jobs/artifacts calls. Per-iteration request order under wiring (a)
    # (issue #512) is: pulls (sha), verdict check-runs (reused by poll_signals),
    # jobs, artifacts, [zip per new artifact]. Iteration 1 (open + in_progress, empty
    # artifacts) issues 4 requests; iteration 2 short-circuits after the single
    # pulls fetch. 4 + 1 = 5 entries, drained.
    print("\n=== (k) main(): merged PR emits terminal 'Merged' and exits cleanly ===")

    PR_OPEN_K = {"head": {"sha": "feedface"}, "merged": False, "state": "open"}
    PR_MERGED_K = {"head": {"sha": "feedface"}, "merged": True, "state": "closed"}
    # Under wiring (a), CHECK_IP_K is reused by poll_signals; include an Actions
    # check run (run 888) so parse_actions_targets discovers the target and
    # poll_signals fetches jobs+artifacts.
    _diag_k = check_runs_payload(("888", None))
    CHECK_IP_K = {
        "total_count": 2,
        "check_runs": [{"status": "in_progress", "conclusion": None}] + _diag_k["check_runs"],
    }
    JOBS_EMPTY_K = {"jobs": [{"name": "build-and-test", "steps": []}]}
    ARTS_EMPTY_K = {"artifacts": []}

    side_effects_k = collections.deque(
        [
            # iteration 1: open, in_progress (no heartbeat: clock frozen at start)
            PR_OPEN_K,  # pulls -> sha, terminal == ''
            CHECK_IP_K,  # verdict check-runs -> in_progress (reused by poll_signals)
            JOBS_EMPTY_K,  # jobs -> nothing
            ARTS_EMPTY_K,  # artifacts -> nothing
            # iteration 2: merged: terminal short-circuit before check-runs
            PR_MERGED_K,  # pulls -> sha, terminal == 'Merged' -> break
        ]
    )

    def fake_request_k(url, token, raw=False):
        return side_effects_k.popleft()

    buf_k = io.StringIO()
    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_k),
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=1000.0),
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None),
        unittest.mock.patch("sys.stdout", new=buf_k),
    ):
        rc_k = ci_monitor.main(["ci_monitor.py", "--pr", "290"])

    out_k = buf_k.getvalue()
    lines_k = out_k.splitlines()
    merged_line_k = "PR#290: Merged"
    check(
        lines_k.count(merged_line_k) == 1,
        "Merged terminal line emitted exactly once",
        "Merged terminal line count != 1; output: %r" % out_k,
    )
    check(
        not any("still computing" in ln for ln in lines_k),
        "no 'still computing' spin while terminating on merged",
        "unexpected 'still computing' line; output: %r" % out_k,
    )
    check(
        len(side_effects_k) == 0,
        "all 5 mocked requests consumed (merged short-circuits iteration 2)",
        "request deque not drained; %d entries left" % len(side_effects_k),
    )
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
    check(ra == 42, "Retry-After header parsed as 42s", "expected 42; got %r" % ra)

    # X-RateLimit-Reset header (Unix timestamp -> delta from now).
    xr = ci_monitor._retry_after_seconds(_FakeHTTPError(403, {"X-RateLimit-Reset": "1100"}), 1000.0)
    check(
        xr == 100, "X-RateLimit-Reset parsed as (reset - now) = 100s", "expected 100; got %r" % xr
    )

    # Clamp to 300s ceiling (huge reset far in the future).
    clamp = ci_monitor._retry_after_seconds(
        _FakeHTTPError(403, {"X-RateLimit-Reset": "100000"}), 1000.0
    )
    check(clamp == 300, "huge backoff clamped to 300s max", "expected 300; got %r" % clamp)

    # Non-rate-limit status returns None (no backoff hint).
    none_status = ci_monitor._retry_after_seconds(
        _FakeHTTPError(500, {"Retry-After": "10"}), 1000.0
    )
    check(
        none_status is None,
        "non-403/429 status yields no hint (None)",
        "expected None; got %r" % none_status,
    )

    # Rate-limit status but no usable header returns None.
    none_hdr = ci_monitor._retry_after_seconds(_FakeHTTPError(429, {}), 1000.0)
    check(
        none_hdr is None,
        "rate-limit status without headers yields None",
        "expected None; got %r" % none_hdr,
    )

    # Invalid Retry-After value falls through to None (no X-RateLimit-Reset).
    bad_ra = ci_monitor._retry_after_seconds(_FakeHTTPError(429, {"Retry-After": "soon"}), 1000.0)
    check(bad_ra is None, "non-numeric Retry-After yields None", "expected None; got %r" % bad_ra)

    print("\n=== (l) fetch_pr_with_retry: first-try success, retry-then-success, all-fail ===")

    PR_RETRY = {"head": {"sha": "0ddba11"}}

    # Success on the first try: one _request call, no sleep.
    calls_first = {"n": 0}

    def req_first(url, token, raw=False):
        calls_first["n"] += 1
        return PR_RETRY

    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=req_first),
        unittest.mock.patch.object(
            ci_monitor.time, "sleep", side_effect=AssertionError("should not sleep")
        ),
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=1000.0),
    ):
        got_first = ci_monitor.fetch_pr_with_retry("290", "tok")
    check(
        got_first == PR_RETRY and calls_first["n"] == 1,
        "fetch_pr_with_retry succeeds on first try with no sleep",
        "first-try fetch wrong; got %r after %d calls" % (got_first, calls_first["n"]),
    )

    # Transient URLError twice, then success. Backoff sleeps recorded.
    err = urllib.error.URLError("temporary blip")
    retry_results = collections.deque([None, None, PR_RETRY])
    slept = []

    def req_retry(url, token, raw=False):
        r = retry_results.popleft()
        # Emulate _request recording the last transient error on failure.
        ci_monitor._request.last_error = None if r is not None else err
        return r

    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=req_retry),
        unittest.mock.patch.object(ci_monitor.time, "sleep", side_effect=slept.append),
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=1000.0),
    ):
        got_retry = ci_monitor.fetch_pr_with_retry("290", "tok", attempts=3, base_delay=2)
    check(
        got_retry == PR_RETRY,
        "fetch_pr_with_retry retries transient failures and eventually succeeds",
        "retry-then-success wrong; got %r" % got_retry,
    )
    check(
        slept == [2, 4],
        "exponential backoff sleeps were 2s then 4s before success",
        "backoff schedule wrong; slept %r" % slept,
    )
    check(
        len(retry_results) == 0,
        "all retry responses consumed",
        "retry deque not drained; %d left" % len(retry_results),
    )

    # Every attempt fails -> returns None after `attempts` tries, sleeps attempts-1 times.
    fail_calls = {"n": 0}
    slept_fail = []

    def req_allfail(url, token, raw=False):
        fail_calls["n"] += 1
        ci_monitor._request.last_error = err
        return None

    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=req_allfail),
        unittest.mock.patch.object(ci_monitor.time, "sleep", side_effect=slept_fail.append),
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=1000.0),
    ):
        got_fail = ci_monitor.fetch_pr_with_retry("290", "tok", attempts=3, base_delay=2)
    check(
        got_fail is None,
        "fetch_pr_with_retry returns None when every attempt fails",
        "expected None; got %r" % got_fail,
    )
    check(
        fail_calls["n"] == 3,
        "fetch_pr_with_retry made exactly `attempts` (3) requests",
        "expected 3 requests; got %d" % fail_calls["n"],
    )
    check(
        slept_fail == [2, 4],
        "slept between the 3 failed attempts (2s, 4s), not after the last",
        "fail backoff schedule wrong; slept %r" % slept_fail,
    )

    # Rate-limit hint overrides exponential backoff: 403 with Retry-After=7.
    rl_err = _FakeHTTPError(429, {"Retry-After": "7"})
    rl_results = collections.deque([None, PR_RETRY])
    slept_rl = []

    def req_ratelimit(url, token, raw=False):
        r = rl_results.popleft()
        ci_monitor._request.last_error = None if r is not None else rl_err
        return r

    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=req_ratelimit),
        unittest.mock.patch.object(ci_monitor.time, "sleep", side_effect=slept_rl.append),
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=1000.0),
    ):
        got_rl = ci_monitor.fetch_pr_with_retry("290", "tok", attempts=3, base_delay=2)
    check(
        got_rl == PR_RETRY and slept_rl == [7],
        "rate-limit Retry-After hint (7s) honored over exponential backoff",
        "rate-limit backoff wrong; got %r, slept %r" % (got_rl, slept_rl),
    )

    # ── (m) main(): staggered step then FAIL, each once, before terminal Blocked ───
    print(
        "\n=== (m) main(): step (poll 1) then FAIL (poll 2) emitted once each, before terminal; no heartbeat ==="
    )

    # A step delta and a per-test FAIL arrive on separate polls; each signal resets
    # the silence timer, so with a 30s-per-poll advancing clock the 120s heartbeat
    # threshold is never crossed and no in_progress line is emitted. Per-poll request
    # order under wiring (a): pulls (sha), verdict check-runs (reused by
    # poll_signals), jobs, artifacts, [zip per new artifact].
    PR_M = {"head": {"sha": "5ca1ab1e"}}
    RUNS_M = check_runs_payload(("606", None))  # drain self-fetch payload (wiring a, issue #512)
    # Under wiring (a), the verdict payload is reused by poll_signals. Combined
    # payloads include the Actions check run (run 606, run-only) so poll_signals
    # discovers the target from the same fetch used for the verdict.
    CHECK_IP_M = {
        "total_count": 2,
        "check_runs": [{"status": "in_progress", "conclusion": None}] + RUNS_M["check_runs"],
    }
    CHECK_BL_M = {
        "total_count": 2,
        "check_runs": [{"name": "build-and-test", "status": "completed", "conclusion": "failure"}]
        + RUNS_M["check_runs"],
    }
    JOBS_UNIT_FAIL_M = {
        "jobs": [
            {
                "name": "build-and-test",
                "steps": [
                    {
                        "number": 1,
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "number": 4,
                        "name": "Build and run unit tests",
                        "status": "completed",
                        "conclusion": "failure",
                    },
                ],
            }
        ]
    }
    ARTS_EMPTY_M = {"artifacts": []}
    ARTS_UNIT_M = {"artifacts": [{"id": 7007, "name": "ci-monitor-feed-unit", "expired": False}]}
    ZIP_UNIT_M = make_zip_ndjson(
        [
            '##GB4PC_TEST## {"suite":"com.gb4pc.unit.GalleryButtonTest","name":"testIcon","outcome":"FAIL","ms":4,"msg":"java.lang.AssertionError: kaboom","trace":"java.lang.AssertionError: kaboom\\n\\tat com.gb4pc.unit.GalleryButtonTest.testIcon(GalleryButtonTest.kt:33)"}',
        ]
    )

    # Poll 1 (4): step delta only (artifacts empty). Poll 2 (5): step already seen,
    # artifact appears -> zip downloaded -> FAIL emitted. Poll 3 terminal (4):
    # Blocked; artifact already seen so no zip call. Then drain_then_print (Gap E)
    # re-polls check-runs/jobs/artifacts up to DRAIN_MAX_ATTEMPTS times (3 each),
    # everything already seen on every attempt, no zip.
    # 4 + 5 + 4 + 3*3 = 22, drained.
    side_effects_m = collections.deque(
        [
            # poll 1: step delta only
            PR_M,  # pulls -> sha
            CHECK_IP_M,  # check-runs -> in_progress (reused by poll_signals)
            JOBS_UNIT_FAIL_M,  # jobs -> step "Build and run unit tests" -> failure
            ARTS_EMPTY_M,  # artifacts -> none yet
            # poll 2: FAIL detail
            PR_M,  # pulls -> sha
            CHECK_IP_M,  # check-runs -> in_progress (reused by poll_signals)
            JOBS_UNIT_FAIL_M,  # jobs -> step already seen, nothing new
            ARTS_UNIT_M,  # artifacts -> one new artifact
            ZIP_UNIT_M,  # zip (raw) -> FAIL line with trace
            # poll 3: terminal Blocked
            PR_M,  # pulls -> sha
            CHECK_BL_M,  # check-runs -> Blocked (terminal, reused by poll_signals)
            JOBS_UNIT_FAIL_M,  # jobs -> step already seen, nothing new
            ARTS_UNIT_M,  # artifacts -> artifact already seen, no zip call
            # drain_then_print (Gap E): up to DRAIN_MAX_ATTEMPTS extra signal polls
            # before the terminal line; all attempts find nothing new here.
            RUNS_M,
            JOBS_UNIT_FAIL_M,
            ARTS_UNIT_M,  # drain attempt 1
            RUNS_M,
            JOBS_UNIT_FAIL_M,
            ARTS_UNIT_M,  # drain attempt 2
            RUNS_M,
            JOBS_UNIT_FAIL_M,
            ARTS_UNIT_M,  # drain attempt 3
        ]
    )

    def fake_request_m(url, token, raw=False):
        return side_effects_m.popleft()

    clock_m = {"t": 0.0}

    def fake_time_m():
        return clock_m["t"]

    def fake_sleep_m(secs):
        clock_m["t"] += secs

    buf_m = io.StringIO()
    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_m),
        unittest.mock.patch.object(ci_monitor.time, "time", side_effect=fake_time_m),
        unittest.mock.patch.object(ci_monitor.time, "sleep", side_effect=fake_sleep_m),
        unittest.mock.patch("sys.stdout", new=buf_m),
    ):
        rc_m = ci_monitor.main(["ci_monitor.py", "--pr", "272"])

    out_m = buf_m.getvalue()
    lines_m = out_m.splitlines()
    step_line_m = 'PR#272: step "Build and run unit tests" -> failure'
    fail_line_m = (
        "PR#272: FAIL [com.gb4pc.unit.GalleryButtonTest] testIcon: java.lang.AssertionError: kaboom"
    )
    # Terminal is now attributed with the blocking check name.
    blocked_line_m = "PR#272: Blocked by: build-and-test"
    ip_line_m = "PR#272: in_progress"

    check(
        lines_m.count(step_line_m) == 1,
        "step failure line emitted exactly once",
        "step failure line count != 1; output: %r" % out_m,
    )
    check(
        lines_m.count(fail_line_m) == 1,
        "FAIL line emitted exactly once",
        "FAIL line count != 1; output: %r" % out_m,
    )
    check(
        lines_m.count(blocked_line_m) == 1,
        "Blocked attributed terminal line emitted exactly once",
        "Blocked attributed terminal line count != 1; output: %r" % out_m,
    )
    check(
        lines_m.count(ip_line_m) == 0,
        "no in_progress heartbeat (each signal resets the 120s timer)",
        "unexpected in_progress heartbeat; output: %r" % out_m,
    )
    ordered_m = (
        step_line_m in lines_m
        and fail_line_m in lines_m
        and blocked_line_m in lines_m
        and lines_m.index(step_line_m) < lines_m.index(fail_line_m) < lines_m.index(blocked_line_m)
    )
    check(
        ordered_m,
        "ordering: step delta, then FAIL, then terminal Blocked",
        "ordering wrong; lines: %r" % lines_m,
    )
    check(
        any(ln.startswith("PR#272:   ") for ln in lines_m),
        "FAIL carries an indented trace line",
        "indented trace line missing; output: %r" % out_m,
    )
    # drain flag suppressed because the check (build-and-test) is blocking/diagnosed.
    no_new_line_m = "PR#272: drain poll found no new diagnostic signals"
    check(
        no_new_line_m not in lines_m,
        "drain flag suppressed (named blocking check diagnoses the terminal)",
        "'drain poll found no new diagnostic signals' unexpectedly present; output: %r" % out_m,
    )
    check(
        len(side_effects_m) == 0,
        "all 22 mocked requests consumed (zip only on poll 2)",
        "request deque not drained; %d entries left" % len(side_effects_m),
    )
    check(rc_m == 0, "main() returned 0", "main() returned %r" % rc_m)

    # (n) main(): quiet passing PR: two adjacent heartbeats then a single Clear---
    print("\n=== (n) main(): quiet polls emit two adjacent in_progress heartbeats, then Clear ===")

    # Quiet in_progress polls produce no step/FAIL output, so the only PR#N lines
    # before the terminal are the heartbeats, which fire only after >120s of
    # silence. With the 30s advancing clock, the boundary is crossed twice across
    # enough polls; the heartbeats are therefore adjacent in the output (no other
    # PR#N line between them). The final all_passed poll emits Clear.
    PR_N = {"head": {"sha": "c0ffee11"}}
    RUNS_N = check_runs_payload(("909", None))  # drain self-fetch payload (wiring a, issue #512)
    CHECK_IP_N = {
        "total_count": 2,
        "check_runs": [{"status": "in_progress", "conclusion": None}] + RUNS_N["check_runs"],
    }
    CHECK_PASS_N = {
        "total_count": 2,
        "check_runs": [
            {"name": "build-and-test", "status": "completed", "conclusion": "success"},
        ]
        + RUNS_N["check_runs"],
    }
    JOBS_EMPTY_N = {"jobs": [{"name": "build-and-test", "steps": []}]}
    ARTS_EMPTY_N = {"artifacts": []}
    MPR_CLEAN_N = {
        "head": {"sha": "c0ffee11"},
        "merged": False,
        "state": "open",
        "mergeable_state": "clean",
    }

    # 11 quiet in_progress polls (4 requests each under wiring (a)) then a final
    # all_passed poll. The heartbeat fires only when now - last_output_ts > 120:
    # first at poll 6 (t=150, >120 since start), which resets the timer, then again
    # at poll 11 (t=300, >150+120). No quiet poll emits anything else, so the two
    # heartbeats land on adjacent output lines. The all_passed terminal poll issues 5
    # requests: the usual 4 plus the mergeable-state /pulls fetch (mpr_json).
    # 11*4 + 5 = 49 entries, drained.
    req_n = collections.deque()
    for _ in range(11):
        req_n.append(PR_N)  # pulls -> sha
        req_n.append(CHECK_IP_N)  # check-runs -> in_progress (reused by poll_signals)
        req_n.append(JOBS_EMPTY_N)  # jobs -> nothing
        req_n.append(ARTS_EMPTY_N)  # artifacts -> nothing
    # final all_passed poll
    req_n.append(PR_N)  # pulls -> sha
    req_n.append(CHECK_PASS_N)  # check-runs -> all_passed (reused by poll_signals)
    req_n.append(JOBS_EMPTY_N)  # jobs -> nothing
    req_n.append(ARTS_EMPTY_N)  # artifacts -> nothing
    req_n.append(MPR_CLEAN_N)  # pulls (mergeable_state) -> clean -> Clear

    def fake_request_n(url, token, raw=False):
        return req_n.popleft()

    clock_n = {"t": 0.0}

    def fake_time_n():
        return clock_n["t"]

    def fake_sleep_n(secs):
        clock_n["t"] += secs

    buf_n = io.StringIO()
    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_n),
        unittest.mock.patch.object(ci_monitor.time, "time", side_effect=fake_time_n),
        unittest.mock.patch.object(ci_monitor.time, "sleep", side_effect=fake_sleep_n),
        unittest.mock.patch("sys.stdout", new=buf_n),
    ):
        rc_n = ci_monitor.main(["ci_monitor.py", "--pr", "272"])

    out_n = buf_n.getvalue()
    lines_n = out_n.splitlines()
    heartbeat_line_n = "PR#272: in_progress"
    clear_lines_n = [ln for ln in lines_n if ln.startswith("PR#272: Clear")]
    summary_hdr_n = "PR#272: summary"

    hb_idx = [k for k, ln in enumerate(lines_n) if ln == heartbeat_line_n]
    check(
        len(hb_idx) == 2,
        "exactly two in_progress heartbeats emitted (got %d)" % len(hb_idx),
        "in_progress count != 2; output: %r" % out_n,
    )
    check(
        len(hb_idx) == 2 and hb_idx[1] - hb_idx[0] == 1,
        "the two heartbeats are adjacent (no PR# line between them)",
        "heartbeats not adjacent; indices %r; output: %r" % (hb_idx, out_n),
    )
    # After the summary block is added, the first PR# line is still a heartbeat
    # (the summary only prints on the final all_passed poll).
    pr_lines_n = [ln for ln in lines_n if ln.startswith("PR#272:")]
    check(
        len(pr_lines_n) > 0 and pr_lines_n[0] == heartbeat_line_n,
        "first PR# line is a heartbeat (no earlier PR# output)",
        "first PR# line is not the heartbeat; output: %r" % out_n,
    )
    check(
        len(clear_lines_n) == 1,
        "exactly one Clear terminal line emitted",
        "Clear line count != 1; output: %r" % out_n,
    )
    check(
        len(clear_lines_n) == 1 and lines_n[-1] == clear_lines_n[0],
        "Clear is the last PR# line",
        "Clear is not the last line; output: %r" % out_n,
    )
    # Summary block emitted before Clear on the passing all_passed poll.
    check(
        summary_hdr_n in lines_n
        and len(clear_lines_n) == 1
        and lines_n.index(summary_hdr_n) < lines_n.index(clear_lines_n[0]),
        "summary header appears before Clear terminal",
        "summary header missing or after Clear; output: %r" % out_n,
    )
    check(
        any("build-and-test" in ln and "success" in ln for ln in lines_n),
        "summary row for build-and-test shows success",
        "summary row for build-and-test missing; output: %r" % out_n,
    )
    check(
        len(req_n) == 0,
        "all 49 mocked requests consumed (terminal poll includes mpr fetch)",
        "request deque not drained; %d entries left" % len(req_n),
    )
    check(rc_n == 0, "main() returned 0", "main() returned %r" % rc_n)

    # ── (o) Gap D: real subprocess advertises its PID and dies on SIGTERM ──────────
    print("\n=== (o) Gap D: ci_monitor.py prints its real PID and stops on SIGTERM ===")

    # The issue's Gap D viability check ("the script's $$ equals the real, killable
    # PID, and kill -TERM is delivered") was carried as a manual step. Automate it:
    # spawn the real script as a child process, read the advisory PID line, send
    # SIGTERM to that exact PID, and confirm the process exits via the signal. A
    # bogus token + the unreachable real API_BASE means the loop never gets past the
    # SHA fetch, so it stays alive (sleeping) until we signal it; no network needed.
    _MONITOR_PATH = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "ci_monitor", "ci_monitor.py"
    )
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
    check(
        _m is not None,
        "subprocess emits a 'monitor PID <n>' advisory line at startup",
        "no PID advisory line; got %r" % advisory,
    )
    check(
        "SIGTERM" in advisory,
        "advisory names SIGTERM as the stop signal",
        "advisory does not mention SIGTERM; got %r" % advisory,
    )

    printed_pid = int(_m.group(1)) if _m else -1
    check(
        printed_pid == _proc.pid,
        "printed PID equals the real, killable subprocess PID",
        "printed PID %r != real PID %r" % (printed_pid, _proc.pid),
    )

    # Send SIGTERM to the advertised PID and confirm the process actually stops.
    try:
        os.kill(_proc.pid, signal.SIGTERM)
    except OSError as e:  # pragma: no cover - only if the process already vanished
        _fail("could not deliver SIGTERM to PID %d: %s" % (_proc.pid, e))

    try:
        _rc = _proc.wait(timeout=10)
        check(
            _rc != 0,
            "SIGTERM to the advertised PID stops the monitor (non-zero exit)",
            "process exited 0 unexpectedly (rc=%r)" % _rc,
        )
        # On a default-disposition SIGTERM, Popen reports the negative signal number.
        check(
            _rc == -signal.SIGTERM,
            "process terminated by SIGTERM (rc == -SIGTERM)",
            "expected rc == %d; got %r" % (-signal.SIGTERM, _rc),
        )
    except subprocess.TimeoutExpired:
        _proc.kill()
        _proc.wait(timeout=10)
        _fail("subprocess did not exit within 10s of SIGTERM")

    # ── (p) #258 fidelity: a real-shaped artifact zip drives step + FAIL ───────────
    print(
        "\n=== (p) #258 fidelity: real-shaped ci-monitor-feed-unit artifact -> step + FAIL via main() ==="
    )

    # Groups (i)/(m) prove the signal logic with a hand-rolled zip whose entry is
    # named 'testresults.ndjson'. This group closes the live-verification gap from
    # #258 by reproducing the artifact exactly as build.yml emits it:
    #   - `... | tee >(grep '^##GB4PC_TEST##' > results/unit.ndjson)` writes only
    #     marker-prefixed lines into results/unit.ndjson.
    #   - `upload-artifact` with `path: results/unit.ndjson` zips it under the
    #     basename entry 'unit.ndjson' inside an artifact named 'ci-monitor-feed-unit'.
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
    RUNS_P = check_runs_payload(("4242", None))  # drain self-fetch payload (wiring a, issue #512)
    CHECK_IP_P = {
        "total_count": 2,
        "check_runs": [{"status": "in_progress", "conclusion": None}] + RUNS_P["check_runs"],
    }
    CHECK_BL_P = {
        "total_count": 2,
        "check_runs": [
            {"name": "build-and-test", "status": "completed", "conclusion": "failure"},
        ]
        + RUNS_P["check_runs"],
    }
    JOBS_UNIT_FAIL_P = {
        "jobs": [
            {
                "name": "build-and-test",
                "steps": [
                    {
                        "number": 1,
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "number": 4,
                        "name": "Build and run unit tests",
                        "status": "completed",
                        "conclusion": "failure",
                    },
                ],
            }
        ]
    }
    # The artifact name mirrors build.yml's 'ci-monitor-feed-unit'; parse_new_artifacts
    # keys on the 'ci-monitor-feed-' prefix and id, so the real name is exercised.
    ARTS_REAL_UNIT_P = {"artifacts": [{"id": 4243, "name": "ci-monitor-feed-unit", "expired": False}]}

    # Poll 1 (4): step delta, artifact not yet present. Poll 2 (5): step seen,
    # artifact appears -> real-shaped zip downloaded -> FAIL emitted. Poll 3 (4):
    # terminal Blocked, artifact already seen so no zip call. Then drain_then_print
    # (Gap E) re-polls check-runs/jobs/artifacts up to DRAIN_MAX_ATTEMPTS times (3 each),
    # already seen on every attempt, no zip.
    # 4 + 5 + 4 + 3*3 = 22.
    side_effects_p = collections.deque(
        [
            PR_P,
            CHECK_IP_P,
            JOBS_UNIT_FAIL_P,
            {"artifacts": []},
            PR_P,
            CHECK_IP_P,
            JOBS_UNIT_FAIL_P,
            ARTS_REAL_UNIT_P,
            REAL_UNIT_ZIP,
            PR_P,
            CHECK_BL_P,
            JOBS_UNIT_FAIL_P,
            ARTS_REAL_UNIT_P,
            # drain_then_print (Gap E): up to DRAIN_MAX_ATTEMPTS extra signal polls
            # before the terminal line; all attempts find nothing new here.
            RUNS_P,
            JOBS_UNIT_FAIL_P,
            ARTS_REAL_UNIT_P,  # drain attempt 1
            RUNS_P,
            JOBS_UNIT_FAIL_P,
            ARTS_REAL_UNIT_P,  # drain attempt 2
            RUNS_P,
            JOBS_UNIT_FAIL_P,
            ARTS_REAL_UNIT_P,  # drain attempt 3
        ]
    )

    def fake_request_p(url, token, raw=False):
        return side_effects_p.popleft()

    buf_p = io.StringIO()
    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_p),
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=2000.0),
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None),
        unittest.mock.patch("sys.stdout", new=buf_p),
    ):
        rc_p = ci_monitor.main(["ci_monitor.py", "--pr", "258"])

    out_p = buf_p.getvalue()
    lines_p = out_p.splitlines()
    step_line_p = 'PR#258: step "Build and run unit tests" -> failure'
    fail_line_p = "PR#258: FAIL [com.gb4pc.unit.GalleryButtonTest] renders_icon: java.lang.AssertionError: icon not tinted"
    # Terminal is now attributed with the blocking check name.
    blocked_line_p = "PR#258: Blocked by: build-and-test"

    check(
        lines_p.count(step_line_p) == 1,
        "step failure line emitted exactly once from real-shaped pipeline",
        "step line count != 1; output: %r" % out_p,
    )
    check(
        lines_p.count(fail_line_p) == 1,
        "FAIL line parsed once from the 'unit.ndjson' artifact entry",
        "FAIL line count != 1; output: %r" % out_p,
    )
    check(
        any(ln.startswith("PR#258:   ") for ln in lines_p),
        "FAIL carries an indented trace line from the real artifact",
        "indented trace missing; output: %r" % out_p,
    )
    check(
        step_line_p in lines_p
        and fail_line_p in lines_p
        and blocked_line_p in lines_p
        and lines_p.index(step_line_p) < lines_p.index(fail_line_p) < lines_p.index(blocked_line_p),
        "ordering: step, then FAIL, then terminal Blocked--both signals before the job concludes",
        "ordering wrong; lines: %r" % lines_p,
    )
    # drain flag suppressed because the check (build-and-test) is blocking/diagnosed.
    no_new_line_p = "PR#258: drain poll found no new diagnostic signals"
    check(
        no_new_line_p not in lines_p,
        "drain flag suppressed (named blocking check diagnoses the terminal)",
        "'drain poll found no new diagnostic signals' unexpectedly present; output: %r" % out_p,
    )
    check(
        len(side_effects_p) == 0,
        "all 22 mocked requests consumed (zip only on poll 2)",
        "request deque not drained; %d entries left" % len(side_effects_p),
    )
    check(rc_p == 0, "main() returned 0", "main() returned %r" % rc_p)

    # ── (q) #259 real clock: heartbeat honors real wall-clock silence window ───────
    print(
        "\n=== (q) #259 real clock: in_progress fires only after real >SILENCE_SECONDS, output resets it ==="
    )

    # Groups (j)/(n) prove the >SILENCE_SECONDS logic with a *fabricated* clock,
    # which cannot show the heartbeat is wired to real wall time. This group closes
    # the #259 live-verification gap by exercising the genuine path: the real
    # time.time() (deliberately NOT patched) gates the heartbeat, with
    # SILENCE_SECONDS shrunk to a tiny window so the run is fast.
    #
    # To stay deterministic under CI timing jitter, every quiet poll sleeps a real
    # interval comfortably larger than the window (SLEEP_Q >> WINDOW_Q). So each
    # quiet poll *always* crosses the silence window and emits a heartbeat; there is
    # no "is 0.24s > 0.30s?" boundary race. The reset property is then proven by a
    # poll that emits a step delta: emit_block() sets last_output_ts to real now, and
    # the in_progress gate re-reads now immediately after, so now - last_output_ts is
    # ~0 (< window) and that poll emits its step but suppresses the heartbeat. A
    # heartbeat on a quiet poll, none on the step poll, and a heartbeat again after
    # proves real-time suppression and that an emitted line resets the real timer.
    WINDOW_Q = 0.05  # silence window (s); real elapsed time gates the heartbeat
    SLEEP_Q = 0.25  # per-poll real sleep, comfortably > WINDOW_Q so each quiet poll crosses it

    PR_Q = {"head": {"sha": "ab1eca11"}}
    RUNS_Q = check_runs_payload(("31337", None))  # drain self-fetch payload (wiring a, issue #512)
    CHECK_IP_Q = {
        "total_count": 2,
        "check_runs": [{"status": "in_progress", "conclusion": None}] + RUNS_Q["check_runs"],
    }
    CHECK_BL_Q = {
        "total_count": 2,
        "check_runs": [
            {"name": "build-and-test", "status": "completed", "conclusion": "failure"},
        ]
        + RUNS_Q["check_runs"],
    }
    JOBS_EMPTY_Q = {"jobs": [{"name": "build-and-test", "steps": []}]}
    JOBS_STEP_Q = {
        "jobs": [
            {
                "name": "build-and-test",
                "steps": [
                    {
                        "number": 4,
                        "name": "Build and run unit tests",
                        "status": "completed",
                        "conclusion": "success",
                    },
                ],
            }
        ]
    }
    ARTS_EMPTY_Q = {"artifacts": []}

    # 5 polls (each under wiring (a): pulls, verdict check-runs (reused by
    # poll_signals), jobs, artifacts; then a real sleep).
    # main() reads last_output_ts = time.time() at startup, BEFORE poll 1, and each
    # poll's silence check runs before that poll's own sleep, so a heartbeat needs a
    # prior quiet sleep to have elapsed:
    #   poll 1: quiet; now ~= startup (no sleep yet), diff ~0 -> NO heartbeat
    #   poll 2: quiet; one SLEEP_Q elapsed (> window)         -> heartbeat #1, resets timer
    #   poll 3: emits a step delta -> resets the real timer    -> NO heartbeat this poll
    #   poll 4: quiet; one SLEEP_Q elapsed since the step      -> heartbeat #2, resets timer
    #   poll 5: Blocked terminal, then drain_then_print (Gap E) re-polls
    #           check-runs/jobs/artifacts up to DRAIN_MAX_ATTEMPTS times (everything
    #           already seen on every attempt) before printing the terminal line.
    JOBS_SCHEDULE_Q = [JOBS_EMPTY_Q, JOBS_EMPTY_Q, JOBS_STEP_Q, JOBS_EMPTY_Q, JOBS_EMPTY_Q]
    CHECK_SCHEDULE_Q = [CHECK_IP_Q, CHECK_IP_Q, CHECK_IP_Q, CHECK_IP_Q, CHECK_BL_Q]

    req_q = collections.deque()
    for n in range(5):
        req_q.append(PR_Q)
        req_q.append(CHECK_SCHEDULE_Q[n])
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
    # rebinds time.sleep; calling time.sleep here would re-enter the mock and recurse.
    _REAL_SLEEP = time.sleep

    def real_sleep_q(_secs):
        # Ignore the script's 30s cadence; sleep a small real interval so the real
        # time.time() advances past WINDOW_Q and the wall-clock gate is exercised.
        _REAL_SLEEP(SLEEP_Q)

    buf_q = io.StringIO()
    with (
        unittest.mock.patch.object(ci_monitor, "SILENCE_SECONDS", WINDOW_Q),
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_q),
        unittest.mock.patch.object(ci_monitor.time, "sleep", side_effect=real_sleep_q),
        unittest.mock.patch("sys.stdout", new=buf_q),
    ):
        # time.time() is intentionally NOT patched: the real clock gates the heartbeat.
        rc_q = ci_monitor.main(["ci_monitor.py", "--pr", "259"])

    out_q = buf_q.getvalue()
    lines_q = out_q.splitlines()
    ip_line_q = "PR#259: in_progress"
    step_line_q = 'PR#259: step "Build and run unit tests" -> success'
    # Terminal is now attributed with the blocking check name.
    blocked_line_q = "PR#259: Blocked by: build-and-test"

    check(
        lines_q.count(ip_line_q) == 2,
        "exactly two real-clock heartbeats emitted (got %d)" % lines_q.count(ip_line_q),
        "in_progress count != 2 under real clock; output: %r" % out_q,
    )
    check(
        lines_q.count(step_line_q) == 1,
        "the step delta is emitted exactly once",
        "step line count != 1; output: %r" % out_q,
    )
    # The step poll emits its delta but no heartbeat (the emission reset the real
    # timer to ~now, so the same poll's in_progress gate sees ~0 < window). Exactly
    # two heartbeats with the step strictly between them proves the real-time reset:
    # without the reset, the step poll would also emit a heartbeat (3 total).
    ip_idx_q = [k for k, ln in enumerate(lines_q) if ln == ip_line_q]
    step_idx_q = lines_q.index(step_line_q) if step_line_q in lines_q else -1
    check(
        len(ip_idx_q) == 2 and step_idx_q != -1 and ip_idx_q[0] < step_idx_q < ip_idx_q[1],
        "an emitted step line resets the real-time silence timer (no heartbeat on the step poll)",
        "step did not reset the real-time timer; lines: %r" % lines_q,
    )
    # drain flag suppressed because the check (build-and-test) is blocking/diagnosed.
    no_new_line_q = "PR#259: drain poll found no new diagnostic signals"
    check(
        no_new_line_q not in lines_q,
        "drain flag suppressed (named blocking check diagnoses the terminal)",
        "'drain poll found no new diagnostic signals' unexpectedly present; output: %r" % out_q,
    )
    check(
        lines_q.count(blocked_line_q) == 1 and lines_q[-1] == blocked_line_q,
        "Blocked attributed terminal emitted once as the final line",
        "terminal Blocked wrong; output: %r" % out_q,
    )
    check(
        len(req_q) == 0,
        "all real-clock poll requests consumed",
        "request deque not drained; %d entries left" % len(req_q),
    )
    check(rc_q == 0, "main() returned 0", "main() returned %r" % rc_q)

    # ── (r) #260 outcome filters: parse_fails with explicit outcome_filters ────────
    print(
        "\n=== (r) #260 outcome filters: parse_fails obeys outcome_filters for FAIL/PASS/SKIP ==="
    )

    # These markers use the new default ##TEST## marker (issue #500): group (r)
    # exercises the outcome_filters mechanism, so the calls below rely on the
    # DEFAULT_TEST_MARKER_REGEX and need no explicit test_marker_regex argument.
    FILTER_NDJSON = [
        '##TEST## {"suite":"com.gb4pc.unit.FooTest","name":"test_fail","outcome":"FAIL","ms":1,"msg":"boom","trace":""}',
        '##TEST## {"suite":"com.gb4pc.unit.FooTest","name":"test_pass","outcome":"PASS","ms":2,"msg":"","trace":""}',
        '##TEST## {"suite":"com.gb4pc.unit.FooTest","name":"test_skip","outcome":"SKIP","ms":0,"msg":"","trace":""}',
        '##TEST## {"suite":"com.gb4pc.unit.BarTest","name":"test_fail_bar","outcome":"FAIL","ms":3,"msg":"kaboom","trace":""}',
        '##TEST## {"suite":"com.gb4pc.unit.BarTest","name":"test_pass_bar","outcome":"PASS","ms":4,"msg":"","trace":""}',
        '##TEST## {"suite":"com.gb4pc.unit.BarTest","name":"test_skip_bar","outcome":"SKIP","ms":0,"msg":"","trace":""}',
    ]

    # Default behavior: all FAIL, all SKIP, no PASS
    out_r_default = ci_monitor.parse_fails(FILTER_NDJSON, set())
    out_r_default_str = "\n".join(out_r_default)
    check(
        "FAIL [com.gb4pc.unit.FooTest] test_fail:" in out_r_default_str,
        "default: FAIL marker emitted",
        "default: FAIL marker missing; output: %r" % out_r_default,
    )
    check(
        "FAIL [com.gb4pc.unit.BarTest] test_fail_bar:" in out_r_default_str,
        "default: FAIL marker for BarTest emitted",
        "default: BarTest FAIL missing; output: %r" % out_r_default,
    )
    check(
        "SKIP [com.gb4pc.unit.FooTest] test_skip:" in out_r_default_str,
        "default: SKIP marker emitted",
        "default: SKIP marker missing; output: %r" % out_r_default,
    )
    check(
        "SKIP [com.gb4pc.unit.BarTest] test_skip_bar:" in out_r_default_str,
        "default: SKIP marker for BarTest emitted",
        "default: BarTest SKIP missing; output: %r" % out_r_default,
    )
    check(
        "PASS" not in out_r_default_str,
        "default: no PASS markers emitted",
        "default: unexpected PASS in output: %r" % out_r_default,
    )

    # --no-include-fail: suppress all FAIL
    out_r_nofail = ci_monitor.parse_fails(
        FILTER_NDJSON,
        set(),
        outcome_filters={"FAIL": (False, None), "SKIP": (True, None), "PASS": (False, None)},
    )
    out_r_nofail_str = "\n".join(out_r_nofail)
    check(
        "FAIL" not in out_r_nofail_str,
        "--no-include-fail: no FAIL emitted",
        "--no-include-fail: FAIL in output: %r" % out_r_nofail,
    )
    check(
        "SKIP [com.gb4pc.unit.FooTest] test_skip:" in out_r_nofail_str,
        "--no-include-fail: SKIP still emitted",
        "--no-include-fail: SKIP missing; output: %r" % out_r_nofail,
    )

    # --no-include-skip: suppress all SKIP
    out_r_noskip = ci_monitor.parse_fails(
        FILTER_NDJSON,
        set(),
        outcome_filters={"FAIL": (True, None), "SKIP": (False, None), "PASS": (False, None)},
    )
    out_r_noskip_str = "\n".join(out_r_noskip)
    check(
        "SKIP" not in out_r_noskip_str,
        "--no-include-skip: no SKIP emitted",
        "--no-include-skip: SKIP in output: %r" % out_r_noskip,
    )
    check(
        "FAIL [com.gb4pc.unit.FooTest] test_fail:" in out_r_noskip_str,
        "--no-include-skip: FAIL still emitted",
        "--no-include-skip: FAIL missing; output: %r" % out_r_noskip,
    )

    # --include-pass '' (no pattern): all PASS markers emitted
    out_r_allpass = ci_monitor.parse_fails(
        FILTER_NDJSON,
        set(),
        outcome_filters={"FAIL": (True, None), "SKIP": (True, None), "PASS": (True, None)},
    )
    out_r_allpass_str = "\n".join(out_r_allpass)
    check(
        "PASS [com.gb4pc.unit.FooTest] test_pass:" in out_r_allpass_str,
        "--include-pass: PASS marker emitted",
        "--include-pass: PASS missing; output: %r" % out_r_allpass,
    )
    check(
        "PASS [com.gb4pc.unit.BarTest] test_pass_bar:" in out_r_allpass_str,
        "--include-pass: PASS marker for BarTest emitted",
        "--include-pass: BarTest PASS missing; output: %r" % out_r_allpass,
    )

    # --include-pass with a pattern: only matching passes emitted
    out_r_patpass = ci_monitor.parse_fails(
        FILTER_NDJSON,
        set(),
        outcome_filters={
            "FAIL": (False, None),
            "SKIP": (False, None),
            "PASS": (True, "test_pass$"),
        },
    )
    out_r_patpass_str = "\n".join(out_r_patpass)
    check(
        "PASS [com.gb4pc.unit.FooTest] test_pass:" in out_r_patpass_str,
        "--include-pass with pattern: matching PASS emitted",
        "--include-pass pattern: matching PASS missing; output: %r" % out_r_patpass,
    )
    check(
        "PASS [com.gb4pc.unit.BarTest] test_pass_bar:" not in out_r_patpass_str,
        "--include-pass with pattern: non-matching PASS suppressed",
        "--include-pass pattern: non-matching PASS leaked; output: %r" % out_r_patpass,
    )

    # --include-fail with a pattern: only matching FAIL emitted
    out_r_patfail = ci_monitor.parse_fails(
        FILTER_NDJSON,
        set(),
        outcome_filters={
            "FAIL": (True, "test_fail$"),
            "SKIP": (False, None),
            "PASS": (False, None),
        },
    )
    out_r_patfail_str = "\n".join(out_r_patfail)
    check(
        "FAIL [com.gb4pc.unit.FooTest] test_fail:" in out_r_patfail_str,
        "--include-fail with pattern: matching FAIL emitted",
        "--include-fail pattern: matching FAIL missing; output: %r" % out_r_patfail,
    )
    check(
        "FAIL [com.gb4pc.unit.BarTest] test_fail_bar:" not in out_r_patfail_str,
        "--include-fail with pattern: non-matching FAIL suppressed",
        "--include-fail pattern: non-matching FAIL leaked; output: %r" % out_r_patfail,
    )

    # --include-skip with a pattern: only matching SKIP emitted
    out_r_patskip = ci_monitor.parse_fails(
        FILTER_NDJSON,
        set(),
        outcome_filters={
            "FAIL": (False, None),
            "SKIP": (True, "test_skip$"),
            "PASS": (False, None),
        },
    )
    out_r_patskip_str = "\n".join(out_r_patskip)
    check(
        "SKIP [com.gb4pc.unit.FooTest] test_skip:" in out_r_patskip_str,
        "--include-skip with pattern: matching SKIP emitted",
        "--include-skip pattern: matching SKIP missing; output: %r" % out_r_patskip,
    )
    check(
        "SKIP [com.gb4pc.unit.BarTest] test_skip_bar:" not in out_r_patskip_str,
        "--include-skip with pattern: non-matching SKIP suppressed",
        "--include-skip pattern: non-matching SKIP leaked; output: %r" % out_r_patskip,
    )

    # SKIP stays labeled SKIP, never relabeled as PASS (even with --include-pass '')
    out_r_labelcheck = ci_monitor.parse_fails(
        FILTER_NDJSON,
        set(),
        outcome_filters={"FAIL": (False, None), "SKIP": (True, None), "PASS": (True, None)},
    )
    out_r_labelcheck_str = "\n".join(out_r_labelcheck)
    check(
        all(
            not ln.startswith("PASS")
            for ln in out_r_labelcheck_str.splitlines()
            if "test_skip" in ln
        ),
        "SKIP marker is never relabeled as PASS",
        "SKIP was relabeled as PASS; output: %r" % out_r_labelcheck,
    )
    check(
        any(ln.startswith("SKIP") for ln in out_r_labelcheck_str.splitlines()),
        "SKIP outcome retains SKIP label in output",
        "SKIP label missing; output: %r" % out_r_labelcheck,
    )

    # ── (s) #260 CLI flags: _parse_outcome_filters and main() respect filter flags ─
    print(
        "\n=== (s) #260 CLI flags: _parse_outcome_filters and main() pass filter flags through ==="
    )

    # _parse_outcome_filters: defaults (no flags)
    import argparse as _argparse  # noqa: E402

    def _make_args(**kw):
        """Build a namespace with the six flag attributes, defaulting to 'not supplied'."""
        defaults = {
            "include_fail": None,
            "no_include_fail": False,
            "include_skip": None,
            "no_include_skip": False,
            "include_pass": None,
            "no_include_pass": False,
        }
        defaults.update(kw)
        return _argparse.Namespace(**defaults)

    # Defaults: all FAIL, all SKIP, no PASS
    filters_default = ci_monitor._parse_outcome_filters(_make_args())
    check(
        filters_default["FAIL"] == (True, None),
        "_parse_outcome_filters default FAIL=(True,None)",
        "_parse_outcome_filters FAIL default wrong; got %r" % (filters_default["FAIL"],),
    )
    check(
        filters_default["SKIP"] == (True, None),
        "_parse_outcome_filters default SKIP=(True,None)",
        "_parse_outcome_filters SKIP default wrong; got %r" % (filters_default["SKIP"],),
    )
    check(
        filters_default["PASS"] == (False, None),
        "_parse_outcome_filters default PASS=(False,None)",
        "_parse_outcome_filters PASS default wrong; got %r" % (filters_default["PASS"],),
    )

    # --no-include-fail
    filters_nofail = ci_monitor._parse_outcome_filters(_make_args(no_include_fail=True))
    check(
        filters_nofail["FAIL"] == (False, None),
        "_parse_outcome_filters --no-include-fail -> FAIL=(False,None)",
        "_parse_outcome_filters --no-include-fail wrong; got %r" % (filters_nofail["FAIL"],),
    )

    # --no-include-skip
    filters_noskip = ci_monitor._parse_outcome_filters(_make_args(no_include_skip=True))
    check(
        filters_noskip["SKIP"] == (False, None),
        "_parse_outcome_filters --no-include-skip -> SKIP=(False,None)",
        "_parse_outcome_filters --no-include-skip wrong; got %r" % (filters_noskip["SKIP"],),
    )

    # --no-include-pass (explicit form of default)
    filters_nopass = ci_monitor._parse_outcome_filters(_make_args(no_include_pass=True))
    check(
        filters_nopass["PASS"] == (False, None),
        "_parse_outcome_filters --no-include-pass -> PASS=(False,None)",
        "_parse_outcome_filters --no-include-pass wrong; got %r" % (filters_nopass["PASS"],),
    )

    # --include-pass '' (const, no pattern -> match all)
    filters_allpass = ci_monitor._parse_outcome_filters(_make_args(include_pass=""))
    check(
        filters_allpass["PASS"] == (True, None),
        "_parse_outcome_filters --include-pass '' -> PASS=(True,None)",
        "_parse_outcome_filters --include-pass '' wrong; got %r" % (filters_allpass["PASS"],),
    )

    # --include-pass 'MyTest' (pattern)
    filters_patpass = ci_monitor._parse_outcome_filters(_make_args(include_pass="MyTest"))
    check(
        filters_patpass["PASS"] == (True, "MyTest"),
        "_parse_outcome_filters --include-pass 'MyTest' -> PASS=(True,'MyTest')",
        "_parse_outcome_filters --include-pass pattern wrong; got %r" % (filters_patpass["PASS"],),
    )

    # --include-fail '' (supplied with no pattern -> match all)
    filters_allfail = ci_monitor._parse_outcome_filters(_make_args(include_fail=""))
    check(
        filters_allfail["FAIL"] == (True, None),
        "_parse_outcome_filters --include-fail '' -> FAIL=(True,None)",
        "_parse_outcome_filters --include-fail '' wrong; got %r" % (filters_allfail["FAIL"],),
    )

    # --include-fail 'Foo' (pattern)
    filters_patfail = ci_monitor._parse_outcome_filters(_make_args(include_fail="Foo"))
    check(
        filters_patfail["FAIL"] == (True, "Foo"),
        "_parse_outcome_filters --include-fail 'Foo' -> FAIL=(True,'Foo')",
        "_parse_outcome_filters --include-fail pattern wrong; got %r" % (filters_patfail["FAIL"],),
    )

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
    RUNS_S = check_runs_payload(("1111", None))  # drain self-fetch payload (wiring a, issue #512)
    CHECK_IP_S = {
        "total_count": 2,
        "check_runs": [{"status": "in_progress", "conclusion": None}] + RUNS_S["check_runs"],
    }
    CHECK_BL_S = {
        "total_count": 2,
        "check_runs": [
            {"name": "build-and-test", "status": "completed", "conclusion": "failure"},
        ]
        + RUNS_S["check_runs"],
    }
    JOBS_EMPTY_S = {"jobs": [{"name": "build-and-test", "steps": []}]}
    ARTS_MIX_S = {"artifacts": [{"id": 2222, "name": "ci-monitor-feed-mix", "expired": False}]}

    # Poll 1 (5): artifact available, zip downloaded; poll 2 (4): terminal Blocked,
    # then drain_then_print (Gap E) re-polls check-runs/jobs/artifacts up to
    # DRAIN_MAX_ATTEMPTS times (3 each), artifact already seen on every attempt so
    # no zip call. 5 + 4 + 3*3 = 18.
    side_effects_s = collections.deque(
        [
            PR_S,
            CHECK_IP_S,
            JOBS_EMPTY_S,
            ARTS_MIX_S,
            ZIP_MIXED,
            PR_S,
            CHECK_BL_S,
            JOBS_EMPTY_S,
            ARTS_MIX_S,
            RUNS_S,
            JOBS_EMPTY_S,
            ARTS_MIX_S,  # drain attempt 1
            RUNS_S,
            JOBS_EMPTY_S,
            ARTS_MIX_S,  # drain attempt 2
            RUNS_S,
            JOBS_EMPTY_S,
            ARTS_MIX_S,  # drain attempt 3
        ]
    )

    def fake_request_s(url, token, raw=False):
        return side_effects_s.popleft()

    buf_s = io.StringIO()
    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_s),
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=3000.0),
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None),
        unittest.mock.patch("sys.stdout", new=buf_s),
    ):
        rc_s = ci_monitor.main(["ci_monitor.py", "--pr", "260", "--include-pass", ""])

    out_s = buf_s.getvalue()
    lines_s = out_s.splitlines()
    check(
        "PR#260: FAIL [com.gb4pc.unit.MixTest] test_fail: oops" in lines_s,
        "main() --include-pass '': FAIL line emitted",
        "main() --include-pass '': FAIL missing; output: %r" % out_s,
    )
    check(
        any("PR#260: PASS [com.gb4pc.unit.MixTest] test_pass:" in ln for ln in lines_s),
        "main() --include-pass '': PASS line emitted",
        "main() --include-pass '': PASS missing; output: %r" % out_s,
    )
    check(
        any("PR#260: SKIP [com.gb4pc.unit.MixTest] test_skip:" in ln for ln in lines_s),
        "main() --include-pass '': SKIP line emitted and stays labeled SKIP",
        "main() --include-pass '': SKIP missing or mislabeled; output: %r" % out_s,
    )
    no_new_line_s = "PR#260: drain poll found no new diagnostic signals"
    check(
        no_new_line_s not in lines_s,
        "main() --include-pass '': drain flag suppressed (named blocking check diagnoses the terminal)",
        "main() --include-pass '': 'drain poll found no new diagnostic signals' unexpectedly present; output: %r"
        % out_s,
    )
    check(
        len(side_effects_s) == 0,
        "main() --include-pass '': all 18 mocked requests consumed",
        "request deque not drained; %d entries left" % len(side_effects_s),
    )
    check(rc_s == 0, "main() --include-pass '' returned 0", "main() returned %r" % rc_s)

    # main() with no flags: FAIL+SKIP emitted, no PASS
    side_effects_s2 = collections.deque(
        [
            PR_S,
            CHECK_IP_S,
            JOBS_EMPTY_S,
            ARTS_MIX_S,
            ZIP_MIXED,
            PR_S,
            CHECK_BL_S,
            JOBS_EMPTY_S,
            ARTS_MIX_S,
            RUNS_S,
            JOBS_EMPTY_S,
            ARTS_MIX_S,  # drain attempt 1
            RUNS_S,
            JOBS_EMPTY_S,
            ARTS_MIX_S,  # drain attempt 2
            RUNS_S,
            JOBS_EMPTY_S,
            ARTS_MIX_S,  # drain attempt 3
        ]
    )

    def fake_request_s2(url, token, raw=False):
        return side_effects_s2.popleft()

    buf_s2 = io.StringIO()
    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_s2),
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=3000.0),
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None),
        unittest.mock.patch("sys.stdout", new=buf_s2),
    ):
        ci_monitor.main(["ci_monitor.py", "--pr", "260"])

    out_s2 = buf_s2.getvalue()
    lines_s2 = out_s2.splitlines()
    check(
        any("FAIL [com.gb4pc.unit.MixTest] test_fail:" in ln for ln in lines_s2),
        "main() no flags: FAIL emitted",
        "main() no flags: FAIL missing; output: %r" % out_s2,
    )
    check(
        any("SKIP [com.gb4pc.unit.MixTest] test_skip:" in ln for ln in lines_s2),
        "main() no flags: SKIP emitted",
        "main() no flags: SKIP missing; output: %r" % out_s2,
    )
    check(
        not any("PASS" in ln for ln in lines_s2 if not ln.startswith("monitor PID")),
        "main() no flags: no PASS emitted",
        "main() no flags: unexpected PASS; output: %r" % out_s2,
    )
    check(
        len(side_effects_s2) == 0,
        "main() no flags: all 18 mocked requests consumed",
        "request deque not drained; %d entries left" % len(side_effects_s2),
    )

    # main() with --no-include-fail: only SKIP emitted (no FAIL, no PASS)
    # The trailing entries cover drain_then_print's (Gap E) up to DRAIN_MAX_ATTEMPTS
    # extra signal polls before the terminal line; the artifact is already seen on
    # every attempt so no zip call.
    side_effects_s3 = collections.deque(
        [
            PR_S,
            CHECK_IP_S,
            JOBS_EMPTY_S,
            ARTS_MIX_S,
            ZIP_MIXED,
            PR_S,
            CHECK_BL_S,
            JOBS_EMPTY_S,
            ARTS_MIX_S,
            RUNS_S,
            JOBS_EMPTY_S,
            ARTS_MIX_S,  # drain attempt 1
            RUNS_S,
            JOBS_EMPTY_S,
            ARTS_MIX_S,  # drain attempt 2
            RUNS_S,
            JOBS_EMPTY_S,
            ARTS_MIX_S,  # drain attempt 3
        ]
    )

    def fake_request_s3(url, token, raw=False):
        return side_effects_s3.popleft()

    buf_s3 = io.StringIO()
    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_s3),
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=3000.0),
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None),
        unittest.mock.patch("sys.stdout", new=buf_s3),
    ):
        ci_monitor.main(["ci_monitor.py", "--pr", "260", "--no-include-fail"])

    out_s3 = buf_s3.getvalue()
    lines_s3 = out_s3.splitlines()
    check(
        not any("FAIL" in ln for ln in lines_s3 if "PR#260:" in ln),
        "main() --no-include-fail: no FAIL emitted",
        "main() --no-include-fail: FAIL leaked; output: %r" % out_s3,
    )
    check(
        any("SKIP [com.gb4pc.unit.MixTest] test_skip:" in ln for ln in lines_s3),
        "main() --no-include-fail: SKIP still emitted",
        "main() --no-include-fail: SKIP missing; output: %r" % out_s3,
    )

    # main() with --no-include-skip: only FAIL emitted (no SKIP, no PASS)
    # The trailing entries cover drain_then_print's (Gap E) up to DRAIN_MAX_ATTEMPTS
    # extra signal polls before the terminal line; the artifact is already seen on
    # every attempt so no zip call.
    side_effects_s4 = collections.deque(
        [
            PR_S,
            CHECK_IP_S,
            JOBS_EMPTY_S,
            ARTS_MIX_S,
            ZIP_MIXED,
            PR_S,
            CHECK_BL_S,
            JOBS_EMPTY_S,
            ARTS_MIX_S,
            RUNS_S,
            JOBS_EMPTY_S,
            ARTS_MIX_S,  # drain attempt 1
            RUNS_S,
            JOBS_EMPTY_S,
            ARTS_MIX_S,  # drain attempt 2
            RUNS_S,
            JOBS_EMPTY_S,
            ARTS_MIX_S,  # drain attempt 3
        ]
    )

    def fake_request_s4(url, token, raw=False):
        return side_effects_s4.popleft()

    buf_s4 = io.StringIO()
    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_s4),
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=3000.0),
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None),
        unittest.mock.patch("sys.stdout", new=buf_s4),
    ):
        ci_monitor.main(["ci_monitor.py", "--pr", "260", "--no-include-skip"])

    out_s4 = buf_s4.getvalue()
    lines_s4 = out_s4.splitlines()
    check(
        not any("SKIP" in ln for ln in lines_s4 if "PR#260:" in ln),
        "main() --no-include-skip: no SKIP emitted",
        "main() --no-include-skip: SKIP leaked; output: %r" % out_s4,
    )
    check(
        any("FAIL [com.gb4pc.unit.MixTest] test_fail:" in ln for ln in lines_s4),
        "main() --no-include-skip: FAIL still emitted",
        "main() --no-include-skip: FAIL missing; output: %r" % out_s4,
    )

    # ── (t) Gap E (#402): drain_then_print surfaces a step/FAIL that lags behind
    #       the Blocked terminal by exactly one poll ─────────────────────────────
    print("\n=== (t) Gap E (#402): drain poll surfaces step+FAIL that lag behind Blocked ===")

    # Reproduces Run B/E/G from issue #402: check-runs flips straight from
    # in_progress to failure (Blocked) on poll 1, while /actions/runs/{id}/jobs
    # still shows the failing "Gate on test failures" step as not-yet-completed
    # and the ci-monitor-feed-* artifact is not yet listed. Without the drain, poll 1
    # would emit Blocked with zero step/FAIL lines. With drain_then_print, the
    # same poll's terminal line is followed by one extra signal poll
    # (DRAIN_DELAY_SECONDS later) where the jobs/artifacts endpoints have caught
    # up, surfacing the step failure and FAIL marker before the terminal line.
    PR_T = {"head": {"sha": "9001dead"}}
    RUNS_T = check_runs_payload(("9001", None))  # drain self-fetch payload (wiring a, issue #512)
    CHECK_BL_T = {
        "total_count": 2,
        "check_runs": [
            {"name": "Gate on test failures", "status": "completed", "conclusion": "failure"},
        ]
        + RUNS_T["check_runs"],
    }
    JOBS_EMPTY_T = {"jobs": [{"name": "build-and-test", "steps": []}]}
    JOBS_GATE_FAIL_T = {
        "jobs": [
            {
                "name": "build-and-test",
                "steps": [
                    {
                        "number": 1,
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "number": 4,
                        "name": "Build and run unit tests",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "number": 5,
                        "name": "Run PixelCameraOverlayE2ETest",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "number": 6,
                        "name": "Run GalleryButtonVisualE2ETest",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "number": 7,
                        "name": "Gate on test failures",
                        "status": "completed",
                        "conclusion": "failure",
                    },
                ],
            }
        ]
    }
    ARTS_EMPTY_T = {"artifacts": []}
    ARTS_E2E_T = {"artifacts": [{"id": 5005, "name": "ci-monitor-feed-GalleryButtonVisualE2ETest", "expired": False}]}
    ZIP_E2E_T = make_zip_ndjson(
        [
            '##GB4PC_TEST## {"suite":"com.gb4pc.e2e.GalleryButtonVisualE2ETest","name":"test1a","outcome":"FAIL","ms":9,"msg":"java.lang.AssertionError: button not green","trace":""}',
        ]
    )

    # Poll 1 (4): check-runs already Blocked (terminal, reused by poll_signals),
    # but jobs/artifacts not yet caught up -> no step/FAIL lines from poll_signals.
    # drain_then_print then sleeps DRAIN_DELAY_SECONDS and re-polls
    # check-runs/jobs/artifacts: attempt 1 (3 + zip) finds the caught-up
    # jobs/artifacts -> step + FAIL emitted. Per issue #419 the drain no longer
    # stops at the first fruitful attempt, so attempts 2 and 3 (3 each) also run,
    # finding everything already seen -> nothing new, no further zip. Then the
    # Blocked terminal line. 4 + 4 + 3 + 3 = 14.
    side_effects_t = collections.deque(
        [
            PR_T,  # pulls -> sha
            CHECK_BL_T,  # check-runs -> Blocked (terminal, reused by poll_signals)
            JOBS_EMPTY_T,  # jobs -> not yet caught up, nothing new
            ARTS_EMPTY_T,  # artifacts -> not yet listed
            # drain attempt 1--caught up: step + FAIL surface
            RUNS_T,  # diagnostic check-runs
            JOBS_GATE_FAIL_T,  # jobs -> "Gate on test failures" -> failure
            ARTS_E2E_T,  # artifacts -> ci-monitor-feed-GalleryButtonVisualE2ETest now listed
            ZIP_E2E_T,  # zip (raw) -> FAIL line for test1a
            # drain attempt 2--everything already seen, nothing new
            RUNS_T,
            JOBS_GATE_FAIL_T,
            ARTS_E2E_T,
            # drain attempt 3--everything already seen, nothing new
            RUNS_T,
            JOBS_GATE_FAIL_T,
            ARTS_E2E_T,
        ]
    )

    def fake_request_t(url, token, raw=False):
        return side_effects_t.popleft()

    buf_t = io.StringIO()
    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_t),
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=4000.0),
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None),
        unittest.mock.patch("sys.stdout", new=buf_t),
    ):
        rc_t = ci_monitor.main(["ci_monitor.py", "--pr", "402"])

    out_t = buf_t.getvalue()
    lines_t = out_t.splitlines()
    gate_step_line_t = 'PR#402: step "Gate on test failures" -> failure'
    fail_line_t = "PR#402: FAIL [com.gb4pc.e2e.GalleryButtonVisualE2ETest] test1a: java.lang.AssertionError: button not green"
    # Terminal is now attributed with the blocking check name.
    blocked_line_t = "PR#402: Blocked by: Gate on test failures"

    check(
        gate_step_line_t in lines_t,
        "drain poll surfaces the lagging 'Gate on test failures' step failure",
        "gate step failure line missing; output: %r" % out_t,
    )
    check(
        fail_line_t in lines_t,
        "drain poll surfaces the lagging per-test FAIL marker",
        "FAIL line missing; output: %r" % out_t,
    )
    check(
        lines_t.count(blocked_line_t) == 1,
        "Blocked attributed terminal line emitted exactly once",
        "Blocked attributed terminal line count != 1; output: %r" % out_t,
    )
    check(
        gate_step_line_t in lines_t
        and fail_line_t in lines_t
        and blocked_line_t in lines_t
        and lines_t.index(gate_step_line_t) < lines_t.index(blocked_line_t)
        and lines_t.index(fail_line_t) < lines_t.index(blocked_line_t),
        "ordering: drained step and FAIL lines precede the terminal Blocked line",
        "ordering wrong; lines: %r" % lines_t,
    )
    check(
        len(side_effects_t) == 0,
        "all 14 mocked requests consumed (drain attempt 1 downloads the zip; attempts 2-3 find nothing new)",
        "request deque not drained; %d entries left" % len(side_effects_t),
    )
    check(rc_t == 0, "main() returned 0", "main() returned %r" % rc_t)

    # ── (u) Gap E (#402 review): drain_then_print's bounded retry recovers a
    #       two-poll lag (Run G shape) that a single drain attempt would miss ──────
    print(
        "\n=== (u) Gap E (#402 review): drain attempt 2 surfaces step+FAIL after attempt 1 finds nothing ==="
    )

    # A reviewer concern on PR #408 was that a single DRAIN_DELAY_SECONDS re-poll
    # only covers a one-poll lag (Runs B/C/E/F/T), not a longer lag like Run G's. This
    # group reproduces a two-poll lag: check-runs flips to failure (Blocked) on poll
    # 1, drain attempt 1 still finds jobs/artifacts not caught up (nothing new), and
    # only drain attempt 2 sees the failing gate step and the FAIL marker. With
    # DRAIN_MAX_ATTEMPTS=3, attempt 2 still runs and surfaces both before the
    # terminal line, and the "drain poll found no new diagnostic signals" line is
    # NOT printed (drain attempt 2 found something new).
    PR_U = {"head": {"sha": "900110ng"}}
    RUNS_U = check_runs_payload(("9002", None))  # drain self-fetch payload (wiring a, issue #512)
    CHECK_BL_U = {
        "total_count": 2,
        "check_runs": [
            {"name": "Gate on test failures", "status": "completed", "conclusion": "failure"},
        ]
        + RUNS_U["check_runs"],
    }
    JOBS_EMPTY_U = {"jobs": [{"name": "build-and-test", "steps": []}]}
    JOBS_GATE_FAIL_U = {
        "jobs": [
            {
                "name": "build-and-test",
                "steps": [
                    {
                        "number": 1,
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "number": 4,
                        "name": "Build and run unit tests",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "number": 5,
                        "name": "Run PixelCameraOverlayE2ETest",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "number": 6,
                        "name": "Run GalleryButtonVisualE2ETest",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "number": 7,
                        "name": "Gate on test failures",
                        "status": "completed",
                        "conclusion": "failure",
                    },
                ],
            }
        ]
    }
    ARTS_EMPTY_U = {"artifacts": []}
    ARTS_E2E_U = {"artifacts": [{"id": 5006, "name": "ci-monitor-feed-GalleryButtonVisualE2ETest", "expired": False}]}
    ZIP_E2E_U = make_zip_ndjson(
        [
            '##GB4PC_TEST## {"suite":"com.gb4pc.e2e.GalleryButtonVisualE2ETest","name":"test1a","outcome":"FAIL","ms":9,"msg":"java.lang.AssertionError: button not green","trace":""}',
        ]
    )

    # Poll 1 (4): check-runs already Blocked (terminal, reused by poll_signals),
    # jobs/artifacts not caught up. Drain attempt 1 (3): still nothing new. Drain
    # attempt 2 (3 + zip): jobs now shows the failing gate step and the artifact is
    # listed -> step + FAIL emitted. Per issue #419 the drain no longer stops at
    # the first fruitful attempt, so attempt 3 (3) also runs, finding everything
    # already seen -> nothing new. 4 + 3 + 4 + 3 = 14.
    side_effects_u = collections.deque(
        [
            PR_U,  # pulls -> sha
            CHECK_BL_U,  # check-runs -> Blocked (terminal, reused by poll_signals)
            JOBS_EMPTY_U,  # jobs -> not yet caught up, nothing new
            ARTS_EMPTY_U,  # artifacts -> not yet listed
            # drain attempt 1--still not caught up
            RUNS_U,  # diagnostic check-runs
            JOBS_EMPTY_U,  # jobs -> still not yet caught up, nothing new
            ARTS_EMPTY_U,  # artifacts -> still not yet listed
            # drain attempt 2--now caught up
            RUNS_U,  # diagnostic check-runs
            JOBS_GATE_FAIL_U,  # jobs -> "Gate on test failures" -> failure
            ARTS_E2E_U,  # artifacts -> ci-monitor-feed-GalleryButtonVisualE2ETest now listed
            ZIP_E2E_U,  # zip (raw) -> FAIL line for test1a
            # drain attempt 3--everything already seen, nothing new
            RUNS_U,
            JOBS_GATE_FAIL_U,
            ARTS_E2E_U,
        ]
    )

    def fake_request_u(url, token, raw=False):
        return side_effects_u.popleft()

    buf_u = io.StringIO()
    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_u),
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=4100.0),
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None),
        unittest.mock.patch("sys.stdout", new=buf_u),
    ):
        rc_u = ci_monitor.main(["ci_monitor.py", "--pr", "402"])

    out_u = buf_u.getvalue()
    lines_u = out_u.splitlines()
    gate_step_line_u = 'PR#402: step "Gate on test failures" -> failure'
    fail_line_u = "PR#402: FAIL [com.gb4pc.e2e.GalleryButtonVisualE2ETest] test1a: java.lang.AssertionError: button not green"
    # Terminal is now attributed with the blocking check name.
    blocked_line_u = "PR#402: Blocked by: Gate on test failures"
    no_new_line_u = "PR#402: drain poll found no new diagnostic signals"

    check(
        gate_step_line_u in lines_u,
        "drain attempt 2 surfaces the lagging 'Gate on test failures' step failure",
        "gate step failure line missing; output: %r" % out_u,
    )
    check(
        fail_line_u in lines_u,
        "drain attempt 2 surfaces the lagging per-test FAIL marker",
        "FAIL line missing; output: %r" % out_u,
    )
    check(
        lines_u.count(blocked_line_u) == 1,
        "Blocked terminal line emitted exactly once",
        "Blocked terminal line count != 1; output: %r" % out_u,
    )
    check(
        gate_step_line_u in lines_u
        and fail_line_u in lines_u
        and blocked_line_u in lines_u
        and lines_u.index(gate_step_line_u) < lines_u.index(blocked_line_u)
        and lines_u.index(fail_line_u) < lines_u.index(blocked_line_u),
        "ordering: drained step and FAIL lines (from attempt 2) precede the terminal Blocked line",
        "ordering wrong; lines: %r" % lines_u,
    )
    check(
        no_new_line_u not in lines_u,
        "drain attempt 2 found new signals -> 'drain poll found no new diagnostic signals' NOT printed",
        "unexpected 'drain poll found no new diagnostic signals'; output: %r" % out_u,
    )
    check(
        len(side_effects_u) == 0,
        "all 14 mocked requests consumed (drain attempt 1 empty, attempt 2 downloads the zip, attempt 3 finds nothing new)",
        "request deque not drained; %d entries left" % len(side_effects_u),
    )
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
    # Scenario: poll 1 fetches the SHA (open PR), the verdict check-runs returns no
    # checks (total_count=0 -> parse_check_result='Clear') -> terminal Clear emitted,
    # loop exits. Per-iteration request order under wiring (a) (issue #512): pulls
    # (sha), verdict check-runs (passed to poll_signals which finds no Actions
    # targets, issues no jobs/artifacts requests, and returns False). The loop then
    # evaluates Clear and breaks. 2 entries in the deque.
    PR_V = {"head": {"sha": "00c1ea12"}, "merged": False, "state": "open"}
    CHECK_CLEAR_V = {"total_count": 0, "check_runs": []}

    side_effects_v = collections.deque(
        [
            PR_V,  # pulls -> sha, terminal == '' (open)
            CHECK_CLEAR_V,  # verdict check-runs -> total_count=0 -> Clear (reused by poll_signals)
        ]
    )

    def fake_request_v(url, token, raw=False):
        return side_effects_v.popleft()

    buf_v = io.StringIO()
    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_v),
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=5000.0),
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None),
        unittest.mock.patch("sys.stdout", new=buf_v),
    ):
        rc_v = ci_monitor.main(["ci_monitor.py", "--pr", "415"])

    out_v = buf_v.getvalue()
    lines_v = out_v.splitlines()
    clear_lines_v = [ln for ln in lines_v if ln.startswith("PR#415: Clear")]

    check(
        len(clear_lines_v) == 1,
        "Clear (no check runs) emitted exactly once (got %d)" % len(clear_lines_v),
        "Clear line count != 1; output: %r" % out_v,
    )
    check(
        len(side_effects_v) == 0,
        "all 2 mocked requests consumed (loop exits after first Clear)",
        "request deque not drained; %d entries left" % len(side_effects_v),
    )
    check(rc_v == 0, "main() returned 0", "main() returned %r" % rc_v)

    # ── (w) #419: the two lagging endpoints settle on different drain attempts;
    #       drain_then_print surfaces BOTH (does not stop at the first fruitful) ───
    print(
        "\n=== (w) #419: step lags to attempt 1, artifact FAIL lags to attempt 2 -> both surfaced ==="
    )

    # Issue #419's partial-lag case: on the poll that produces the terminal
    # Blocked, neither signal is ready. The gate STEP catches up on drain attempt 1
    # (so that attempt emits something), but the ci-monitor-feed-* ARTIFACT only lists
    # on drain attempt 2. The pre-#419 code broke out of the drain at the first
    # fruitful attempt, so it emitted the step but silently dropped the FAIL marker
    # for this process's lifetime. With the drain running every attempt, attempt 2
    # still runs and surfaces the FAIL. seen_arts is only populated once the
    # artifact is actually downloaded, so the lagging artifact is the genuine
    # signal recovered here, not a re-emit of an already-seen one.
    PR_W = {"head": {"sha": "519c0de1"}}
    RUNS_W = check_runs_payload(("9419", None))  # drain self-fetch payload (wiring a, issue #512)
    CHECK_BL_W = {
        "total_count": 2,
        "check_runs": [
            {"name": "Gate on test failures", "status": "completed", "conclusion": "failure"},
        ]
        + RUNS_W["check_runs"],
    }
    JOBS_EMPTY_W = {"jobs": [{"name": "build-and-test", "steps": []}]}
    JOBS_GATE_FAIL_W = {
        "jobs": [
            {
                "name": "build-and-test",
                "steps": [
                    {
                        "number": 1,
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "number": 7,
                        "name": "Gate on test failures",
                        "status": "completed",
                        "conclusion": "failure",
                    },
                ],
            }
        ]
    }
    ARTS_EMPTY_W = {"artifacts": []}
    ARTS_E2E_W = {"artifacts": [{"id": 5419, "name": "ci-monitor-feed-GalleryButtonVisualE2ETest", "expired": False}]}
    ZIP_E2E_W = make_zip_ndjson(
        [
            '##GB4PC_TEST## {"suite":"com.gb4pc.e2e.GalleryButtonVisualE2ETest","name":"test1a","outcome":"FAIL","ms":9,"msg":"java.lang.AssertionError: button not green","trace":""}',
        ]
    )

    # Poll 1 (4): Blocked terminal (reused by poll_signals), nothing caught up.
    # Drain attempt 1 (3): jobs now shows the failing gate step (emits something)
    # but artifacts still empty. Drain attempt 2 (3 + zip): artifact now listed ->
    # FAIL emitted; the step is already seen. Drain attempt 3 (3): everything
    # already seen, nothing new. 4 + 3 + 4 + 3 = 14.
    side_effects_w = collections.deque(
        [
            PR_W,  # pulls -> sha
            CHECK_BL_W,  # check-runs -> Blocked (terminal, reused by poll_signals)
            JOBS_EMPTY_W,  # jobs -> not yet caught up, nothing new
            ARTS_EMPTY_W,  # artifacts -> not yet listed
            # drain attempt 1--STEP caught up, ARTIFACT still lagging
            RUNS_W,  # diagnostic check-runs
            JOBS_GATE_FAIL_W,  # jobs -> "Gate on test failures" -> failure (emits)
            ARTS_EMPTY_W,  # artifacts -> still not listed
            # drain attempt 2--ARTIFACT now caught up
            RUNS_W,  # diagnostic check-runs
            JOBS_GATE_FAIL_W,  # jobs -> step already seen, nothing new
            ARTS_E2E_W,  # artifacts -> ci-monitor-feed-GalleryButtonVisualE2ETest now listed
            ZIP_E2E_W,  # zip (raw) -> FAIL line for test1a
            # drain attempt 3--everything already seen, nothing new
            RUNS_W,
            JOBS_GATE_FAIL_W,
            ARTS_E2E_W,
        ]
    )

    def fake_request_w(url, token, raw=False):
        return side_effects_w.popleft()

    buf_w = io.StringIO()
    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_w),
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=4200.0),
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None),
        unittest.mock.patch("sys.stdout", new=buf_w),
    ):
        rc_w = ci_monitor.main(["ci_monitor.py", "--pr", "419"])

    out_w = buf_w.getvalue()
    lines_w = out_w.splitlines()
    gate_step_line_w = 'PR#419: step "Gate on test failures" -> failure'
    fail_line_w = "PR#419: FAIL [com.gb4pc.e2e.GalleryButtonVisualE2ETest] test1a: java.lang.AssertionError: button not green"
    # Terminal is now attributed with the blocking check name.
    blocked_line_w = "PR#419: Blocked by: Gate on test failures"
    no_new_line_w = "PR#419: drain poll found no new diagnostic signals"

    check(
        gate_step_line_w in lines_w,
        "drain attempt 1 surfaces the gate step that caught up first",
        "gate step failure line missing; output: %r" % out_w,
    )
    check(
        fail_line_w in lines_w,
        "drain attempt 2 surfaces the FAIL whose artifact lagged a further attempt (NOT dropped)",
        "lagging FAIL marker was dropped; output: %r" % out_w,
    )
    check(
        lines_w.count(blocked_line_w) == 1,
        "Blocked terminal line emitted exactly once",
        "Blocked terminal line count != 1; output: %r" % out_w,
    )
    check(
        gate_step_line_w in lines_w
        and fail_line_w in lines_w
        and blocked_line_w in lines_w
        and lines_w.index(gate_step_line_w) < lines_w.index(blocked_line_w)
        and lines_w.index(fail_line_w) < lines_w.index(blocked_line_w),
        "ordering: both drained signals precede the terminal Blocked line",
        "ordering wrong; lines: %r" % lines_w,
    )
    check(
        no_new_line_w not in lines_w,
        "the drain found new signals -> 'drain poll found no new diagnostic signals' NOT printed",
        "unexpected 'drain poll found no new diagnostic signals'; output: %r" % out_w,
    )
    check(
        len(side_effects_w) == 0,
        "all 14 mocked requests consumed (step on attempt 1, artifact+zip on attempt 2, attempt 3 empty)",
        "request deque not drained; %d entries left" % len(side_effects_w),
    )
    check(rc_w == 0, "main() returned 0", "main() returned %r" % rc_w)

    # ── (x) #500 parse_actions_targets: derives (run_id, job_id) from check-runs ───
    print("\n=== (x) #500 parse_actions_targets: discovers run/job ids from check-runs data ===")

    # A check run from github-actions whose details_url carries run + job id.
    targets_basic = ci_monitor.parse_actions_targets(
        {
            "check_runs": [
                {
                    "app": {"slug": "github-actions"},
                    "details_url": "https://github.com/aunger/gallery-button-for-pixel-camera/actions/runs/555/job/42",
                }
            ]
        }
    )
    check(
        targets_basic == [("555", "42")],
        "parses (run_id, job_id) from an Actions check run's details_url",
        "expected [('555','42')]; got %r" % (targets_basic,),
    )

    # Non-Actions checks (e.g. a coverage app) are skipped; only github-actions
    # runs are returned. A run-only URL yields a None job id. Duplicates collapse.
    targets_mixed = ci_monitor.parse_actions_targets(
        {
            "check_runs": [
                {
                    "app": {"slug": "github-actions"},
                    "details_url": "https://github.com/o/r/actions/runs/555/job/42",
                },
                {
                    "app": {"slug": "github-actions"},
                    "details_url": "https://github.com/o/r/actions/runs/555/job/42",
                },
                {"app": {"slug": "codecov"}, "details_url": "https://codecov.io/gh/o/r/whatever"},
                {
                    "app": {"slug": "github-actions"},
                    "details_url": "https://github.com/o/r/actions/runs/999",
                },
            ]
        }
    )
    check(
        targets_mixed == [("555", "42"), ("999", None)],
        "skips non-Actions checks, de-dupes, and yields None job id for run-only URLs",
        "expected [('555','42'),('999',None)]; got %r" % (targets_mixed,),
    )

    # Missing app block: fall back to recognizing the /actions/runs/ URL shape.
    targets_noapp = ci_monitor.parse_actions_targets(
        {"check_runs": [{"details_url": "https://github.com/o/r/actions/runs/777/job/7"}]}
    )
    check(
        targets_noapp == [("777", "7")],
        "falls back to the /actions/runs/ URL shape when the app block is absent",
        "expected [('777','7')]; got %r" % (targets_noapp,),
    )

    # html_url fallback when details_url is absent.
    targets_html = ci_monitor.parse_actions_targets(
        {
            "check_runs": [
                {
                    "app": {"slug": "github-actions"},
                    "html_url": "https://github.com/o/r/actions/runs/321/job/9",
                }
            ]
        }
    )
    check(
        targets_html == [("321", "9")],
        "uses html_url when details_url is missing",
        "expected [('321','9')]; got %r" % (targets_html,),
    )

    # Empty check_runs -> empty result (preserves #415 Clear on total_count == 0).
    check(
        ci_monitor.parse_actions_targets({"check_runs": []}) == [],
        "empty check_runs yields no targets",
        "expected []; got %r" % (ci_monitor.parse_actions_targets({"check_runs": []}),),
    )

    # The same run id appearing both run-only and with a job id must not crash the
    # None-safe sort (None is not orderable against a str job id).
    targets_mixed_jobnone = ci_monitor.parse_actions_targets(
        {
            "check_runs": [
                {"app": {"slug": "github-actions"}, "details_url": "https://x/actions/runs/555"},
                {
                    "app": {"slug": "github-actions"},
                    "details_url": "https://x/actions/runs/555/job/42",
                },
            ]
        }
    )
    check(
        targets_mixed_jobnone == [("555", None), ("555", "42")],
        "a run appearing both run-only and with a job id sorts without raising",
        "None-safe sort failed; got %r" % (targets_mixed_jobnone,),
    )

    # ── (y) #500 parse_steps job-id filter ─────────────────────────────────────────
    print("\n=== (y) #500 parse_steps filters by job id ===")

    TWO_JOBS = {
        "jobs": [
            {
                "id": 42,
                "name": "build-and-test",
                "steps": [
                    {
                        "number": 4,
                        "name": "Build and run unit tests",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ],
            },
            {
                "id": 77,
                "name": "some-other-job",
                "steps": [
                    {
                        "number": 1,
                        "name": "Build and run unit tests",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ],
            },
        ]
    }
    out_y_match = ci_monitor.parse_steps(TWO_JOBS, set(), {"42"}, REPO_STEP_REGEX)
    check(
        out_y_match == ['step "Build and run unit tests" -> success'],
        "job-id filter {42} reports only job 42's step",
        "expected exactly job 42's step; got %r" % out_y_match,
    )
    out_y_none = ci_monitor.parse_steps(TWO_JOBS, set(), None, REPO_STEP_REGEX)
    check(
        len(out_y_none) == 2,
        "job_ids=None applies no filter (both jobs' steps reported)",
        "expected 2 step lines with no filter; got %r" % out_y_none,
    )

    # ── (z) #500 configurable interesting-step regex ───────────────────────────────
    print("\n=== (z) #500 interesting_step_regex configures which steps surface on success ===")

    CUSTOM_STEP_JOBS = {
        "jobs": [
            {
                "id": 1,
                "name": "j",
                "steps": [
                    {
                        "number": 1,
                        "name": "MyCustomStep",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "number": 2,
                        "name": "Boring setup",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "number": 3,
                        "name": "Boring failing step",
                        "status": "completed",
                        "conclusion": "failure",
                    },
                ],
            }
        ]
    }
    out_z_custom = ci_monitor.parse_steps(CUSTOM_STEP_JOBS, set(), None, "MyCustomStep")
    check(
        'step "MyCustomStep" -> success' in out_z_custom,
        "a step matching interesting_step_regex surfaces on a success conclusion",
        "custom interesting step not surfaced; got %r" % out_z_custom,
    )
    check(
        not any("Boring setup" in ln for ln in out_z_custom),
        "a non-matching successful step stays suppressed",
        "non-matching success step leaked; got %r" % out_z_custom,
    )
    check(
        'step "Boring failing step" -> failure' in out_z_custom,
        "a genuine failure surfaces regardless of interesting_step_regex (unconditional clause)",
        "genuine failure step not surfaced; got %r" % out_z_custom,
    )
    # In-code default (never-match): only the genuine failure surfaces.
    out_z_default = ci_monitor.parse_steps(CUSTOM_STEP_JOBS, set(), None)
    check(
        out_z_default == ['step "Boring failing step" -> failure'],
        "the in-code default interesting_step_regex surfaces only genuine failures",
        "default interesting-step behavior wrong; got %r" % out_z_default,
    )

    # ── (aa) #500 configurable artifact-name regex ─────────────────────────────────
    print("\n=== (aa) #500 artifact_name_regex configures which artifacts are downloaded ===")

    ARTS_FIXTURE = {
        "artifacts": [
            {"id": 1, "name": "ci-monitor-feed-unit", "expired": False},
            {"id": 2, "name": "unit-test-results", "expired": False},
            {"id": 3, "name": "myresults-foo", "expired": False},
        ]
    }
    out_aa_default = ci_monitor.parse_new_artifacts(ARTS_FIXTURE, set(), REPO_ARTIFACT_REGEX)
    check(
        out_aa_default == [("1", "ci-monitor-feed-unit")],
        "default ^ci-monitor-feed- regex includes ci-monitor-feed-unit, excludes unit-test-results",
        "default artifact regex wrong; got %r" % out_aa_default,
    )
    out_aa_custom = ci_monitor.parse_new_artifacts(ARTS_FIXTURE, set(), "^myresults-")
    check(
        out_aa_custom == [("3", "myresults-foo")],
        "a custom artifact regex selects myresults-foo and excludes ci-monitor-feed-unit",
        "custom artifact regex wrong; got %r" % out_aa_custom,
    )

    # ── (ab) #500 dual-marker back-compat and default-marker behavior ──────────────
    print("\n=== (ab) #500 test_marker_regex: dual-marker back-compat and ##TEST## default ===")

    LINE_TEST = (
        '##TEST## {"suite":"S","name":"n_new","outcome":"FAIL","ms":1,"msg":"new","trace":""}'
    )
    LINE_GB4PC = 'x ##GB4PC_TEST## {"suite":"S","name":"n_old","outcome":"FAIL","ms":1,"msg":"old","trace":""}'

    # The repo config regex (##GB4PC_TEST##|##TEST##) parses BOTH marker forms, each
    # with the correct JSON payload offset (computed from the matched span's end).
    out_ab_new = ci_monitor.parse_fails([LINE_TEST], set(), test_marker_regex=REPO_MARKER_REGEX)
    check(
        out_ab_new == ["FAIL [S] n_new: new"],
        "dual-marker regex parses a ##TEST## line",
        "dual-marker on ##TEST## wrong; got %r" % out_ab_new,
    )
    out_ab_old = ci_monitor.parse_fails([LINE_GB4PC], set(), test_marker_regex=REPO_MARKER_REGEX)
    check(
        out_ab_old == ["FAIL [S] n_old: old"],
        "dual-marker regex parses a legacy ##GB4PC_TEST## line with the correct offset",
        "dual-marker on ##GB4PC_TEST## wrong (offset bug?); got %r" % out_ab_old,
    )
    out_ab_both = ci_monitor.parse_fails(
        [LINE_TEST, LINE_GB4PC], set(), test_marker_regex=REPO_MARKER_REGEX
    )
    check(
        out_ab_both == ["FAIL [S] n_new: new", "FAIL [S] n_old: old"],
        "a mixed stream with both marker forms parses both lines",
        "mixed-marker stream wrong; got %r" % out_ab_both,
    )

    # The in-code default (##TEST##) parses a ##TEST## line but does NOT parse a
    # ##GB4PC_TEST##-only line: ##TEST## does not match anywhere in the legacy
    # marker (the char after the leading ## is G, not T), so re.search returns None
    # and the line is skipped. This pins "switch by default, keep both only in this
    # repo's config".
    out_ab_def_new = ci_monitor.parse_fails([LINE_TEST], set())
    check(
        out_ab_def_new == ["FAIL [S] n_new: new"],
        "the default ##TEST## regex parses a ##TEST## line",
        "default marker on ##TEST## wrong; got %r" % out_ab_def_new,
    )
    out_ab_def_old = ci_monitor.parse_fails([LINE_GB4PC], set())
    check(
        out_ab_def_old == [],
        "the default ##TEST## regex does NOT parse a legacy ##GB4PC_TEST##-only line",
        "default marker unexpectedly parsed a ##GB4PC_TEST## line; got %r" % out_ab_def_old,
    )

    # ── (ac) #500 load_config: file present / absent / invalid / partial / bad regex ─
    print(
        "\n=== (ac) #500 load_config: defaults, absence, invalid JSON, partial keys, bad regex ==="
    )

    import tempfile  # noqa: E402

    _DEFAULTS = {
        "artifact_name_regex": ci_monitor.DEFAULT_ARTIFACT_NAME_REGEX,
        "interesting_step_regex": ci_monitor.DEFAULT_INTERESTING_STEP_REGEX,
        "test_marker_regex": ci_monitor.DEFAULT_TEST_MARKER_REGEX,
        "label_gate_check_regex": ci_monitor.DEFAULT_LABEL_GATE_CHECK_REGEX,
    }

    def _write_tmp(text):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    # Present file with all four keys.
    _p_full = _write_tmp(
        json.dumps(
            {
                "artifact_name_regex": "^foo-",
                "interesting_step_regex": "bar",
                "test_marker_regex": "##BAZ##",
                "label_gate_check_regex": "MyGate",
            }
        )
    )
    cfg_full = ci_monitor.load_config(_p_full)
    os.remove(_p_full)
    check(
        cfg_full
        == {
            "artifact_name_regex": "^foo-",
            "interesting_step_regex": "bar",
            "test_marker_regex": "##BAZ##",
            "label_gate_check_regex": "MyGate",
        },
        "present file returns all four configured regexes",
        "present-file config wrong; got %r" % cfg_full,
    )

    # Absent file -> all defaults.
    cfg_absent = ci_monitor.load_config(
        os.path.join(tempfile.gettempdir(), "no-such-ci-config.json")
    )
    check(
        cfg_absent == _DEFAULTS,
        "absent file falls back to all in-code defaults",
        "absent-file config wrong; got %r" % cfg_absent,
    )

    # Invalid JSON -> all defaults, no raise.
    _p_bad = _write_tmp("{ this is not json ")
    cfg_bad = ci_monitor.load_config(_p_bad)
    os.remove(_p_bad)
    check(
        cfg_bad == _DEFAULTS,
        "invalid JSON falls back to all defaults without raising",
        "invalid-JSON config wrong; got %r" % cfg_bad,
    )

    # Partial file (one key) -> that key from file, others default.
    _p_partial = _write_tmp(json.dumps({"test_marker_regex": "##ONLY##"}))
    cfg_partial = ci_monitor.load_config(_p_partial)
    os.remove(_p_partial)
    check(
        cfg_partial["test_marker_regex"] == "##ONLY##"
        and cfg_partial["artifact_name_regex"] == ci_monitor.DEFAULT_ARTIFACT_NAME_REGEX
        and cfg_partial["interesting_step_regex"] == ci_monitor.DEFAULT_INTERESTING_STEP_REGEX,
        "a partial file uses the file value for its key and defaults for the rest",
        "partial config wrong; got %r" % cfg_partial,
    )

    # Non-compiling regex for one key -> that key falls back to its default.
    _p_badre = _write_tmp(json.dumps({"artifact_name_regex": "([unclosed"}))
    cfg_badre = ci_monitor.load_config(_p_badre)
    os.remove(_p_badre)
    check(
        cfg_badre["artifact_name_regex"] == ci_monitor.DEFAULT_ARTIFACT_NAME_REGEX,
        "a non-compiling regex value falls back to that key's default",
        "bad-regex fallback wrong; got %r" % cfg_badre,
    )

    # The committed repo config carries this repo's values, including the dual marker.
    _repo_cfg_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "ci_monitor", "ci_monitor.config.json"
    )
    cfg_repo = ci_monitor.load_config(_repo_cfg_path)
    check(
        cfg_repo["test_marker_regex"] == REPO_MARKER_REGEX
        and cfg_repo["artifact_name_regex"] == REPO_ARTIFACT_REGEX
        and cfg_repo["interesting_step_regex"] == REPO_STEP_REGEX,
        "the committed ci_monitor.config.json carries this repo's regexes (incl. dual marker)",
        "committed repo config wrong; got %r" % cfg_repo,
    )

    # ── (ad) #499 wrong-run regression + multi-run discovery via main() ────────────
    print(
        "\n=== (ad) #499/#500: the failing build run is tracked among multiple Actions checks ==="
    )

    # #499: an auxiliary Actions workflow's check run appears first in check-runs (its
    # own run id, no ci-monitor-feed-* artifact, an unrelated job id), and the real build
    # check appears later (failing gate step + a ci-monitor-feed-* artifact). Discovering
    # targets from check-runs (not a hardcoded workflow/job name) must track the build
    # run so its step failure and FAIL surface. Two distinct run ids also exercise
    # multi-run fan-out.
    PR_AD = {"head": {"sha": "499f1xed"}}
    # Under wiring (a) the verdict and diagnostic payloads are unified: one
    # check-runs fetch drives both parse_check_result (verdict) and
    # parse_actions_targets (run/job discovery). CHECK_BL_AD combines the failing
    # status check (for the Blocked verdict) and the two Actions check runs (for
    # multi-run fan-out: aux run 700/job 70 and build run 800/job 80).
    _diag_ad = check_runs_payload(("700", "70"), ("800", "80"))
    CHECK_BL_AD = {
        "total_count": 3,
        "check_runs": [
            {"name": "build-and-test", "status": "completed", "conclusion": "failure"},
        ]
        + _diag_ad["check_runs"],
    }
    DIAG_CHECK_AD = _diag_ad  # drain attempts still self-fetch (aux run 700, build run 800)
    # Aux run 700/job 70: a successful unrelated step, no ci-monitor-feed-* artifact.
    JOBS_AUX_AD = {
        "jobs": [
            {
                "id": 70,
                "name": "aux-workflow",
                "steps": [
                    {"number": 1, "name": "Lint", "status": "completed", "conclusion": "success"},
                ],
            }
        ]
    }
    ARTS_AUX_AD = {"artifacts": [{"id": 7000, "name": "lint-report", "expired": False}]}
    # Build run 800/job 80: the failing gate step + a ci-monitor-feed-* artifact.
    JOBS_BUILD_AD = {
        "jobs": [
            {
                "id": 80,
                "name": "build-and-test",
                "steps": [
                    {
                        "number": 7,
                        "name": "Gate on test failures",
                        "status": "completed",
                        "conclusion": "failure",
                    },
                ],
            }
        ]
    }
    ARTS_BUILD_AD = {
        "artifacts": [{"id": 8000, "name": "ci-monitor-feed-GalleryButtonVisualE2ETest", "expired": False}]
    }
    ZIP_BUILD_AD = make_zip_ndjson(
        [
            '##GB4PC_TEST## {"suite":"com.gb4pc.e2e.GalleryButtonVisualE2ETest","name":"test1a","outcome":"FAIL","ms":9,"msg":"button not green","trace":""}',
        ]
    )

    # Single poll, terminal Blocked. Under wiring (a), poll_signals receives the
    # same CHECK_BL_AD payload (which includes both Actions check runs), discovers
    # runs 700 and 800, and fetches jobs+artifacts; the build run's artifact
    # downloads its zip. Then drain_then_print runs DRAIN_MAX_ATTEMPTS times,
    # finding everything already seen (drain self-fetches DIAG_CHECK_AD).
    # Poll: pulls, verdict check-runs (reused by poll_signals), (jobs+arts for 700),
    #       (jobs+arts+zip for 800) = 1+1+2+3 = 7.
    # Each drain attempt: diagnostic check-runs, (jobs+arts x2) = 1+4 = 5; x3 = 15.
    # 7 + 15 = 22.
    side_effects_ad = collections.deque(
        [
            PR_AD,  # pulls -> sha
            CHECK_BL_AD,  # verdict check-runs -> Blocked (terminal, reused by poll_signals)
            JOBS_AUX_AD,  # run 700 jobs -> unrelated success step (suppressed)
            ARTS_AUX_AD,  # run 700 artifacts -> no ci-monitor-feed-* match, no zip
            JOBS_BUILD_AD,  # run 800 jobs -> gate step failure
            ARTS_BUILD_AD,  # run 800 artifacts -> ci-monitor-feed-GalleryButtonVisualE2ETest
            ZIP_BUILD_AD,  # run 800 zip -> FAIL line
        ]
    )
    for _ in range(3):
        side_effects_ad.append(DIAG_CHECK_AD)  # drain: diagnostic check-runs
        side_effects_ad.append(JOBS_AUX_AD)  # run 700 jobs (seen)
        side_effects_ad.append(ARTS_AUX_AD)  # run 700 artifacts (no match)
        side_effects_ad.append(JOBS_BUILD_AD)  # run 800 jobs (seen)
        side_effects_ad.append(ARTS_BUILD_AD)  # run 800 artifacts (seen)

    def fake_request_ad(url, token, raw=False):
        return side_effects_ad.popleft()

    buf_ad = io.StringIO()
    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_ad),
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=6000.0),
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None),
        unittest.mock.patch("sys.stdout", new=buf_ad),
    ):
        rc_ad = ci_monitor.main(["ci_monitor.py", "--pr", "499"])

    out_ad = buf_ad.getvalue()
    lines_ad = out_ad.splitlines()
    gate_step_ad = 'PR#499: step "Gate on test failures" -> failure'
    fail_line_ad = (
        "PR#499: FAIL [com.gb4pc.e2e.GalleryButtonVisualE2ETest] test1a: button not green"
    )
    # Terminal is now attributed with the blocking check name.
    blocked_line_ad = "PR#499: Blocked by: build-and-test"

    check(
        gate_step_ad in lines_ad,
        "the build run's failing gate step is tracked despite an auxiliary Actions check appearing first",
        "build gate step missing; output: %r" % out_ad,
    )
    check(
        fail_line_ad in lines_ad,
        "the build run's per-test FAIL is surfaced (multi-run discovery from check-runs)",
        "build FAIL missing; output: %r" % out_ad,
    )
    check(
        not any("Lint" in ln for ln in lines_ad),
        "the auxiliary run's unrelated success step is suppressed",
        "auxiliary step leaked; output: %r" % out_ad,
    )
    check(
        gate_step_ad in lines_ad
        and fail_line_ad in lines_ad
        and blocked_line_ad in lines_ad
        and lines_ad.index(gate_step_ad) < lines_ad.index(blocked_line_ad)
        and lines_ad.index(fail_line_ad) < lines_ad.index(blocked_line_ad),
        "ordering: discovered build signals precede the terminal Blocked line",
        "ordering wrong; lines: %r" % lines_ad,
    )
    check(
        len(side_effects_ad) == 0,
        "all 22 mocked requests consumed (two runs fanned out, build zip once)",
        "request deque not drained; %d entries left" % len(side_effects_ad),
    )
    check(rc_ad == 0, "main() returned 0", "main() returned %r" % rc_ad)

    # ── (ae) #500 doc-sync grep: no legacy hardcoded couplings remain ──────────────
    print(
        "\n=== (ae) #500 doc-sync: ci_monitor.py has no legacy hardcoded workflow/job/marker literals ==="
    )

    _MONITOR_SRC = open(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "ci_monitor", "ci_monitor.py"),
        encoding="utf-8",
    ).read()

    # Strip docstrings/comments would be heavy; instead assert the load-bearing
    # legacy literals do not appear as code. These are gone entirely from the source
    # (including comments), having been replaced by config-driven regexes (#500).
    for legacy in (
        '"build-and-test"',
        "head_sha=",
        "event=pull_request",
        "workflow_runs",
        "parse_run_id",
    ):
        check(
            legacy not in _MONITOR_SRC,
            "no legacy literal %r remains in ci_monitor.py" % legacy,
            "legacy literal %r still present in ci_monitor.py" % legacy,
        )
    # The hardcoded named-step heuristic ("Build and run unit tests" / "E2ETest" as
    # code) is gone; those names now live only in the config file.
    check(
        'name == "Build and run unit tests"' not in _MONITOR_SRC,
        "the hardcoded 'Build and run unit tests' name-equality heuristic is gone",
        "hardcoded unit-test step name still in ci_monitor.py",
    )
    # The legacy emit marker is no longer hardcoded as a contract; only the
    # DEFAULT_TEST_MARKER_REGEX (##TEST##) and explanatory text remain. Assert the
    # literal ##GB4PC_TEST## does not appear as a Python string constant assignment.
    check(
        'TEST_MARKER = "##GB4PC_TEST##"' not in _MONITOR_SRC,
        "the module-level TEST_MARKER = ##GB4PC_TEST## constant is gone",
        "TEST_MARKER constant still present in ci_monitor.py",
    )

    # ── (af) #500 poll_signals scopes the job-id filter per run ────────────────────
    print(
        "\n=== (af) #500 poll_signals: a run-only target does not widen another run's job filter ==="
    )

    # Two Actions runs in one poll: run 100 exposes only a run id (no job id), run 200
    # exposes job 22. The job filter must be scoped per run: run 200's steps are
    # filtered to job 22 (so a step in its *other* job 23 is suppressed), even though
    # run 100 is run-only. Before the per-run scoping fix, a single run-only target
    # set job_ids=None globally and run 200's job-23 step would have leaked.
    PR_AF = {"head": {"sha": "5c0pe1d1"}}
    # Under wiring (a) the verdict and diagnostic payloads are unified. CHECK_BL_AF
    # combines the failing status check (for the Blocked verdict) and the two
    # Actions check runs (run 100 run-only, run 200 job 22) for per-run scoping.
    _diag_af = check_runs_payload(("100", None), ("200", "22"))
    CHECK_BL_AF = {
        "total_count": 3,
        "check_runs": [
            {"name": "build-and-test", "status": "completed", "conclusion": "failure"},
        ]
        + _diag_af["check_runs"],
    }
    DIAG_CHECK_AF = _diag_af  # drain attempts still self-fetch (run 100 run-only, run 200 job 22)
    # Run 100 (run-only): a named unit-test step that should surface (no job filter).
    JOBS_100_AF = {
        "jobs": [
            {
                "id": 11,
                "name": "run-only-job",
                "steps": [
                    {
                        "number": 1,
                        "name": "Build and run unit tests",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ],
            }
        ]
    }
    # Run 200: job 22 has a named E2E step (should surface); job 23 has a named step
    # that must be SUPPRESSED because the filter is scoped to job 22 only.
    JOBS_200_AF = {
        "jobs": [
            {
                "id": 22,
                "name": "build-and-test",
                "steps": [
                    {
                        "number": 1,
                        "name": "Run PixelCameraOverlayE2ETest",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ],
            },
            {
                "id": 23,
                "name": "unrelated-job",
                "steps": [
                    {
                        "number": 1,
                        "name": "Run GalleryButtonVisualE2ETest",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ],
            },
        ]
    }
    ARTS_EMPTY_AF = {"artifacts": []}

    # Single poll, terminal Blocked. Under wiring (a), poll_signals receives
    # CHECK_BL_AF (which includes both Actions check runs), so no separate
    # diagnostic fetch is needed; per run, fetches jobs+artifacts (no zip: artifacts
    # empty). Poll: pulls, verdict check-runs (reused by poll_signals),
    # (jobs+arts x2) = 6. Each drain attempt: diagnostic check-runs, (jobs+arts x2)
    # = 5; x3 = 15. 6+15=21.
    side_effects_af = collections.deque(
        [
            PR_AF,
            CHECK_BL_AF,
            JOBS_100_AF,
            ARTS_EMPTY_AF,
            JOBS_200_AF,
            ARTS_EMPTY_AF,
        ]
    )
    for _ in range(3):
        side_effects_af.append(DIAG_CHECK_AF)
        side_effects_af.append(JOBS_100_AF)
        side_effects_af.append(ARTS_EMPTY_AF)
        side_effects_af.append(JOBS_200_AF)
        side_effects_af.append(ARTS_EMPTY_AF)

    def fake_request_af(url, token, raw=False):
        return side_effects_af.popleft()

    buf_af = io.StringIO()
    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_af),
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=7000.0),
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None),
        unittest.mock.patch("sys.stdout", new=buf_af),
    ):
        rc_af = ci_monitor.main(["ci_monitor.py", "--pr", "500"])

    out_af = buf_af.getvalue()
    lines_af = out_af.splitlines()
    check(
        'PR#500: step "Build and run unit tests" -> success' in lines_af,
        "the run-only run's named step surfaces (no job filter for that run)",
        "run-only run's step missing; output: %r" % out_af,
    )
    check(
        'PR#500: step "Run PixelCameraOverlayE2ETest" -> success' in lines_af,
        "run 200's filtered job (22) reports its named step",
        "run 200 job-22 step missing; output: %r" % out_af,
    )
    check(
        not any("GalleryButtonVisualE2ETest" in ln for ln in lines_af),
        "run 200's other job (23) is suppressed: the run-only target did not widen run 200's filter",
        "job-23 step leaked (per-run scoping failed); output: %r" % out_af,
    )
    check(
        len(side_effects_af) == 0,
        "all 21 mocked requests consumed (two runs fanned out, no zips)",
        "request deque not drained; %d entries left" % len(side_effects_af),
    )
    check(rc_af == 0, "main() returned 0", "main() returned %r" % rc_af)

    # ── (ag) #516 parse_check_summary: per-check rows, blocking, label_gate ─────────
    print("\n=== (ag) #516 parse_check_summary: rows with correct fields ===")

    # Payload with a failing 'No blocking labels' check plus three passing checks.
    # The label_gate_check_regex is passed explicitly to test the function directly.
    LABEL_GATE_REGEX = "No blocking labels"
    SUMMARY_PAYLOAD = {
        "total_count": 4,
        "check_runs": [
            {"name": "No blocking labels", "status": "completed", "conclusion": "failure"},
            {"name": "Build and run unit tests", "status": "completed", "conclusion": "success"},
            {
                "name": "Run PixelCameraOverlayE2ETest",
                "status": "completed",
                "conclusion": "success",
            },
            {
                "name": "Run GalleryButtonVisualE2ETest",
                "status": "completed",
                "conclusion": "success",
            },
        ],
    }
    rows_ag = ci_monitor.parse_check_summary(SUMMARY_PAYLOAD, LABEL_GATE_REGEX)

    check(
        len(rows_ag) == 4,
        "parse_check_summary returns one row per check run (got %d)" % len(rows_ag),
        "expected 4 rows; got %d" % len(rows_ag),
    )
    blocking_rows_ag = [r for r in rows_ag if r["blocking"]]
    check(
        len(blocking_rows_ag) == 1,
        "exactly one blocking row (the 'No blocking labels' check)",
        "expected 1 blocking row; got %d: %r" % (len(blocking_rows_ag), blocking_rows_ag),
    )
    check(
        len(blocking_rows_ag) == 1 and blocking_rows_ag[0]["name"] == "No blocking labels",
        "blocking row is named 'No blocking labels'",
        "blocking row name wrong; got %r" % (blocking_rows_ag,),
    )
    check(
        len(blocking_rows_ag) == 1 and blocking_rows_ag[0]["label_gate"],
        "the blocking row is also label_gate=True",
        "label_gate not set on blocking row; got %r" % (blocking_rows_ag,),
    )
    passing_rows_ag = [r for r in rows_ag if not r["blocking"]]
    check(
        all(not r["label_gate"] for r in passing_rows_ag),
        "no passing row is label_gate",
        "unexpected label_gate on passing row; got %r" % (passing_rows_ag,),
    )
    check(
        all(r["conclusion"] == "success" for r in passing_rows_ag),
        "all passing rows have conclusion 'success'",
        "wrong conclusion on passing rows; got %r" % (passing_rows_ag,),
    )

    # All-passing payload: no blocking rows, no label_gate rows.
    ALL_PASS_PAYLOAD = {
        "total_count": 2,
        "check_runs": [
            {"name": "check-1", "status": "completed", "conclusion": "success"},
            {"name": "check-2", "status": "completed", "conclusion": "success"},
        ],
    }
    rows_ag_pass = ci_monitor.parse_check_summary(ALL_PASS_PAYLOAD)
    check(
        not any(r["blocking"] for r in rows_ag_pass),
        "all-passing payload produces no blocking rows",
        "unexpected blocking rows; got %r" % (rows_ag_pass,),
    )

    # Infra conclusion (cancelled): that row is blocking=True, label_gate=False.
    INFRA_PAYLOAD = {
        "total_count": 1,
        "check_runs": [{"name": "build-job", "status": "completed", "conclusion": "cancelled"}],
    }
    rows_ag_infra = ci_monitor.parse_check_summary(INFRA_PAYLOAD)
    check(
        len(rows_ag_infra) == 1 and rows_ag_infra[0]["blocking"],
        "a cancelled check is blocking",
        "cancelled check not marked blocking; got %r" % (rows_ag_infra,),
    )
    check(
        not rows_ag_infra[0]["label_gate"],
        "a cancelled non-gate check is not label_gate",
        "unexpected label_gate on cancelled non-gate check; got %r" % (rows_ag_infra,),
    )

    # Empty check_runs yields [].
    check(
        ci_monitor.parse_check_summary({"total_count": 0, "check_runs": []}) == [],
        "empty check_runs yields []",
        "expected []; got non-empty from empty payload",
    )

    # In-progress check: conclusion field is blank, but effective is the status string.
    INPROG_PAYLOAD = {
        "total_count": 1,
        "check_runs": [{"name": "job", "status": "in_progress", "conclusion": None}],
    }
    rows_ag_ip = ci_monitor.parse_check_summary(INPROG_PAYLOAD)
    check(
        len(rows_ag_ip) == 1 and rows_ag_ip[0]["conclusion"] == "in_progress",
        "an in-progress check's effective conclusion is 'in_progress'",
        "in-progress conclusion wrong; got %r" % (rows_ag_ip,),
    )
    check(
        not rows_ag_ip[0]["blocking"],
        "an in-progress check is not blocking",
        "in-progress check wrongly marked blocking; got %r" % (rows_ag_ip,),
    )

    # ── (ah) #516 format_check_summary and blocking_suffix ───────────────────────
    print("\n=== (ah) #516 format_check_summary and blocking_suffix ===")

    # Basic format: first line is 'summary', rows are aligned, blocking row carries
    # [BLOCKING], a label-gate blocking row also carries [label gate].
    ROWS_AH = [
        {
            "name": "No blocking labels",
            "conclusion": "failure",
            "blocking": True,
            "label_gate": True,
        },
        {
            "name": "Build and run unit tests",
            "conclusion": "success",
            "blocking": False,
            "label_gate": False,
        },
        {
            "name": "Run PixelCameraOverlayE2ETest",
            "conclusion": "success",
            "blocking": False,
            "label_gate": False,
        },
    ]
    lines_ah = ci_monitor.format_check_summary(ROWS_AH)
    check(
        len(lines_ah) == 4 and lines_ah[0] == "summary",
        "first line of format_check_summary is 'summary'",
        "wrong first line; got %r" % (lines_ah,),
    )
    check(
        any(
            "No blocking labels" in ln
            and "failure" in ln
            and "[BLOCKING]" in ln
            and "[label gate]" in ln
            for ln in lines_ah
        ),
        "label-gate blocking row carries [BLOCKING] and [label gate]",
        "label-gate row format wrong; got %r" % (lines_ah,),
    )
    check(
        any(
            "Build and run unit tests" in ln and "success" in ln and "[BLOCKING]" not in ln
            for ln in lines_ah
        ),
        "passing row carries no [BLOCKING]",
        "passing row format wrong; got %r" % (lines_ah,),
    )
    check(
        ci_monitor.format_check_summary([]) == [],
        "empty rows yields []",
        "expected [] from empty rows",
    )

    # blocking_suffix: no blocking rows -> "".
    check(
        ci_monitor.blocking_suffix(
            [{"name": "x", "conclusion": "success", "blocking": False, "label_gate": False}]
        )
        == "",
        "blocking_suffix returns '' when no row is blocking",
        "expected ''; got %r"
        % ci_monitor.blocking_suffix(
            [{"name": "x", "conclusion": "success", "blocking": False, "label_gate": False}]
        ),
    )

    # One non-label-gate blocking row.
    suffix_code = ci_monitor.blocking_suffix(
        [{"name": "build-and-test", "conclusion": "failure", "blocking": True, "label_gate": False}]
    )
    check(
        suffix_code == " by: build-and-test",
        "blocking_suffix for a non-gate blocker: ' by: build-and-test'",
        "got %r" % suffix_code,
    )

    # Only label-gate blocking rows: carries [label gate].
    suffix_gate = ci_monitor.blocking_suffix(
        [
            {
                "name": "No blocking labels",
                "conclusion": "failure",
                "blocking": True,
                "label_gate": True,
            }
        ]
    )
    check(
        suffix_gate == " by: No blocking labels [label gate]",
        "blocking_suffix for a label-gate-only blocker: ' by: No blocking labels [label gate]'",
        "got %r" % suffix_gate,
    )

    # Mixed (code/test failure plus label gate): no [label gate] flag.
    suffix_mixed = ci_monitor.blocking_suffix(
        [
            {
                "name": "build-and-test",
                "conclusion": "failure",
                "blocking": True,
                "label_gate": False,
            },
            {
                "name": "No blocking labels",
                "conclusion": "failure",
                "blocking": True,
                "label_gate": True,
            },
        ]
    )
    check(
        suffix_mixed == " by: build-and-test, No blocking labels",
        "mixed blocker: no [label gate] since not all-label-gate; got %r" % suffix_mixed,
        "mixed suffix wrong; got %r" % suffix_mixed,
    )

    # ── (ai) #516 load_config: label_gate_check_regex key ────────────────────────
    print("\n=== (ai) #516 load_config: label_gate_check_regex defaults and repo config ===")

    import tempfile as _tempfile  # noqa: E402 (already imported in ac, re-using module)

    # Default: key absent in file -> DEFAULT_LABEL_GATE_CHECK_REGEX (never-match).
    _p_ai_partial = _tempfile.mkstemp(suffix=".json")[1]
    with open(_p_ai_partial, "w", encoding="utf-8") as _fh:
        _fh.write(json.dumps({"artifact_name_regex": "^testresults-"}))
    cfg_ai_partial = ci_monitor.load_config(_p_ai_partial)
    os.remove(_p_ai_partial)
    check(
        cfg_ai_partial.get("label_gate_check_regex") == ci_monitor.DEFAULT_LABEL_GATE_CHECK_REGEX,
        "absent label_gate_check_regex falls back to DEFAULT_LABEL_GATE_CHECK_REGEX",
        "fallback wrong; got %r" % cfg_ai_partial.get("label_gate_check_regex"),
    )

    # Absent file -> DEFAULT_LABEL_GATE_CHECK_REGEX.
    cfg_ai_absent = ci_monitor.load_config(
        os.path.join(_tempfile.gettempdir(), "no-such-ci-config-ai.json")
    )
    check(
        cfg_ai_absent.get("label_gate_check_regex") == ci_monitor.DEFAULT_LABEL_GATE_CHECK_REGEX,
        "absent file -> label_gate_check_regex is the in-code default",
        "absent-file fallback wrong; got %r" % cfg_ai_absent.get("label_gate_check_regex"),
    )

    # Committed repo config carries 'No blocking labels'.
    _repo_cfg_path_ai = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "ci_monitor", "ci_monitor.config.json"
    )
    cfg_ai_repo = ci_monitor.load_config(_repo_cfg_path_ai)
    check(
        cfg_ai_repo.get("label_gate_check_regex") == "No blocking labels",
        "committed config carries label_gate_check_regex = 'No blocking labels'",
        "committed config wrong; got %r" % cfg_ai_repo.get("label_gate_check_regex"),
    )

    # ── (aj) #516 end-to-end: label-gate Blocked scenario (the #513 / #514 case) ─
    print(
        "\n=== (aj) #516 end-to-end: label-gate Blocked -> 'Blocked by: No blocking labels [label gate]' ==="
    )

    # Mirrors the #513 scenario: verdict check-runs contains a 'No blocking labels'
    # failure plus three passing checks. The summary shows all four; the terminal is
    # attributed to the label-gate check; the drain flag is suppressed (diagnosed).
    # The label_gate_check_regex config key is set to 'No blocking labels' in the
    # committed ci_monitor.config.json, so main() loads it automatically (no patch needed
    # beyond the standard _request mock).
    PR_AJ = {"head": {"sha": "513c0de1"}}
    CHECK_BL_GATE_AJ = {
        "total_count": 4,
        "check_runs": [
            {"name": "No blocking labels", "status": "completed", "conclusion": "failure"},
            {"name": "Build and run unit tests", "status": "completed", "conclusion": "success"},
            {
                "name": "Run PixelCameraOverlayE2ETest",
                "status": "completed",
                "conclusion": "success",
            },
            {
                "name": "Run GalleryButtonVisualE2ETest",
                "status": "completed",
                "conclusion": "success",
            },
        ],
    }
    # Diagnostic check-runs: no Actions targets -> poll_signals returns False immediately.
    DIAG_EMPTY_AJ = {"total_count": 0, "check_runs": []}

    # Single-poll run (wiring a): pulls, verdict check-runs (reused by poll_signals;
    # no Actions targets -> fast exit). Then drain_then_print: DRAIN_MAX_ATTEMPTS
    # attempts each with a self-fetch of check-runs -> no targets -> 3 requests. 2 + 3 = 5.
    side_effects_aj = collections.deque(
        [
            PR_AJ,  # pulls -> sha
            CHECK_BL_GATE_AJ,  # verdict check-runs -> Blocked (label gate, reused by poll_signals)
        ]
    )
    for _ in range(3):  # DRAIN_MAX_ATTEMPTS drain attempts
        side_effects_aj.append(DIAG_EMPTY_AJ)  # each drain poll: no targets

    def fake_request_aj(url, token, raw=False):
        return side_effects_aj.popleft()

    buf_aj = io.StringIO()
    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_aj),
        unittest.mock.patch.object(ci_monitor.time, "time", return_value=9000.0),
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None),
        unittest.mock.patch("sys.stdout", new=buf_aj),
    ):
        rc_aj = ci_monitor.main(["ci_monitor.py", "--pr", "513"])

    out_aj = buf_aj.getvalue()
    lines_aj = out_aj.splitlines()
    terminal_aj = "PR#513: Blocked by: No blocking labels [label gate]"
    summary_hdr_aj = "PR#513: summary"
    no_new_aj = "PR#513: drain poll found no new diagnostic signals"

    check(
        terminal_aj in lines_aj,
        "terminal line is exactly 'PR#513: Blocked by: No blocking labels [label gate]'",
        "terminal line wrong; output: %r" % out_aj,
    )
    check(
        no_new_aj not in lines_aj,
        "drain flag suppressed (No blocking labels is a blocking/diagnosed check)",
        "'drain poll found no new diagnostic signals' unexpectedly present; output: %r" % out_aj,
    )
    check(
        summary_hdr_aj in lines_aj and lines_aj.index(summary_hdr_aj) < lines_aj.index(terminal_aj),
        "summary header appears before the terminal line",
        "summary header missing or after terminal; output: %r" % out_aj,
    )
    check(
        any(
            "No blocking labels" in ln
            and "failure" in ln
            and "[BLOCKING]" in ln
            and "[label gate]" in ln
            for ln in lines_aj
        ),
        "summary row for 'No blocking labels' shows failure, [BLOCKING], and [label gate]",
        "label-gate summary row wrong; output: %r" % out_aj,
    )
    check(
        any("Build and run unit tests" in ln and "success" in ln for ln in lines_aj),
        "summary row for 'Build and run unit tests' shows success",
        "passing summary row missing; output: %r" % out_aj,
    )
    check(
        any("Run PixelCameraOverlayE2ETest" in ln and "success" in ln for ln in lines_aj),
        "summary row for 'Run PixelCameraOverlayE2ETest' shows success",
        "passing summary row missing; output: %r" % out_aj,
    )
    check(
        any("Run GalleryButtonVisualE2ETest" in ln and "success" in ln for ln in lines_aj),
        "summary row for 'Run GalleryButtonVisualE2ETest' shows success",
        "passing summary row missing; output: %r" % out_aj,
    )
    # Summary lines precede the terminal; terminal is the last PR#N: line.
    pr_lines_aj = [ln for ln in lines_aj if ln.startswith("PR#513:")]
    check(
        len(pr_lines_aj) > 0 and pr_lines_aj[-1] == terminal_aj,
        "terminal line is the last PR#513: line",
        "terminal is not the last PR#513: line; output: %r" % out_aj,
    )
    check(
        len(side_effects_aj) == 0,
        "all 5 mocked requests consumed (1 poll (no diag self-fetch) + 3 drain attempts, no jobs/artifacts fetched)",
        "request deque not drained; %d entries left" % len(side_effects_aj),
    )
    check(rc_aj == 0, "main() returned 0", "main() returned %r" % rc_aj)

    # ── (ak) #516 doc-sync: 'No blocking labels' not hardcoded in ci_monitor.py ──
    print(
        "\n=== (ak) #516 doc-sync: 'No blocking labels' lives only in the config, not in ci_monitor.py ==="
    )

    _MONITOR_SRC_AK = open(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "ci_monitor", "ci_monitor.py"),
        encoding="utf-8",
    ).read()

    check(
        "No blocking labels" not in _MONITOR_SRC_AK,
        "the string 'No blocking labels' does not appear in ci_monitor.py (config-driven only)",
        "'No blocking labels' hardcoded in ci_monitor.py--must live only in ci_monitor.config.json",
    )

    # ── (al) #619: importing this module directly has no side effects ─────────────
    print(
        "\n=== (al) #619: importing test_ci_monitor.py directly runs no checks"
        " (no import side effects) ==="
    )

    _SCRIPTS_DIR_AL = os.path.dirname(os.path.abspath(__file__))

    _plain_import_al = subprocess.run(
        [sys.executable, "-c", "import test_ci_monitor"],
        cwd=_SCRIPTS_DIR_AL,
        capture_output=True,
        text=True,
        timeout=10,
    )
    check(
        _plain_import_al.returncode == 0
        and _plain_import_al.stdout == ""
        and _plain_import_al.stderr == "",
        "plain import exits 0 with no PASS/FAIL output (no side effects)",
        "plain import misbehaved; returncode=%r stdout=%r stderr=%r"
        % (_plain_import_al.returncode, _plain_import_al.stdout, _plain_import_al.stderr),
    )

    _reuse_helper_al = subprocess.run(
        [
            sys.executable,
            "-c",
            "import test_ci_monitor as m; print(hasattr(m, 'check_runs_payload'))",
        ],
        cwd=_SCRIPTS_DIR_AL,
        capture_output=True,
        text=True,
        timeout=10,
    )
    check(
        _reuse_helper_al.returncode == 0 and _reuse_helper_al.stdout == "True\n",
        "check_runs_payload() is reachable on the freshly imported module, unchanged"
        " by running the suite",
        "check_runs_payload() not reachable via plain import; returncode=%r stdout=%r stderr=%r"
        % (_reuse_helper_al.returncode, _reuse_helper_al.stdout, _reuse_helper_al.stderr),
    )

    # ── (am) #603 parse_run_result: classifies a /actions/runs/{id} response ───────
    print(
        "\n=== (am) #603 parse_run_result: in_progress/all_passed/Blocked/Infra classification ==="
    )

    check(
        ci_monitor.parse_run_result({"status": "queued", "conclusion": None}) == "in_progress",
        "queued run maps to in_progress",
        "expected in_progress for a queued run",
    )
    check(
        ci_monitor.parse_run_result({"status": "in_progress", "conclusion": None}) == "in_progress",
        "in_progress run maps to in_progress",
        "expected in_progress for an in_progress run",
    )
    check(
        ci_monitor.parse_run_result({"status": "completed", "conclusion": "success"})
        == "all_passed",
        "completed+success maps to all_passed",
        "expected all_passed for a completed/success run",
    )
    for concl in ("failure", "action_required"):
        check(
            ci_monitor.parse_run_result({"status": "completed", "conclusion": concl}) == "Blocked",
            "completed+%s maps to Blocked" % concl,
            "expected Blocked for completed/%s" % concl,
        )
    for concl in ("cancelled", "timed_out", "stale", "startup_failure"):
        check(
            ci_monitor.parse_run_result({"status": "completed", "conclusion": concl}) == "Infra",
            "completed+%s maps to Infra" % concl,
            "expected Infra for completed/%s" % concl,
        )
    check(
        ci_monitor.parse_run_result({"status": "completed", "conclusion": "neutral"})
        == "all_passed",
        "an unrecognized completed conclusion (e.g. neutral) falls through to all_passed,"
        " mirroring parse_check_result's default",
        "expected all_passed fallback for an unrecognized completed conclusion",
    )

    # ── (an) #603 parse_commit_sha: top-level sha from /commits/{ref} ──────────────
    print("\n=== (an) #603 parse_commit_sha: reads the top-level 'sha' field ===")

    check(
        ci_monitor.parse_commit_sha({"sha": "abc123", "commit": {}}) == "abc123",
        "parse_commit_sha reads the top-level sha",
        "expected 'abc123'",
    )
    check(
        ci_monitor.parse_commit_sha({}) == "",
        "parse_commit_sha returns '' when sha is absent",
        "expected ''",
    )
    check(
        ci_monitor.parse_commit_sha({"head": {"sha": "nested-only"}}) == "",
        "parse_commit_sha does not read the nested head.sha shape (that's parse_pr_sha's job)",
        "parse_commit_sha should not read a PR-shaped head.sha",
    )

    # ── (ao) #603 argparse: exactly one of --pr/--sha/--run-id/--branch required ───
    print("\n=== (ao) #603 argparse: mutually exclusive identifier group ===")

    with unittest.mock.patch("sys.stderr", new=io.StringIO()):
        try:
            ci_monitor.main(["ci_monitor.py"])
            _fail("main() with no identifier flag should exit, but returned normally")
        except SystemExit as e:
            check(
                e.code == 2,
                "supplying none of --pr/--sha/--run-id/--branch exits with code 2",
                "expected exit code 2, got %r" % e.code,
            )

    with unittest.mock.patch("sys.stderr", new=io.StringIO()):
        try:
            ci_monitor.main(["ci_monitor.py", "--pr", "1", "--sha", "deadbeef"])
            _fail("main() with two identifier flags should exit, but returned normally")
        except SystemExit as e:
            check(
                e.code == 2,
                "supplying two identifier flags (--pr and --sha) exits with code 2",
                "expected exit code 2, got %r" % e.code,
            )
    # Each flag being independently accepted is demonstrated by (ap)/(aq)/(ar)
    # below, each of which drives main() to a terminal line via exactly one of
    # --sha/--branch/--run-id.

    # ── (ap) #603 main(): --sha mode Clear and Blocked, no PR involved ─────────────
    print("\n=== (ap) #603 main(): --sha mode Clear path (no mergeable_state fetch) ===")

    SHA_AP = "d34db33fcafe"
    tag_ap = "SHA#%s" % SHA_AP[:7]
    CHECK_PASS_AP = {
        "total_count": 1,
        "check_runs": [{"name": "build-and-test", "status": "completed", "conclusion": "success"}],
    }

    def fake_request_ap_clear(url, token, raw=False):
        return CHECK_PASS_AP

    buf_ap1 = io.StringIO()
    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_ap_clear),
        unittest.mock.patch("sys.stdout", new=buf_ap1),
    ):
        rc_ap1 = ci_monitor.main(["ci_monitor.py", "--sha", SHA_AP])

    lines_ap1 = buf_ap1.getvalue().splitlines()
    check(
        ("%s: Clear" % tag_ap) in lines_ap1,
        "sha mode emits '%s: Clear' with no mergeable_state suffix" % tag_ap,
        "expected a bare Clear line; output: %r" % lines_ap1,
    )
    check(
        not any("mergeable_state" in ln for ln in lines_ap1),
        "sha mode never fetches or mentions mergeable_state",
        "unexpected mergeable_state reference; output: %r" % lines_ap1,
    )
    check(rc_ap1 == 0, "main() returned 0", "main() returned %r" % rc_ap1)

    print("\n=== (ap) #603 main(): --sha mode Blocked path, attributed by check name ===")

    CHECK_BLOCKED_AP = {
        "total_count": 1,
        "check_runs": [{"name": "build-and-test", "status": "completed", "conclusion": "failure"}],
    }
    # 1 verdict poll + DRAIN_MAX_ATTEMPTS drain self-fetches of check-runs (no
    # github-actions-shaped check run is present, so parse_actions_targets finds
    # no jobs/artifacts to fetch on any of them).
    side_effects_ap2 = collections.deque([CHECK_BLOCKED_AP] * (1 + ci_monitor.DRAIN_MAX_ATTEMPTS))

    def fake_request_ap_blocked(url, token, raw=False):
        return side_effects_ap2.popleft()

    buf_ap2 = io.StringIO()
    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_ap_blocked),
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None),
        unittest.mock.patch("sys.stdout", new=buf_ap2),
    ):
        rc_ap2 = ci_monitor.main(["ci_monitor.py", "--sha", SHA_AP])

    lines_ap2 = buf_ap2.getvalue().splitlines()
    check(
        ("%s: Blocked by: build-and-test" % tag_ap) in lines_ap2,
        "sha mode Blocked terminal attributes the failing check by name",
        "expected an attributed Blocked line; output: %r" % lines_ap2,
    )
    check(
        len(side_effects_ap2) == 0,
        "all mocked check-runs requests consumed (1 poll + %d drain attempts)"
        % ci_monitor.DRAIN_MAX_ATTEMPTS,
        "request deque not drained; %d entries left" % len(side_effects_ap2),
    )
    check(rc_ap2 == 0, "main() returned 0", "main() returned %r" % rc_ap2)

    # ── (aq) #603 main(): --branch mode re-resolves the head SHA each poll ─────────
    print(
        "\n=== (aq) #603 main(): --branch mode Clear path, sha resolved via /commits/{branch} ==="
    )

    BRANCH_AQ = "feature/x"
    tag_aq = "BRANCH#%s" % BRANCH_AQ
    COMMIT_AQ = {"sha": "0ff1ceb0a7d0"}
    CHECK_PASS_AQ = {
        "total_count": 1,
        "check_runs": [{"name": "build-and-test", "status": "completed", "conclusion": "success"}],
    }
    side_effects_aq1 = collections.deque([COMMIT_AQ, CHECK_PASS_AQ])

    def fake_request_aq1(url, token, raw=False):
        return side_effects_aq1.popleft()

    buf_aq1 = io.StringIO()
    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_aq1),
        unittest.mock.patch("sys.stdout", new=buf_aq1),
    ):
        rc_aq1 = ci_monitor.main(["ci_monitor.py", "--branch", BRANCH_AQ])

    lines_aq1 = buf_aq1.getvalue().splitlines()
    check(
        ("%s: Clear" % tag_aq) in lines_aq1,
        "branch mode emits '%s: Clear'" % tag_aq,
        "expected a Clear line; output: %r" % lines_aq1,
    )
    check(
        len(side_effects_aq1) == 0,
        "both the commit-resolution and check-runs requests were consumed",
        "request deque not drained; %d entries left" % len(side_effects_aq1),
    )
    check(rc_aq1 == 0, "main() returned 0", "main() returned %r" % rc_aq1)

    print("\n=== (aq) #603 main(): --branch mode Blocked path ===")

    CHECK_BLOCKED_AQ = {
        "total_count": 1,
        "check_runs": [{"name": "build-and-test", "status": "completed", "conclusion": "failure"}],
    }
    # Commit resolution + 1 verdict poll + DRAIN_MAX_ATTEMPTS drain self-fetches of
    # check-runs (the drain re-polls check-runs directly by sha, not via the
    # branch, so no further /commits/{branch} request is issued).
    side_effects_aq2 = collections.deque(
        [COMMIT_AQ, CHECK_BLOCKED_AQ] + [CHECK_BLOCKED_AQ] * ci_monitor.DRAIN_MAX_ATTEMPTS
    )

    def fake_request_aq2(url, token, raw=False):
        return side_effects_aq2.popleft()

    buf_aq2 = io.StringIO()
    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_aq2),
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None),
        unittest.mock.patch("sys.stdout", new=buf_aq2),
    ):
        rc_aq2 = ci_monitor.main(["ci_monitor.py", "--branch", BRANCH_AQ])

    lines_aq2 = buf_aq2.getvalue().splitlines()
    check(
        ("%s: Blocked by: build-and-test" % tag_aq) in lines_aq2,
        "branch mode Blocked terminal attributes the failing check by name",
        "expected an attributed Blocked line; output: %r" % lines_aq2,
    )
    check(
        len(side_effects_aq2) == 0,
        "all mocked requests consumed (commit resolve + verdict + %d drain attempts)"
        % ci_monitor.DRAIN_MAX_ATTEMPTS,
        "request deque not drained; %d entries left" % len(side_effects_aq2),
    )
    check(rc_aq2 == 0, "main() returned 0", "main() returned %r" % rc_aq2)

    # ── (ar) #603 main(): --run-id mode scopes to the run object itself ────────────
    print(
        "\n=== (ar) #603 main(): --run-id mode Clear path (never fetches check-runs) ==="
    )

    RUN_ID_AR = "778899"
    tag_ar = "RUN#%s" % RUN_ID_AR
    RUN_SUCCESS_AR = {"status": "completed", "conclusion": "success", "head_sha": "5eed1234"}

    def fake_request_ar1(url, token, raw=False):
        check(
            "check-runs" not in url,
            "run-id mode never fetches /commits/{sha}/check-runs",
            "unexpected check-runs fetch: %s" % url,
        )
        return RUN_SUCCESS_AR

    buf_ar1 = io.StringIO()
    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_ar1),
        unittest.mock.patch("sys.stdout", new=buf_ar1),
    ):
        rc_ar1 = ci_monitor.main(["ci_monitor.py", "--run-id", RUN_ID_AR])

    lines_ar1 = buf_ar1.getvalue().splitlines()
    check(
        ("%s: Clear" % tag_ar) in lines_ar1,
        "run-id mode emits '%s: Clear'" % tag_ar,
        "expected a Clear line; output: %r" % lines_ar1,
    )
    check(rc_ar1 == 0, "main() returned 0", "main() returned %r" % rc_ar1)

    print(
        "\n=== (ar) #603 main(): --run-id mode Blocked path, scoped to this run's jobs only ==="
    )

    RUN_FAILURE_AR = {"status": "completed", "conclusion": "failure", "head_sha": "5eed1234"}
    JOBS_AR = {
        "jobs": [
            {
                "id": 1,
                "name": "build-and-test",
                "steps": [
                    {
                        "number": 4,
                        "name": "Build and run unit tests",
                        "status": "completed",
                        "conclusion": "failure",
                    }
                ],
            }
        ]
    }
    ARTS_EMPTY_AR = {"artifacts": []}
    # 1 run fetch + 1 poll_signals (jobs+artifacts) + DRAIN_MAX_ATTEMPTS drain
    # attempts, each just jobs+artifacts (explicit_targets skips any check-runs
    # or run re-fetch during the drain).
    side_effects_ar2 = collections.deque(
        [RUN_FAILURE_AR, JOBS_AR, ARTS_EMPTY_AR]
        + [JOBS_AR, ARTS_EMPTY_AR] * ci_monitor.DRAIN_MAX_ATTEMPTS
    )

    def fake_request_ar2(url, token, raw=False):
        check(
            "check-runs" not in url,
            "run-id mode's drain never fetches check-runs either",
            "unexpected check-runs fetch during drain: %s" % url,
        )
        return side_effects_ar2.popleft()

    buf_ar2 = io.StringIO()
    with (
        unittest.mock.patch.object(ci_monitor, "_request", side_effect=fake_request_ar2),
        unittest.mock.patch.object(ci_monitor.time, "sleep", return_value=None),
        unittest.mock.patch("sys.stdout", new=buf_ar2),
    ):
        rc_ar2 = ci_monitor.main(["ci_monitor.py", "--run-id", RUN_ID_AR])

    lines_ar2 = buf_ar2.getvalue().splitlines()
    check(
        ('%s: step "Build and run unit tests" -> failure' % tag_ar) in lines_ar2,
        "run-id mode surfaces the failing step from its own run's jobs",
        "expected a step failure line; output: %r" % lines_ar2,
    )
    check(
        ("%s: Blocked" % tag_ar) in lines_ar2,
        "run-id mode emits a bare Blocked terminal (no check-run summary to attribute)",
        "expected a Blocked line; output: %r" % lines_ar2,
    )
    check(
        len(side_effects_ar2) == 0,
        "all mocked requests consumed (run fetch + jobs/artifacts + %d drain attempts)"
        % ci_monitor.DRAIN_MAX_ATTEMPTS,
        "request deque not drained; %d entries left" % len(side_effects_ar2),
    )
    check(rc_ar2 == 0, "main() returned 0", "main() returned %r" % rc_ar2)

    # ── (as) #603 regression: fetch_pr_with_retry delegates to fetch_with_retry; ───
    # --pr's output format is unchanged by the multi-mode refactor
    print(
        "\n=== (as) #603 regression: fetch_pr_with_retry delegates to fetch_with_retry ==="
    )

    delegate_calls_as = []

    def _spy_fetch_with_retry_as(url, token, attempts=3, base_delay=2):
        delegate_calls_as.append((url, attempts, base_delay))
        return {"head": {"sha": "beefcafe"}}

    with unittest.mock.patch.object(
        ci_monitor, "fetch_with_retry", side_effect=_spy_fetch_with_retry_as
    ):
        got_as = ci_monitor.fetch_pr_with_retry("999", "tok")
    check(
        got_as == {"head": {"sha": "beefcafe"}},
        "fetch_pr_with_retry returns fetch_with_retry's result",
        "expected the delegate's return value; got %r" % got_as,
    )
    expected_url_as = "%s/repos/%s/%s/pulls/999" % (ci_monitor.API_BASE, OWNER_T, REPO_T)
    check(
        len(delegate_calls_as) == 1 and delegate_calls_as[0][0] == expected_url_as,
        "fetch_pr_with_retry builds the same /pulls/{n} URL as before and calls"
        " fetch_with_retry exactly once",
        "unexpected delegate call(s): %r" % delegate_calls_as,
    )
    # The PR-mode tests above ((i),(j),(k),(m),(n),(p),(q),(r),(s),(t),(u),(v),(w),
    # (x),(y),(z),(aa)-(ak)) all still assert exact 'PR#N: ...' lines byte-for-byte;
    # their continued passing after this multi-mode refactor (issue #603) is itself
    # the regression check that --pr's output shape is unchanged.

    # ── Summary ────────────────────────────────────────────────────────────────────
    print("\nResults: %d passed, %d failed." % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
