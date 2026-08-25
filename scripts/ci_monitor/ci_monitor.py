#!/usr/bin/env python3
"""ci_monitor.py: Poll a PR's CI and stream a terminal outcome plus per-test signals.

Invoked by the Orchestrator's Monitor tool call (see agents/dev_orchestration.md).
Each stdout line is consumed as a task-notification event, so output is the
interface: terminal outcome lines end the loop, while informational lines
(in_progress heartbeat, per-step deltas, per-test FAILs) keep it alive.

See scripts/ci_monitor/README.md for full usage instructions, including the
command-line arguments, per-outcome filter flags, and the outcome vocabulary.

Usage:
    python3 scripts/ci_monitor/ci_monitor.py --pr <PR_NUMBER> [filter flags]
    python3 scripts/ci_monitor/ci_monitor.py --sha <SHA> [filter flags]
    python3 scripts/ci_monitor/ci_monitor.py --run-id <RUN_ID> [filter flags]
    python3 scripts/ci_monitor/ci_monitor.py --branch <BRANCH> [filter flags]

Exactly one of --pr/--sha/--run-id/--branch is required. --sha, --run-id, and
--branch have no PR to consult, so their `Clear` terminal fires directly off
an all-passed check verdict--no `mergeable_state` gating (that concept is
--pr-only). See scripts/ci_monitor/README.md for the per-mode output prefixes
(PR#/SHA#/RUN#/BRANCH#).

Environment:
    GITHUB_TOKEN  GitHub token used for the REST calls (required).

NOTE on error handling: the poll loop must survive transient REST/parse
failures. HTTP and JSON errors are caught per-call and treated as "no data this
poll" rather than aborting the resilient loop. The 30-minute escalation
threshold is enforced by `timeout_ms` on the Monitor call, not here.
"""

import argparse
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

OWNER = "aunger"
REPO = "gallery-button-for-pixel-camera"
API_BASE = "https://api.github.com"

# Configurable behavior (issue #500). Each tunable is a regex with an in-code
# default; the committed scripts/ci_monitor/ci_monitor.config.json overrides the
# defaults with this repo's specifics. load_config() reads that file, falling
# back to these defaults when the file is absent, unreadable, invalid, or missing
# a key, so the resilient poll loop never aborts on configuration.

# Match (re.search) against an artifact's `name` to decide whether it carries
# per-test ndjson markers worth downloading. Preserves the historical
# testresults-* contract by default.
DEFAULT_ARTIFACT_NAME_REGEX = r"^testresults-"

# Match (re.search) against a step's `name` to decide whether to surface a
# `step "..." -> ...` line on a non-failing conclusion. The never-match default
# keeps the generic, repo-agnostic rule "a step is interesting if it failed"
# (the genuine-failure clause in parse_steps is unconditional); named-step
# reporting on success is project-specific and supplied via the config file.
DEFAULT_INTERESTING_STEP_REGEX = r"(?!)"

# Locate the per-test marker prefix in a raw ndjson line. The JSON payload begins
# at the end of the matched span (re.search(...).end()), which is correct for any
# marker the regex matches. Default switched to ##TEST## per issue #500; this
# repo's config matches both markers for back-compat (see ci_monitor.config.json).
DEFAULT_TEST_MARKER_REGEX = r"##TEST##"

# Match (re.search) against a step's `name` to identify it as a deferred-verdict
# step: one whose own GitHub Actions `conclusion` is always `success` because the
# workflow wraps its test invocation in an if/else that records the real outcome
# elsewhere and always exits 0 (issue #309). Its `success` conclusion is not a
# test verdict, so parse_steps annotates its success line "(verdict deferred to
# Gate)" instead of letting it read like a pass. The never-match default keeps the
# rule repo-agnostic (mirroring `interesting_step_regex`); the project-specific
# names are supplied via config. Kept distinct from `interesting_step_regex`
# because not every named-on-success step is deferred-verdict (e.g. this repo's
# honest `Build and run unit tests` step is surfaced but must not be annotated).
DEFAULT_DEFERRED_VERDICT_STEP_REGEX = r"(?!)"

# Match (re.search) against a check-run's `name` to identify it as a process-label
# gate rather than a substantive code/test block. The never-match default keeps the
# rule repo-agnostic; the project-specific name is supplied via config, mirroring
# how `interesting_step_regex` defaults to never-match.
DEFAULT_LABEL_GATE_CHECK_REGEX = r"(?!)"


def load_config(path=None):
    """Load the CI Monitor config, falling back to in-code defaults.

    Returns a dict with keys artifact_name_regex, interesting_step_regex,
    deferred_verdict_step_regex, test_marker_regex, and label_gate_check_regex.
    A missing file, unreadable file, or invalid JSON falls back entirely to the
    DEFAULT_* regexes (the Monitor must never abort on config). Each key
    independently defaults if absent, and a value that does not compile as a
    regex falls back to that key's default rather than crashing.
    """
    defaults = {
        "artifact_name_regex": DEFAULT_ARTIFACT_NAME_REGEX,
        "interesting_step_regex": DEFAULT_INTERESTING_STEP_REGEX,
        "deferred_verdict_step_regex": DEFAULT_DEFERRED_VERDICT_STEP_REGEX,
        "test_marker_regex": DEFAULT_TEST_MARKER_REGEX,
        "label_gate_check_regex": DEFAULT_LABEL_GATE_CHECK_REGEX,
    }
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ci_monitor.config.json")
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as e:
        sys.stderr.write("config load failed (%s): %s; using defaults\n" % (path, e))
        sys.stderr.flush()
        return dict(defaults)

    cfg = {}
    for key, default in defaults.items():
        value = raw.get(key, default)
        if not isinstance(value, str):
            sys.stderr.write("config key %s is not a string; using default\n" % key)
            sys.stderr.flush()
            value = default
        try:
            re.compile(value)
        except re.error as e:
            sys.stderr.write("config key %s is not a valid regex (%s); using default\n" % (key, e))
            sys.stderr.flush()
            value = default
        cfg[key] = value
    return cfg


# Suppress repeated informational lines (in_progress heartbeat, "could not fetch
# SHA", "still computing") until this many seconds have elapsed with no output of
# any kind, so a quiet poll loop stays quiet. Every emitted line resets the timer.
SILENCE_SECONDS = 120

# Gap E (issue #402)--before emitting a Blocked/Infra terminal line, re-poll
# the step/artifact signals a few more times. /actions/runs/{id}/jobs and
# /actions/runs/{id}/artifacts can lag behind /commits/{sha}/check-runs: the
# poll where check-runs first reports the failing conclusion may still show the
# final "Gate on test failures" step as not-yet-completed, or the
# testresults-<group> artifact as not-yet-listed. These extra drain polls give
# those endpoints a chance to catch up before the loop ends, so a Blocked
# terminal is not reported with zero diagnostic step/FAIL lines.
#
# DRAIN_DELAY_SECONDS is the pause before each drain poll. DRAIN_MAX_ATTEMPTS
# bounds how many times we retry. Every attempt runs (the drain does not stop
# at the first fruitful one): the two lagging endpoints can settle at different
# times (issue #419), e.g. the gate step appears on attempt 1 while the
# testresults-<group> artifact only lists on attempt 2, so stopping after the
# first attempt that emits anything would drop the later signal for this
# process's lifetime. At 5s per attempt, 3 attempts cover up to 15s of lag
# (issue #402 Runs B/C/E/F/T needed only one); if the lag outlives that
# (Run G's multi-process, multi-minute shape), the drain gives up and
# `drain_then_print` says so explicitly rather than printing a bare terminal
# line.
DRAIN_DELAY_SECONDS = 5
DRAIN_MAX_ATTEMPTS = 3


# Conclusion buckets shared by all three verdict classifiers (parse_check_result,
# parse_check_summary's _BLOCKING_CONCLUSIONS, and parse_run_result). Hoisted to
# module level (issue #603) so the third classifier does not re-duplicate them.
_INFRA_CONCLUSIONS = frozenset(("cancelled", "timed_out", "stale", "startup_failure"))
_BLOCKED_CONCLUSIONS = frozenset(("failure", "action_required"))


# Parsers----------------------------------------------------------------------


def parse_steps(
    jobs_json,
    seen,
    job_ids=None,
    interesting_step_regex=DEFAULT_INTERESTING_STEP_REGEX,
    deferred_verdict_step_regex=DEFAULT_DEFERRED_VERDICT_STEP_REGEX,
    failed_steps=None,
):
    """Emit step-delta lines for the tracked CI job(s).

    Reads parsed jobs JSON (a dict, as from /actions/runs/{id}/jobs) and a
    `seen` set of already-reported "<job id>#<step number>" keys (mutated in
    place; step numbers are per-job, so the job id keeps them distinct across
    multiple jobs/runs). When
    `job_ids` is a set, only jobs whose id is in it are considered; `job_ids` of
    None applies no job filter. Emits a line when a step reaches 'completed' if
    its name matches `interesting_step_regex` or `deferred_verdict_step_regex`
    (on any conclusion) or it genuinely failed; successful setup steps and
    skipped conditional steps are otherwise suppressed. Returns a list of
    step-delta lines.

    The genuine-failure clause is unconditional, so a failing step always
    surfaces regardless of the regexes.

    A step whose name matches `deferred_verdict_step_regex` and concluded
    'success' has its line annotated "(verdict deferred to Gate)": these steps
    always exit 0 by design (issue #309), so their `success` conclusion is not a
    test verdict and must not read like a pass. A deferred-verdict step that
    genuinely failed is not annotated (a real failure is a real failure).

    When `failed_steps` is a list, each genuinely-failed step's (name,
    conclusion) tuple is appended to it (deduped via `seen`, so a step is
    recorded at most once), letting a caller attribute a Blocked terminal to the
    specific failing step (e.g. "Gate on test failures").
    """
    out = []
    for j in jobs_json.get("jobs", []):
        if job_ids is not None and str(j.get("id")) not in job_ids:
            continue
        for s in j.get("steps", []):
            if s.get("status") != "completed":
                continue
            # Dedup by job id + step number: step numbers are per-job, so across
            # multiple jobs/runs (issue #500) a bare number would collide.
            key = "%s#%s" % (j.get("id"), s.get("number"))
            if key in seen:
                continue
            seen.add(key)
            name = s.get("name", "?")
            concl = s.get("conclusion") or "?"
            genuine_failure = concl in (
                "failure",
                "cancelled",
                "timed_out",
                "action_required",
            )
            deferred = bool(re.search(deferred_verdict_step_regex, name))
            if re.search(interesting_step_regex, name) or deferred or genuine_failure:
                if genuine_failure and failed_steps is not None:
                    failed_steps.append((name, concl))
                if deferred and concl == "success":
                    out.append('step "%s" -> %s (verdict deferred to Gate)' % (name, concl))
                else:
                    out.append('step "%s" -> %s' % (name, concl))
    return out


def parse_fails(
    lines,
    seen,
    outcome_filters=None,
    test_marker_regex=DEFAULT_TEST_MARKER_REGEX,
    failed_tests=None,
):
    """Emit FAIL/PASS/SKIP lines from per-test ndjson markers.

    `lines` is an iterable of raw text lines; `seen` is a set of already-reported
    suite#name#outcome keys (mutated in place). Returns a list of output lines, each
    possibly carrying an indented (truncated) trace. Deduped across calls by
    suite#name#outcome.

    `test_marker_regex` locates the marker that prefixes each test's JSON payload.
    The payload is parsed from the end of the matched span (re.search(...).end()),
    which keeps the offset correct for any marker the regex matches, including a
    multi-alternative regex like `##GB4PC_TEST##|##TEST##`. A line whose marker
    the regex does not match at all is skipped.

    `outcome_filters` is a dict mapping outcome names ('FAIL', 'PASS', 'SKIP') to
    (enabled, pattern) tuples, where `enabled` is a bool and `pattern` is either
    None (match all) or a regex string. A non-None pattern matches a marker when
    it matches either the marker's `name` or its `suite` field (issue #646), so a
    suite-shaped query like 'SetupActivityPermissionDialog' surfaces a marker
    whose distinguishing text lives in `suite` rather than `name`.
    Default behavior (outcome_filters=None): report all FAIL, all SKIP, no PASS.

    When `failed_tests` is a list, each emitted FAIL's "[suite] name" identifier
    is appended to it (deduped via `seen`), letting a caller attribute a Blocked
    terminal to the specific failing suite/test.
    """
    if outcome_filters is None:
        outcome_filters = {
            "FAIL": (True, None),
            "SKIP": (True, None),
            "PASS": (False, None),
        }

    out = []
    for raw in lines:
        marker = re.search(test_marker_regex, raw)
        if marker is None:
            continue
        try:
            m = json.loads(raw[marker.end() :].strip())
        except Exception:
            continue
        outcome = m.get("outcome", "")
        if outcome not in outcome_filters:
            continue
        enabled, pattern = outcome_filters[outcome]
        if not enabled:
            continue
        # Pattern is matched against the marker's `name` or `suite` field
        # (issue #646): a suite-shaped query surfaces a marker whose
        # distinguishing text lives in `suite` rather than `name`.
        name = m.get("name", "")
        suite = m.get("suite", "")
        if pattern is not None and not (re.search(pattern, name) or re.search(pattern, suite)):
            continue
        key = suite + "#" + name + "#" + outcome
        if key in seen:
            continue
        seen.add(key)
        if outcome == "FAIL" and failed_tests is not None:
            failed_tests.append("[%s] %s" % (m.get("suite", "?"), name or "?"))
        msg = (m.get("msg") or "").strip()
        tr = (m.get("trace") or "").strip()
        if len(tr) > 800:
            tr = tr[:800] + " ...(truncated)"
        line = "%s [%s] %s: %s" % (outcome, m.get("suite", "?"), name or "?", msg)
        if tr:
            line += "\n  " + tr.replace("\n", "\n  ")
        out.append(line)
    return out


def parse_pr_sha(pr_json):
    """Return the head SHA from a /pulls/{n} response, or '' if absent."""
    return pr_json.get("head", {}).get("sha", "")


def parse_pr_terminal(pr_json):
    """Map a /pulls/{n} response to a terminal line for a done PR, or '' if open.

    GitHub leaves `mergeable_state` at "unknown" indefinitely once a PR is
    closed or merged, which would otherwise spin the poll loop until timeout.
    Returns 'Merged' when the PR was merged, 'Closed' when it was closed without
    merging, or '' when the PR is still open and should keep being polled. A
    response missing both fields (e.g. a minimal mock) is treated as open.
    """
    if pr_json.get("merged"):
        return "Merged"
    if pr_json.get("state") == "closed":
        return "Closed"
    return ""


def pr_is_draft(pr_json):
    """Return True when a /pulls/{n} response describes a draft pull request.

    Reads the `draft` boolean GitHub states outright rather than inferring
    draftness from `mergeable_state == "draft"` (issue #968). `mergeable_state`
    is one value drawn from a ladder whose precedence order GitHub does not
    document, so a draft PR that is also conflicted, behind, or waiting on a
    required check can report `dirty`/`behind`/`blocked` instead, and its
    draftness would go unseen--turning a held PR into a false `Blocked` or a
    false `Infra` escalation. The boolean cannot be crowded out that way.

    A response missing the field (e.g. a minimal mock, or a payload GitHub
    truncated) reads as not-draft; callers pair this with the `draft`
    mergeable_state as a corroborating fallback.
    """
    return bool(pr_json.get("draft"))


def _check_run_recency_key(run):
    """Return a sort key ordering check runs oldest-to-newest by check-run id.

    GitHub attaches several check runs with the same `name` to one commit when a
    workflow re-runs (e.g. each PR-side label add/remove re-triggers the
    label-gate workflow), so that gate's check-run name accumulates several
    entries against the same head commit, of which only the most recent is
    authoritative (issue #707). A re-run always creates a new check-run row with
    a higher `id`, and--unlike `started_at`, which GitHub leaves null until a run
    actually starts (issue #719)--the `id` is assigned at creation, so a
    freshly-queued re-run already outranks the completed run it supersedes.
    Sorting by `id` alone keeps the latest attempt for both completed re-runs
    (#707) and not-yet-started ones (#719), with no null-handling and no
    status special-casing. A run with an unusable id sorts oldest.
    """
    try:
        return int(run.get("id"))
    except (TypeError, ValueError):
        return -1


def latest_check_runs(check_json):
    """Collapse same-named check runs to the latest one each (issues #707, #719).

    Returns a shallow copy of `check_json` whose `check_runs` list keeps, for
    each distinct non-empty check-run `name`, only the most recent run (the
    highest check-run `id`, via `_check_run_recency_key`); the surviving run sits
    at the position of that name's first appearance, so the summary's row order
    is otherwise preserved. Real check-run ids are unique, so in practice the
    `>=` winner test below never ties; the one exception is two runs whose ids
    are both unusable (each keyed -1), where `>=` keeps the later of the two in
    list order, which is harmless since neither carries real recency.
    Runs with no usable `name` are passed through unchanged (each kept), since
    name-based identity does not apply to them and GitHub always names its own
    check runs; this also leaves the unnamed-Actions-run fixtures other tests
    rely on untouched.

    This mirrors GitHub's own `mergeable_state`, which judges a required check by
    its latest run per name: without it, a stale `failure` from an earlier re-run
    of a named check (e.g. a label-gate check that briefly saw a blocking label,
    since removed) would outvote the authoritative later `success` and drive a
    spurious `Blocked` terminal. Feeding the collapsed payload to the verdict,
    the summary, and the Actions-target discovery keeps all three from latching
    onto a superseded run. `total_count` is left as-is:
    only its zero/non-zero distinction is load-bearing (parse_check_result's
    "no checks registered" case), and collapsing never empties a non-empty list.
    """
    runs = check_json.get("check_runs", [])
    winners = {}
    for r in runs:
        name = r.get("name")
        if not name:
            continue
        current = winners.get(name)
        if current is None or _check_run_recency_key(r) >= _check_run_recency_key(current):
            winners[name] = r
    kept = []
    emitted = set()
    for r in runs:
        name = r.get("name")
        if not name:
            kept.append(r)
            continue
        if name in emitted:
            continue
        emitted.add(name)
        kept.append(winners[name])
    result = dict(check_json)
    result["check_runs"] = kept
    return result


def parse_check_result(check_json):
    """Map a /commits/{sha}/check-runs response to an overall result token.

    Returns one of: 'Clear', 'in_progress', 'Infra', 'Blocked', 'all_passed'.
    """
    runs = check_json.get("check_runs", [])
    total = check_json.get("total_count", 0)
    if total == 0:
        return "Clear"
    statuses = [r["status"] for r in runs]
    conclusions = [r.get("conclusion", "") for r in runs if r["status"] == "completed"]
    if any(s in ("in_progress", "queued") for s in statuses):
        return "in_progress"
    if all(s == "completed" for s in statuses):
        if any(c in _INFRA_CONCLUSIONS for c in conclusions):
            return "Infra"
        if any(c in _BLOCKED_CONCLUSIONS for c in conclusions):
            return "Blocked"
        return "all_passed"
    return "in_progress"


def parse_check_summary(check_json, label_gate_check_regex=DEFAULT_LABEL_GATE_CHECK_REGEX):
    """Extract per-check (name, conclusion, blocking, label_gate, run_id) rows.

    Returns a list of dicts (one per check run, preserving order):
      {"name": str, "conclusion": str, "blocking": bool, "label_gate": bool,
       "run_id": str | None}

    Conclusions in the "blocking" set match what parse_check_result treats as
    Blocked/Infra. The label_gate_check_regex is matched (re.search) against
    each check run's name; a True label_gate lets the consumer annotate a
    process-label block distinctly from a substantive code/test failure.

    `run_id` is the GitHub Actions workflow run the check came from
    (via _actions_run_id), or None for a non-Actions check; it lets the
    formatter annotate each row with the run that produced it (issue #720),
    which after the same-named collapse (issue #707) names exactly which run
    survived for that check name. It is inert on the verdict path--only the
    formatter renders it.

    Returns [] when check_runs is absent or empty. Does not make any HTTP
    requests; reads only from the already-fetched check_json payload.
    """
    _BLOCKING_CONCLUSIONS = _BLOCKED_CONCLUSIONS | _INFRA_CONCLUSIONS
    rows = []
    for r in check_json.get("check_runs", []):
        name = r.get("name") or "?"
        status = r.get("status", "")
        conclusion = r.get("conclusion") or ""
        # For completed checks use the conclusion; otherwise use the in-progress status
        # so the summary renders e.g. "in_progress" rather than a blank slot.
        effective = conclusion if status == "completed" else status
        blocking = status == "completed" and conclusion in _BLOCKING_CONCLUSIONS
        label_gate = bool(re.search(label_gate_check_regex, name))
        rows.append(
            {
                "name": name,
                "conclusion": effective,
                "blocking": blocking,
                "label_gate": label_gate,
                "run_id": _actions_run_id(r),
            }
        )
    return rows


def format_check_summary(rows):
    """Format per-check rows into a summary block (without the PR#N: prefix).

    Returns [] when rows is empty. The first line is "summary", followed by one
    aligned dotted line per check. Blocking rows carry [BLOCKING]; a label-gate
    blocking row additionally carries [label gate]. A row that carries a
    non-None `run_id` (a GitHub Actions check) ends with a `[run <id>]` token
    naming the workflow run it came from (issue #720); non-Actions rows omit it.
    The token rides after the [BLOCKING]/[label gate] cluster, outside the dotted
    column, so the existing alignment is unchanged. Column width is capped at 60
    characters to avoid pathological output on long check names.
    """
    if not rows:
        return []
    _MAX_COL = 60
    name_width = min(max(len(r["name"]) for r in rows), _MAX_COL)
    lines = ["summary"]
    for r in rows:
        name = r["name"]
        conclusion = r["conclusion"]
        # Truncate long names so the dotfill stays within the column ceiling.
        display_name = name[:_MAX_COL] if len(name) > _MAX_COL else name
        dots = "." * (name_width - len(display_name) + 4)
        line = "  %s %s %s" % (display_name, dots, conclusion)
        if r["blocking"]:
            line += "   [BLOCKING]"
            if r["label_gate"]:
                line += " [label gate]"
        # .get keeps the formatter tolerant of rows built without a run_id key
        # (e.g. manually constructed rows); a non-Actions check has run_id None.
        run_id = r.get("run_id")
        if run_id is not None:
            line += " [run %s]" % run_id
        lines.append(line)
    return lines


def blocking_suffix(rows, failed_steps=None, failed_tests=None):
    """Return the attributed terminal suffix for a set of per-check rows.

    Returns "" when no row is blocking (caller emits the bare terminal token).
    Returns " by: <names> [label gate]" when every blocking row is a label gate.
    Otherwise returns " by: <names>", enriched (issue #602) with the specific
    failing step(s) and test(s) that explain the block when they are known:

        " by: build-and-test (step \"Gate on test failures\" -> failure; test [suite] name)"

    `failed_steps` is a list of (name, conclusion) tuples for the genuinely-failed
    step(s) (collected by parse_steps); `failed_tests` is a list of "[suite] name"
    identifiers for the failing test(s) (collected by parse_fails). The check-run
    name alone (e.g. the single `build-and-test` job) does not say which step or
    test failed--the deferred-verdict E2E steps all conclude `success` and the
    real verdict is the `Gate on test failures` step--so this names the blocker at
    step/test granularity (asks #519 and #591, issue #602). The extra detail is
    added only for a substantive (non-label-gate) block; a label-gate-only block
    keeps its bare " by: <names> [label gate]" form (no test failed), and
    `[label gate]` stays the terminal suffix's final token so the Orchestrator's
    label-gate detection is unaffected.
    """
    blocking = [r for r in rows if r["blocking"]]
    if not blocking:
        return ""
    names = [r["name"] for r in blocking]
    suffix = " by: %s" % ", ".join(names)
    if all(r["label_gate"] for r in blocking):
        return suffix + " [label gate]"
    detail = []
    if failed_steps:
        detail.extend('step "%s" -> %s' % (name, concl) for name, concl in failed_steps)
    if failed_tests:
        detail.extend("test %s" % t for t in failed_tests)
    if detail:
        suffix += " (%s)" % "; ".join(detail)
    return suffix


def parse_run_result(run_json):
    """Map a /actions/runs/{run_id} response to an overall result token.

    Mirrors parse_check_result's classification, but reads status/conclusion
    directly off a single Actions run object (issue #603, --run-id mode)
    instead of a list of check-runs. Returns one of: 'in_progress', 'Infra',
    'Blocked', 'all_passed'. Unlike parse_check_result, there is no 'Clear'
    token here--a bare Actions run always exists once fetched, so the
    "no checks registered yet" case does not apply.

    Any non-'completed' status (not just 'queued'/'in_progress') maps to
    'in_progress': a single run object only has one status field, so there is
    no multi-check "some still running" case to distinguish, unlike
    parse_check_result's per-check-run list.
    """
    status = run_json.get("status", "")
    conclusion = run_json.get("conclusion") or ""
    if status != "completed":
        return "in_progress"
    if conclusion in _INFRA_CONCLUSIONS:
        return "Infra"
    if conclusion in _BLOCKED_CONCLUSIONS:
        return "Blocked"
    return "all_passed"


def parse_commit_sha(commit_json):
    """Return the top-level `sha` from a /commits/{ref} response, or '' if absent.

    Used by --branch mode (issue #603) to re-resolve the branch's head SHA
    every poll. Differs from parse_pr_sha, whose SHA is nested under
    `head.sha`: a /commits/{ref} response (ref may be a branch or SHA) carries
    the SHA at the top level.
    """
    return commit_json.get("sha", "")


# Extract the run id (and optional job id) from an Actions check run's URL.
_RUN_JOB_URL_RE = re.compile(r"/actions/runs/(\d+)(?:/job/(\d+))?")


def _actions_run_job(check_run):
    """Return the (run_id, job_id) for a GitHub Actions check run, or (None, None).

    A check run is treated as a GitHub Actions run when `app.slug ==
    "github-actions"`; when the `app` block is missing, it falls back to "the
    `details_url` (or `html_url`) matched /actions/runs/". Returns the run id (as
    a string) paired with the job id (a string, or None when the URL carries only
    a run id), or (None, None) for a non-Actions check or a URL from which no run
    id can be parsed.

    Single source of the Actions-detection and URL-parsing logic (issue #720):
    parse_actions_targets needs both ids, while _actions_run_id (used by the
    per-check summary) needs only the run id, so both read from this one parse
    rather than each re-deriving it.
    """
    app = check_run.get("app")
    if isinstance(app, dict) and app.get("slug") != "github-actions":
        return (None, None)
    # No app block: fall back to recognizing the URL shape.
    url = check_run.get("details_url") or check_run.get("html_url") or ""
    match = _RUN_JOB_URL_RE.search(url)
    if not match:
        return (None, None)
    return (match.group(1), match.group(2))


def _actions_run_id(check_run):
    """Return the GitHub Actions workflow run id for a check run, or None.

    Thin accessor over _actions_run_job for callers that need only the run id
    (the per-check summary annotation, issue #720). Returns None for a
    non-Actions check or a URL from which no run id can be parsed.
    """
    return _actions_run_job(check_run)[0]


def parse_actions_targets(check_json):
    """Return sorted, de-duplicated (run_id, job_id) tuples from check-runs data.

    Iterates the check runs in a /commits/{sha}/check-runs payload and, for each
    one produced by GitHub Actions, parses the `(run_id, job_id)` pair out of its
    `details_url` (falling back to `html_url`). A check run is treated as an
    Actions run when `app.slug == "github-actions"`; when the `app` block is
    missing, it falls back to "the URL matched /actions/runs/". `job_id` is None
    when the URL carries only a run id.

    Replaces the old workflow-name / head_sha-based run discovery (issue #500):
    the run(s)/job(s) to track are derived from the same check-runs data the
    verdict reads, rather than from a hardcoded workflow or job name. An empty
    `check_runs` list yields an empty result.
    """
    targets = set()
    for r in check_json.get("check_runs", []):
        run_id, job_id = _actions_run_job(r)
        if run_id is None:
            continue
        targets.add((run_id, job_id))
    # Sort with a None-safe key: the same run could appear both run-only
    # (job_id=None) and with a job id, and None is not orderable against str.
    return sorted(targets, key=lambda t: (t[0], t[1] is not None, t[1] or ""))


def parse_new_artifacts(artifacts_json, seen, artifact_name_regex=DEFAULT_ARTIFACT_NAME_REGEX):
    """Return [(id, name)] for new, unexpired artifacts matching the name regex.

    `artifact_name_regex` is matched (re.search) against each artifact's `name`
    to decide whether it carries per-test markers worth downloading; it replaces
    the old hardcoded testresults-* prefix (issue #500). `seen` is a set of
    already-downloaded artifact ids (read-only here). The caller is responsible
    for adding an id to `seen` only after the artifact has been successfully
    downloaded and parsed, so a transient download failure leaves the artifact
    unseen and eligible for retry on the next poll.
    """
    out = []
    for a in artifacts_json.get("artifacts", []):
        n = a.get("name", "")
        if re.search(artifact_name_regex, n) and not a.get("expired") and str(a["id"]) not in seen:
            out.append((str(a["id"]), n))
    return out


def extract_ndjson_lines(zip_bytes):
    """Yield every line of every *.ndjson entry inside a zip archive's bytes."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.namelist():
            if info.endswith(".ndjson"):
                with zf.open(info) as fh:
                    for line in io.TextIOWrapper(fh, encoding="utf-8", errors="replace"):
                        yield line


# HTTP (stdlib urllib)-----------------------------------------------------------


def _err_detail(e):
    """Return a concise, human-readable cause string for a request exception.

    HTTPError carries an HTTP status and reason ("HTTP 403: Forbidden");
    other URLError/OSError instances expose their underlying reason
    ("URLError: [Errno -2] Name or service not known").
    """
    if isinstance(e, urllib.error.HTTPError):
        return "HTTP %s: %s" % (e.code, e.reason)
    reason = getattr(e, "reason", None)
    if reason is not None:
        return "%s: %s" % (type(e).__name__, reason)
    return "%s: %s" % (type(e).__name__, e)


def _retry_after_seconds(e, now):
    """Return how long to back off (seconds) for a rate-limited HTTPError.

    On HTTP 403/429 GitHub advertises when to retry via either a `Retry-After`
    header (delta seconds) or an `X-RateLimit-Reset` header (Unix timestamp).
    Returns that delay clamped to a sane ceiling, or None when the error is not
    a recognized rate-limit response or carries no usable hint.
    """
    if not isinstance(e, urllib.error.HTTPError) or e.code not in (403, 429):
        return None
    headers = getattr(e, "headers", None)
    if headers is None:
        return None
    retry_after = headers.get("Retry-After")
    if retry_after:
        try:
            return min(max(int(retry_after), 0), 300)
        except (ValueError, TypeError):
            pass
    reset = headers.get("X-RateLimit-Reset")
    if reset:
        try:
            return min(max(int(reset) - int(now), 0), 300)
        except (ValueError, TypeError):
            pass
    return None


class _StripAuthOnCrossHostRedirect(urllib.request.HTTPRedirectHandler):
    """Redirect handler that drops GitHub-specific headers on a cross-host redirect.

    `GET /repos/{owner}/{repo}/actions/artifacts/{id}/zip` (the `raw=True` path)
    redirects to a `*.blob.core.windows.net` URL that is already fully
    authorized by a SAS token in its own query string. urllib's default
    redirect handling forwards the original request's headers to the redirect
    target regardless of host. The `Authorization` header causes Azure Blob
    Storage to reject the request with HTTP 401 because it receives both a
    valid SAS token and an unexpected bearer token (issue #634). The `Accept:
    application/vnd.github+json` header set by `_request()` is GitHub-specific
    too and has no business riding along to a non-GitHub host, so it is
    stripped alongside `Authorization` for the same reason, even though it is
    not currently known to cause a failure (issue #650). Stripping only
    applies when the redirect target's host differs from the original
    request's host, so same-host redirects (if any) are unaffected.
    """

    # Headers added by `_request()` that are meaningful only to api.github.com
    # and should not be forwarded to a cross-host redirect target.
    _GITHUB_SPECIFIC_HEADERS = ("Authorization", "Accept")

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is None:
            return None
        old_host = urllib.parse.urlparse(req.full_url).hostname
        new_host = urllib.parse.urlparse(newurl).hostname
        if new_host != old_host:
            for header_name in self._GITHUB_SPECIFIC_HEADERS:
                new_req.remove_header(header_name)
        return new_req


# Reused across raw=True (artifact-zip) requests: the handler itself is
# stateless, so a single opener built once suffices (issue #634).
_RAW_OPENER = urllib.request.build_opener(_StripAuthOnCrossHostRedirect)


def _request(url, token, raw=False):
    """Perform an authenticated GET. Returns parsed JSON (or raw bytes if raw).

    Returns None on any HTTP/URL/JSON error so the poll loop can survive blips.
    On an HTTP error the originating HTTPError is recorded on the function as
    `_request.last_error` so callers that retry (e.g. the SHA fetch) can inspect
    rate-limit headers; it is cleared to None on a successful request.

    The `raw=True` path (artifact-zip downloads) uses an opener whose redirect
    handler strips GitHub-specific headers (`Authorization`, `Accept`) on a
    cross-host redirect (see `_StripAuthOnCrossHostRedirect`); the JSON API
    path never redirects off `api.github.com`, so it keeps the plain
    `urllib.request.urlopen` call.
    """
    _request.last_error = None
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer %s" % token)
    req.add_header("Accept", "application/vnd.github+json")
    try:
        # URL is built from a GitHub API constant; the file:// risk does not apply.
        if raw:
            with _RAW_OPENER.open(req) as resp:  # nosemgrep
                data = resp.read()
        else:
            with urllib.request.urlopen(req) as resp:  # nosemgrep
                data = resp.read()
    except (urllib.error.URLError, OSError) as e:
        _request.last_error = e
        sys.stderr.write("request failed (%s): %s\n" % (url, _err_detail(e)))
        sys.stderr.flush()
        return None
    if raw:
        return data
    try:
        return json.loads(data)
    except (ValueError, TypeError) as e:
        sys.stderr.write("response parse failed (%s): %s\n" % (url, e))
        sys.stderr.flush()
        return None


_request.last_error = None


def fetch_with_retry(url, token, attempts=3, base_delay=2):
    """Fetch `url` with bounded exponential backoff and rate-limit handling.

    Tries up to `attempts` times. Between failed tries it sleeps with exponential
    backoff (base_delay, 2x, 4x, ...), unless the failure is a rate-limit
    response (HTTP 403/429) advertising a `Retry-After` or `X-RateLimit-Reset`
    hint, in which case it honors that delay instead. Returns the parsed JSON on
    success, or None if every attempt fails (the caller then handles the
    throttled "could not fetch SHA" line).

    Generalized (issue #603) from the old PR-only `fetch_pr_with_retry` so the
    same retry/backoff logic serves both the `--pr` fetch and the `--branch`
    head-commit fetch, without duplicating it.
    """
    for attempt in range(attempts):
        result = _request(url, token)
        if result is not None:
            return result
        if attempt == attempts - 1:
            break
        hint = _retry_after_seconds(_request.last_error, time.time())
        time.sleep(hint if hint is not None else base_delay * (2**attempt))
    return None


def fetch_pr_with_retry(pr, token, attempts=3, base_delay=2):
    """Fetch /pulls/{n} with bounded exponential backoff and rate-limit handling.

    Thin wrapper over fetch_with_retry that builds the /pulls/{n} URL; kept as
    its own function since it is the --pr path's entry point and is exercised
    directly by the test suite.
    """
    url = "%s/repos/%s/%s/pulls/%s" % (API_BASE, OWNER, REPO, pr)
    return fetch_with_retry(url, token, attempts=attempts, base_delay=base_delay)


# Main poll loop-----------------------------------------------------------------


def _parse_outcome_filters(args):
    """Build an outcome_filters dict from parsed CLI args.

    Each outcome has an independent pair of flags:
      --include-fail [pattern] / --no-include-fail
      --include-skip [pattern] / --no-include-skip
      --include-pass [pattern] / --no-include-pass

    Returns a dict: {'FAIL': (enabled, pattern), 'SKIP': (enabled, pattern),
                     'PASS': (enabled, pattern)}.
    Defaults: FAIL all, SKIP all, PASS none.
    """

    def _outcome(no_flag, include_flag, default_enabled):
        if no_flag:
            return (False, None)
        if include_flag is None:
            # flag was not provided at all--use default
            return (default_enabled, None)
        # flag was provided; include_flag is either '' (no pattern) or a pattern string
        return (True, include_flag if include_flag != "" else None)

    return {
        "FAIL": _outcome(args.no_include_fail, args.include_fail, True),
        "SKIP": _outcome(args.no_include_skip, args.include_skip, True),
        "PASS": _outcome(args.no_include_pass, args.include_pass, False),
    }


def _select_mode(args):
    """Return (mode, tag) for whichever identifier flag was supplied (issue #603).

    `mode` is one of 'pr'/'sha'/'run'/'branch'; `tag` is the output-line
    prefix. Only --pr has a PR to gate mergeable_state on, a `draft` flag to
    read, or a merged/closed short-circuit; the other three modes are simpler
    variants that fire `Clear` directly off an all-passed check verdict.

    Branches on "was this flag supplied" (argparse leaves an un-supplied dest
    at its None default), not on truthiness: the mutually exclusive group
    only guarantees exactly one dest is non-None, not that its value is
    non-empty, so a truthiness check would misroute a literal empty-string
    value for the flag actually used (e.g. `--sha ""`) to the next mode in
    the chain instead of honoring --sha.
    """
    if args.pr is not None:
        return "pr", "PR#%s" % args.pr
    if args.sha is not None:
        return "sha", "SHA#%s" % args.sha[:7]
    if args.run_id is not None:
        return "run", "RUN#%s" % args.run_id
    return "branch", "BRANCH#%s" % args.branch


class _DocstringParser(argparse.ArgumentParser):
    """ArgumentParser that prints the module docstring for --help/-h."""

    def print_help(self, file=None):
        if file is None:
            file = sys.stdout
        file.write(__doc__)
        file.write("\n")
        file.flush()

    def error(self, message):
        sys.stderr.write(
            "%s: error: %s\nUse --help for usage information.\n" % (self.prog, message)
        )
        sys.exit(2)


def main(argv):
    parser = _DocstringParser(
        prog="ci_monitor.py",
        description="Poll a PR's CI and stream a terminal outcome plus per-test signals.",
    )
    # Exactly one identifier is required (issue #603): --pr identifies a pull
    # request; --sha/--run-id/--branch monitor a commit, an Actions run, or a
    # branch head directly, with no PR involved.
    id_group = parser.add_mutually_exclusive_group(required=True)
    id_group.add_argument("--pr", metavar="PR_NUMBER", help="The pull request number to monitor.")
    id_group.add_argument(
        "--sha", metavar="SHA", help="A commit SHA to monitor directly; no PR involved."
    )
    id_group.add_argument(
        "--run-id",
        dest="run_id",
        metavar="RUN_ID",
        help="A specific GitHub Actions run ID to monitor, scoped to that run only.",
    )
    id_group.add_argument(
        "--branch",
        metavar="BRANCH",
        help="A branch name to monitor; its head SHA is re-resolved every poll.",
    )

    # Per-outcome filter flags. Each outcome has an --include-* (optional regex)
    # and a --no-include-* suppressor. Defaults: all FAIL, all SKIP, no PASS.
    for outcome in ("fail", "skip", "pass"):
        parser.add_argument(
            "--include-%s" % outcome,
            dest="include_%s" % outcome,
            metavar="PATTERN",
            nargs="?",
            default=None,  # sentinel: flag not supplied
            const="",  # supplied with no argument: match all
            help="Include %s markers, optionally filtered by regex on name or suite."
            % outcome.upper(),
        )
        parser.add_argument(
            "--no-include-%s" % outcome,
            dest="no_include_%s" % outcome,
            action="store_true",
            default=False,
            help="Suppress all %s markers." % outcome.upper(),
        )

    args = parser.parse_args(argv[1:])
    token = os.environ.get("GITHUB_TOKEN", "")
    outcome_filters = _parse_outcome_filters(args)

    mode, tag = _select_mode(args)

    # Configurable run/artifact/step/marker behavior (issue #500). Loaded once at
    # startup and threaded into the parsers below; a missing or invalid config
    # falls back to the DEFAULT_* regexes without aborting the loop.
    config = load_config()
    artifact_name_regex = config["artifact_name_regex"]
    interesting_step_regex = config["interesting_step_regex"]
    deferred_verdict_step_regex = config["deferred_verdict_step_regex"]
    test_marker_regex = config["test_marker_regex"]
    label_gate_check_regex = config["label_gate_check_regex"]

    last_output_ts = time.time()

    # In-memory dedup state for the streamed test-result signals.
    seen_steps = set()
    seen_arts = set()
    seen_fails = set()

    # Attribution accumulators (issue #602): the genuinely-failed step(s) and
    # failing test(s) seen across every poll (including drain re-polls), used to
    # name the specific blocker in the Blocked terminal line rather than only the
    # enclosing check-run/job. Populated by poll_signals; read by drain_then_print.
    failed_steps = []
    failed_tests = []

    def emit_block(lines):
        """Print each line prefixed with the mode's tag and reset the 120s timer."""
        nonlocal last_output_ts
        if not lines:
            return
        text = "\n".join(lines)
        for line in text.split("\n"):
            print("%s: %s" % (tag, line))
        sys.stdout.flush()
        last_output_ts = time.time()

    def poll_signals(sha, check_json=None, explicit_targets=None):
        """Fetch and emit the per-step and per-test informational signals for `sha`.

        Mirrors the streamed test-result signals described in the module
        docstring: per-step conclusion deltas (Signal 1, via parse_steps) and
        per-test FAIL/SKIP/PASS markers from the test-result artifacts
        (Signal 2, via parse_fails). Both are purely informational--they reset
        the silence timer via emit_block but never end the loop. Returns True if
        any line was emitted this call.

        Wiring (a) (issue #512): the run(s)/job(s) to track are discovered from a
        /commits/{sha}/check-runs payload via parse_actions_targets. When the
        caller passes `check_json` (the main loop passes the same payload it read
        for the verdict), that single snapshot drives both the verdict and these
        diagnostics, so verdict and diagnostics never disagree and no extra
        check-runs request is issued. When `check_json` is None (e.g. the drain
        in drain_then_print, which must re-fetch to observe the jobs/artifacts
        endpoints catching up), poll_signals self-fetches the payload as before.

        `explicit_targets`, when not None, is used directly as the (run_id,
        job_id) target list instead of deriving it from check-runs data (issue
        #603). --run-id mode passes `[(run_id, None)]` so it can track its one
        run's jobs without ever fetching /commits/{sha}/check-runs--avoiding the
        "unrelated check on the same commit" ambiguity a check-runs-derived
        target list would reintroduce.
        """
        emitted = [False]

        def _emit(lines):
            if lines:
                emitted[0] = True
            emit_block(lines)

        if explicit_targets is not None:
            targets = explicit_targets
        else:
            if check_json is None:
                check_json = _request(
                    "%s/repos/%s/%s/commits/%s/check-runs" % (API_BASE, OWNER, REPO, sha), token
                )
                # Collapse same-named re-runs (issue #707) on the drain's own
                # self-fetch too, so a stale run's jobs are not tracked; the
                # main-loop caller already passes a collapsed check_json, and
                # re-collapsing that is a no-op.
                if check_json:
                    check_json = latest_check_runs(check_json)
            targets = parse_actions_targets(check_json) if check_json else []
        if not targets:
            return emitted[0]

        # Map each distinct run id to the set of job ids to filter its steps to,
        # scoped per run: a run-only target (job id None) widens the filter to
        # all jobs of that run only, without affecting any other run's filter.
        run_job_ids = {}
        run_order = []
        for run_id, job_id in targets:
            if run_id not in run_job_ids:
                run_job_ids[run_id] = set()
                run_order.append(run_id)
            if job_id is None:
                run_job_ids[run_id] = None
            elif run_job_ids[run_id] is not None:
                run_job_ids[run_id].add(job_id)

        for run_id in run_order:
            job_ids = run_job_ids[run_id]
            # Signal 1--per-step conclusion deltas for the tracked job(s).
            jobs_json = _request(
                "%s/repos/%s/%s/actions/runs/%s/jobs?per_page=30" % (API_BASE, OWNER, REPO, run_id),
                token,
            )
            if jobs_json:
                _emit(
                    parse_steps(
                        jobs_json,
                        seen_steps,
                        job_ids,
                        interesting_step_regex,
                        deferred_verdict_step_regex,
                        failed_steps,
                    )
                )

            # Signal 2--per-test FAIL detail from the test-result artifacts.
            # Download each new artifact once, parse its per-test ndjson markers,
            # and emit new FAIL entries.
            artifacts_json = _request(
                "%s/repos/%s/%s/actions/runs/%s/artifacts?per_page=100"
                % (API_BASE, OWNER, REPO, run_id),
                token,
            )
            if artifacts_json:
                for aid, _name in parse_new_artifacts(
                    artifacts_json, seen_arts, artifact_name_regex
                ):
                    zip_bytes = _request(
                        "%s/repos/%s/%s/actions/artifacts/%s/zip" % (API_BASE, OWNER, REPO, aid),
                        token,
                        raw=True,
                    )
                    if not zip_bytes:
                        continue
                    try:
                        lines = list(extract_ndjson_lines(zip_bytes))
                    except (zipfile.BadZipFile, OSError):
                        continue
                    _emit(
                        parse_fails(
                            lines, seen_fails, outcome_filters, test_marker_regex, failed_tests
                        )
                    )
                    # Mark the artifact seen only after a successful download
                    # and parse: a transient failure above hits `continue` and
                    # leaves the id unseen, so it is retried on the next poll.
                    seen_arts.add(aid)

        return emitted[0]

    def print_summary(rows):
        """Emit the per-check summary block prefixed with the mode's tag."""
        for ln in format_check_summary(rows):
            print("%s: %s" % (tag, ln))
        sys.stdout.flush()

    def drain_then_print(sha, terminal_prefix, terminal_tail, rows, explicit_targets=None):
        """Gap E (issue #402)--drain lagging signal polls before a terminal line.

        Pauses DRAIN_DELAY_SECONDS and re-polls the step/artifact signals, so
        any step or FAIL/SKIP/PASS line that was still lagging on the poll that
        produced the terminal result gets a chance to be emitted before the
        loop ends. Repeats up to DRAIN_MAX_ATTEMPTS times.

        Every attempt runs even after one emits something (issue #419): the two
        lagging endpoints (the gate step from /actions/runs/{id}/jobs and the
        testresults-<group> artifact from /actions/runs/{id}/artifacts) can
        settle on different attempts, so stopping at the first fruitful attempt
        would drop the later-arriving signal for this process's lifetime.

        If every attempt comes up empty AND no check already explains the
        terminal (no row is blocking), prints a diagnostic-absence line.
        That line is suppressed when a named check already concluded a blocking
        conclusion (the terminal is diagnosed).

        Then emits the per-check summary block and the attributed terminal line.
        The terminal is
        `terminal_prefix + blocking_suffix(rows, failed_steps, failed_tests) + terminal_tail`,
        so the drain's late-arriving step/FAIL signals (issue #602) feed the
        blocker attribution just as they feed the streamed diagnostic lines.

        `explicit_targets` is forwarded to poll_signals unchanged (issue #603);
        --run-id mode passes its single (run_id, None) target so the drain
        re-polls stay scoped to that run instead of self-fetching check-runs.
        """
        drained = False
        for _ in range(DRAIN_MAX_ATTEMPTS):
            time.sleep(DRAIN_DELAY_SECONDS)
            if poll_signals(sha, explicit_targets=explicit_targets):
                drained = True
        diagnosed = any(r["blocking"] for r in rows)
        if not drained and not diagnosed:
            print("%s: drain poll found no new diagnostic signals" % tag)
            sys.stdout.flush()
        print_summary(rows)
        terminal_line = (
            terminal_prefix + blocking_suffix(rows, failed_steps, failed_tests) + terminal_tail
        )
        print(terminal_line)
        sys.stdout.flush()

    # Gap D--advertise our PID so the Orchestrator can stop us out-of-band
    # (e.g. `kill -TERM <PID>`) if the Monitor tool's TaskStop is unavailable.
    print(
        "monitor PID %d--if TaskStop is unavailable, send SIGTERM to this PID to stop me"
        % os.getpid()
    )
    sys.stdout.flush()

    while True:
        # Resolve this poll's sha (mode-specific) and check for the one
        # mode-specific early terminal (--pr's merged/closed short-circuit;
        # issue #603 keeps that concept --pr-only, per Gap A below).
        if mode == "pr":
            # Gap C--retry the SHA fetch with backoff and rate-limit awareness
            # instead of a flat 30s retry, so transient blips and 403/429
            # throttles are handled without hammering the API.
            pr_json = fetch_pr_with_retry(args.pr, token)
            sha = parse_pr_sha(pr_json) if pr_json else ""
        elif mode == "sha":
            sha = args.sha
        elif mode == "branch":
            commit_json = fetch_with_retry(
                "%s/repos/%s/%s/commits/%s" % (API_BASE, OWNER, REPO, args.branch), token
            )
            sha = parse_commit_sha(commit_json) if commit_json else ""
        else:  # mode == "run"
            sha = None  # --run-id resolves head_sha from the run object below

        if mode != "run" and not sha:
            # Throttle the noise: only surface the failure after >120s of
            # silence, matching the in_progress heartbeat suppression.
            now = time.time()
            if now - last_output_ts > SILENCE_SECONDS:
                print("%s: could not fetch SHA" % tag)
                sys.stdout.flush()
                last_output_ts = now
            time.sleep(30)
            continue

        if mode == "pr":
            # Gap A--a closed or merged PR leaves mergeable_state "unknown"
            # forever; emit a terminal line and stop instead of spinning. This
            # concept has no equivalent for a bare commit, run, or branch.
            terminal = parse_pr_terminal(pr_json)
            if terminal:
                print("%s: %s" % (tag, terminal))
                sys.stdout.flush()
                break

        if mode == "run":
            # --run-id mode (issue #603) resolves verdict and diagnostics from
            # the run object itself, not from /commits/{sha}/check-runs, so
            # tracking stays scoped to this run and is not confused by an
            # unrelated check on the same commit.
            run_json = _request(
                "%s/repos/%s/%s/actions/runs/%s" % (API_BASE, OWNER, REPO, args.run_id), token
            )
            if run_json is None:
                now = time.time()
                if now - last_output_ts > SILENCE_SECONDS:
                    print("%s: could not fetch run" % tag)
                    sys.stdout.flush()
                    last_output_ts = now
                time.sleep(30)
                continue
            sha = run_json.get("head_sha", "")
            result = parse_run_result(run_json)
            summary_rows = []
            poll_signals(sha, explicit_targets=[(str(args.run_id), None)])
        else:
            check_json = _request(
                "%s/repos/%s/%s/commits/%s/check-runs" % (API_BASE, OWNER, REPO, sha), token
            )
            # Collapse same-named check runs to the latest each (issue #707) so a
            # stale conclusion from an earlier re-run of a named check does not
            # outvote its authoritative latest run. The one collapsed payload
            # then drives the verdict, the summary, and poll_signals' target
            # discovery, keeping all three consistent with GitHub's own
            # mergeable_state.
            if check_json:
                check_json = latest_check_runs(check_json)
            result = parse_check_result(check_json) if check_json else None
            summary_rows = (
                parse_check_summary(check_json, label_gate_check_regex) if check_json else []
            )

            # --- Streamed test-result signals -------------------------------------
            # Emitted independent of the overall check conclusion, so E2E failures
            # surface even while the check stays green via continue-on-error. Both
            # signals are purely informational: they reset the silence timer but
            # never end the loop.
            poll_signals(sha, check_json=check_json)
            # ----------------------------------------------------------------------

        if result == "in_progress":
            now = time.time()
            if now - last_output_ts > SILENCE_SECONDS:
                print("%s: in_progress" % tag)
                sys.stdout.flush()
                last_output_ts = now
        elif result == "all_passed":
            if mode == "pr":
                mpr_json = _request(
                    "%s/repos/%s/%s/pulls/%s" % (API_BASE, OWNER, REPO, args.pr), token
                )
                mergeable = mpr_json.get("mergeable_state", "unknown") if mpr_json else "unknown"
                if pr_is_draft(pr_json) or mergeable == "draft":
                    # Issue #968--a draft PR is held: every check has reported,
                    # and the PR cannot merge until someone marks it ready for
                    # review. That is a settled fact, not a value GitHub is still
                    # computing, so it terminates here instead of falling to the
                    # still-computing else below and spinning until the
                    # Orchestrator's 30-minute timeout. It is neither Clear (the
                    # PR cannot merge) nor Blocked (nothing failed), so it gets
                    # its own terminal.
                    #
                    # Draftness is read first, and from the `draft` boolean, so
                    # no other mergeable_state can crowd it out (see
                    # pr_is_draft); the mergeable_state that came back is
                    # still reported in the suffix as a diagnostic. The `draft`
                    # mergeable_state remains a fallback for a payload with no
                    # `draft` field at all.
                    print_summary(summary_rows)
                    print("%s: Draft (mergeable_state=%s)" % (tag, mergeable))
                    sys.stdout.flush()
                    break
                elif mergeable in ("clean", "unstable"):
                    print_summary(summary_rows)
                    print("%s: Clear (mergeable_state=%s)" % (tag, mergeable))
                    sys.stdout.flush()
                    break
                elif mergeable in ("behind", "dirty"):
                    # Gap E (issue #402)--drain lagging step/FAIL signals before
                    # the terminal line; see drain_then_print and DRAIN_DELAY_SECONDS.
                    drain_then_print(
                        sha,
                        "%s: Blocked" % tag,
                        " (mergeable_state=%s)" % mergeable,
                        summary_rows,
                    )
                    break
                elif mergeable == "blocked":
                    drain_then_print(
                        sha, "%s: Infra" % tag, " (mergeable_state=blocked)", summary_rows
                    )
                    break
                else:
                    # Gap B--throttle "still computing" to >120s of silence, just
                    # like the in_progress heartbeat, so it does not print every poll.
                    now = time.time()
                    if now - last_output_ts > SILENCE_SECONDS:
                        print(
                            "%s: all_passed mergeable_state=%s (still computing)" % (tag, mergeable)
                        )
                        sys.stdout.flush()
                        last_output_ts = now
            else:
                # --sha/--branch/--run-id modes have no mergeable_state concept
                # (issue #603): Clear fires directly off an all-passed verdict.
                print_summary(summary_rows)
                print("%s: Clear" % tag)
                sys.stdout.flush()
                break
        elif result in ("Blocked", "Infra"):
            # Gap E (issue #402)--drain lagging step/FAIL signals before the
            # terminal line; see drain_then_print and DRAIN_DELAY_SECONDS.
            explicit_targets = [(str(args.run_id), None)] if mode == "run" else None
            if mode == "pr":
                # Issue #748--the raw per-check scan flags Blocked/Infra when ANY
                # check-run on the head commit fails, but a failing NON-required
                # check (e.g. an advisory label linter) leaves the PR mergeable.
                # GitHub's mergeable_state is authoritative for "can this merge",
                # so consult it before terminating, exactly as the all_passed path
                # does (a fresh /pulls fetch). Only an explicitly un-mergeable state
                # (behind/dirty/blocked) is a real block that falls through to the
                # raw scan's Blocked/Infra terminal, which still names the blocking
                # check (including the label gate). A mergeable state (clean/
                # unstable) reports Clear; anything else (mergeable_state not yet
                # computed, or another non-blocking state such as has_hooks) keeps
                # polling rather than terminating, staying symmetric with the
                # all_passed path's still-computing else. The raw scan keeps
                # driving the per-check summary and step/FAIL diagnostics
                # regardless. A draft PR is settled before any of that is asked
                # (issue #968), exactly as in the all_passed ladder.
                mpr_json = _request(
                    "%s/repos/%s/%s/pulls/%s" % (API_BASE, OWNER, REPO, args.pr), token
                )
                mergeable = mpr_json.get("mergeable_state", "unknown") if mpr_json else "unknown"
                if pr_is_draft(pr_json) or mergeable == "draft":
                    # Issue #968--as in the all_passed ladder above, draftness is
                    # read first and from the `draft` boolean, so it terminates
                    # instead of polling forever and no other mergeable_state can
                    # crowd it out. The raw scan's Blocked/Infra verdict is
                    # deliberately not reported here: a draft PR's mergeable_state
                    # says nothing about whether the non-passing check is
                    # required, so reporting a merge block would risk the same
                    # false positive issue #748 removed. The terminal names the
                    # draft state and attributes the non-passing check(s) through
                    # the shared " by: ..." suffix--including its [label gate]
                    # annotation, which is the everyday shape while a process
                    # label holds the PR--after the usual drain for lagging
                    # step/FAIL diagnostics.
                    drain_then_print(
                        sha,
                        "%s: Draft" % tag,
                        " (mergeable_state=%s)" % mergeable,
                        summary_rows,
                    )
                    break
                if mergeable in ("clean", "unstable"):
                    print_summary(summary_rows)
                    print("%s: Clear (mergeable_state=%s)" % (tag, mergeable))
                    sys.stdout.flush()
                    break
                if mergeable not in ("behind", "dirty", "blocked"):
                    # Not an un-mergeable state: don't emit a possibly-false
                    # terminal off the raw scan; keep polling. The heartbeat names
                    # the internal "non-passing check" state rather than the raw
                    # Blocked/Infra verdict, so--like the all_passed heartbeat, which
                    # avoids the Clear keyword--it can never be mistaken for a
                    # terminal line by a consumer scanning for Blocked/Infra.
                    now = time.time()
                    if now - last_output_ts > SILENCE_SECONDS:
                        print(
                            "%s: non-passing check, mergeable_state=%s (still computing)"
                            % (tag, mergeable)
                        )
                        sys.stdout.flush()
                        last_output_ts = now
                    time.sleep(30)
                    continue
                # else: behind/dirty/blocked (a real merge block)--fall through to
                # the raw scan's Blocked/Infra terminal below.
            drain_then_print(sha, "%s: %s" % (tag, result), "", summary_rows, explicit_targets)
            break
        elif result == "Clear":
            # No check runs registered (total_count == 0)--already clear.
            # Break immediately; no drain needed (there are no failing signals).
            print("%s: Clear" % tag)
            sys.stdout.flush()
            break
        elif result is not None:
            print("%s: %s" % (tag, result))
            sys.stdout.flush()
            last_output_ts = time.time()

        time.sleep(30)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
