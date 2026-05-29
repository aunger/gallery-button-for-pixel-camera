#!/usr/bin/env python3
"""ci_monitor.py — Poll a PR's CI and stream a terminal outcome plus per-test signals.

Invoked by the Orchestrator's Monitor tool call (see agents/dev_orchestration.md).
Each stdout line is consumed as a task-notification event, so output is the
interface: terminal outcome lines end the loop, while informational lines
(in_progress heartbeat, per-step deltas, per-test FAILs) keep it alive.

Usage:
    python3 scripts/ci_monitor.py <PR_NUMBER>

Arguments:
    <PR_NUMBER>   The pull request number to monitor (required).

Environment:
    GITHUB_TOKEN  GitHub token used for the REST calls (required).

Outcome vocabulary (one terminal line ends the loop):
    PR#N: Clear ...      All checks passed and mergeable_state is clean/unstable.
    PR#N: Blocked ...    A check failed, or mergeable_state is behind/dirty.
    PR#N: Infra ...      A CI infrastructure problem, or mergeable_state=blocked.
    PR#N: in_progress    CI still running; emitted only after >120 s of silence.
    PR#N: step "..." -> ...    A build-and-test step reached a conclusion (informational).
    PR#N: FAIL [suite] name: ...   A per-test failure from a testresults artifact (informational).

NOTE on error handling: like the bash predecessor, the poll loop must survive
transient REST/parse failures. HTTP and JSON errors are caught per-call and
treated as "no data this poll" rather than aborting the resilient loop. The
30-minute escalation threshold is enforced by `timeout_ms` on the Monitor call,
not here.
"""

import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile

OWNER = "aunger"
REPO = "gallery-button-for-pixel-camera"
API_BASE = "https://api.github.com"

TEST_MARKER = "##GB4PC_TEST##"


# ── Parsers ───────────────────────────────────────────────────────────────────
# These are the two real parsers exercised directly by test_ci_monitor.py.


def parse_steps(jobs_json, seen):
    """Emit step-delta lines for the build-and-test job.

    Reads parsed jobs JSON (a dict, as from /actions/runs/{id}/jobs) and a
    `seen` set of already-reported step numbers (mutated in place). Emits a line
    when a step reaches 'completed' if it is one of the named test steps (on any
    conclusion) or genuinely failed; successful setup steps and skipped
    conditional steps are suppressed. Returns a list of step-delta lines.
    """
    out = []
    for j in jobs_json.get("jobs", []):
        if j.get("name") != "build-and-test":
            continue
        for s in j.get("steps", []):
            if s.get("status") != "completed":
                continue
            num = str(s.get("number"))
            if num in seen:
                continue
            seen.add(num)
            name = s.get("name", "?")
            concl = s.get("conclusion") or "?"
            if (
                name == "Build and run unit tests"
                or "E2ETest" in name
                or concl in ("failure", "cancelled", "timed_out", "action_required")
            ):
                out.append('step "%s" -> %s' % (name, concl))
    return out


def parse_fails(lines, seen):
    """Emit FAIL lines from ##GB4PC_TEST## ndjson markers.

    `lines` is an iterable of raw text lines; `seen` is a set of already-reported
    suite#name keys (mutated in place). Returns a list of FAIL lines, each
    possibly carrying an indented (truncated) trace. Deduped across calls by
    suite#name.
    """
    out = []
    for raw in lines:
        i = raw.find(TEST_MARKER)
        if i == -1:
            continue
        try:
            m = json.loads(raw[i + len(TEST_MARKER):].strip())
        except Exception:
            continue
        if m.get("outcome") != "FAIL":
            continue
        key = m.get("suite", "") + "#" + m.get("name", "")
        if key in seen:
            continue
        seen.add(key)
        msg = (m.get("msg") or "").strip()
        tr = (m.get("trace") or "").strip()
        if len(tr) > 800:
            tr = tr[:800] + " ...(truncated)"
        line = "FAIL [%s] %s: %s" % (m.get("suite", "?"), m.get("name", "?"), msg)
        if tr:
            line += "\n  " + tr.replace("\n", "\n  ")
        out.append(line)
    return out


def parse_pr_sha(pr_json):
    """Return the head SHA from a /pulls/{n} response, or '' if absent."""
    return pr_json.get("head", {}).get("sha", "")


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
        if any(c in ("cancelled", "timed_out", "stale", "startup_failure") for c in conclusions):
            return "Infra"
        if any(c in ("failure", "action_required") for c in conclusions):
            return "Blocked"
        return "all_passed"
    return "in_progress"


def parse_run_id(runs_json):
    """Return the first non-cancelled workflow run id, or '' if none."""
    for r in runs_json.get("workflow_runs", []):
        if r.get("status") != "cancelled":
            return str(r["id"])
    return ""


def parse_new_artifacts(artifacts_json, seen):
    """Return [(id, name)] for new, unexpired testresults-* artifacts.

    `seen` is a set of already-downloaded artifact ids (read-only here). The
    caller is responsible for adding an id to `seen` only after the artifact has
    been successfully downloaded and parsed, so a transient download failure
    leaves the artifact unseen and eligible for retry on the next poll.
    """
    out = []
    for a in artifacts_json.get("artifacts", []):
        n = a.get("name", "")
        if n.startswith("testresults-") and not a.get("expired") and str(a["id"]) not in seen:
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


# ── HTTP (stdlib urllib) ────────────────────────────────────────────────────────


def _request(url, token, raw=False):
    """Perform an authenticated GET. Returns parsed JSON (or raw bytes if raw).

    Returns None on any HTTP/URL/JSON error so the poll loop can survive blips.
    """
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer %s" % token)
    req.add_header("Accept", "application/vnd.github+json")
    try:
        with urllib.request.urlopen(req) as resp:
            data = resp.read()
    except (urllib.error.URLError, OSError):
        return None
    if raw:
        return data
    try:
        return json.loads(data)
    except (ValueError, TypeError):
        return None


# ── Main poll loop ────────────────────────────────────────────────────────────


def main(argv):
    if len(argv) < 2:
        sys.stderr.write("usage: ci_monitor.py <PR_NUMBER>\n")
        return 2
    pr = argv[1]
    token = os.environ.get("GITHUB_TOKEN", "")

    last_output_ts = time.time()

    # In-memory dedup state for the streamed test-result signals. (No temp files:
    # a single process shares state directly, so the bash mktemp/trap dance and
    # its cleanup are unnecessary.)
    seen_steps = set()
    seen_arts = set()
    seen_fails = set()

    def emit_block(lines):
        """Print each line prefixed with the PR tag and reset the 120s timer."""
        nonlocal last_output_ts
        if not lines:
            return
        text = "\n".join(lines)
        for line in text.split("\n"):
            print("PR#%s: %s" % (pr, line))
        sys.stdout.flush()
        last_output_ts = time.time()

    while True:
        pr_json = _request("%s/repos/%s/%s/pulls/%s" % (API_BASE, OWNER, REPO, pr), token)
        sha = parse_pr_sha(pr_json) if pr_json else ""

        if not sha:
            print("PR#%s: could not fetch SHA" % pr)
            sys.stdout.flush()
            last_output_ts = time.time()
            time.sleep(30)
            continue

        check_json = _request(
            "%s/repos/%s/%s/commits/%s/check-runs" % (API_BASE, OWNER, REPO, sha), token
        )
        result = parse_check_result(check_json) if check_json else None

        # --- Streamed test-result signals -------------------------------------
        # Emitted independent of the overall check conclusion, so E2E failures
        # surface even while the check stays green via continue-on-error. Both
        # signals are purely informational: they reset the silence timer but
        # never end the loop.
        runs_json = _request(
            "%s/repos/%s/%s/actions/runs?head_sha=%s&event=pull_request&per_page=5"
            % (API_BASE, OWNER, REPO, sha),
            token,
        )
        run_id = parse_run_id(runs_json) if runs_json else ""

        if run_id:
            # Signal 1 — per-step conclusion deltas for the build-and-test job.
            jobs_json = _request(
                "%s/repos/%s/%s/actions/runs/%s/jobs?per_page=30"
                % (API_BASE, OWNER, REPO, run_id),
                token,
            )
            if jobs_json:
                emit_block(parse_steps(jobs_json, seen_steps))

            # Signal 2 — per-test FAIL detail from the testresults-<group>
            # artifacts. Download each new artifact once, parse its
            # ##GB4PC_TEST## ndjson markers, and emit new FAIL entries.
            artifacts_json = _request(
                "%s/repos/%s/%s/actions/runs/%s/artifacts?per_page=100"
                % (API_BASE, OWNER, REPO, run_id),
                token,
            )
            if artifacts_json:
                for aid, _name in parse_new_artifacts(artifacts_json, seen_arts):
                    zip_bytes = _request(
                        "%s/repos/%s/%s/actions/artifacts/%s/zip"
                        % (API_BASE, OWNER, REPO, aid),
                        token,
                        raw=True,
                    )
                    if not zip_bytes:
                        continue
                    try:
                        lines = list(extract_ndjson_lines(zip_bytes))
                    except (zipfile.BadZipFile, OSError):
                        continue
                    emit_block(parse_fails(lines, seen_fails))
                    # Only mark the artifact seen after a successful download
                    # and parse, mirroring the bash script's "add after a
                    # successful curl && unzip" behavior. A transient failure
                    # above hits `continue` and leaves the id unseen for retry.
                    seen_arts.add(aid)
        # ----------------------------------------------------------------------

        if result == "in_progress":
            now = time.time()
            if now - last_output_ts > 120:
                print("PR#%s: in_progress" % pr)
                sys.stdout.flush()
                last_output_ts = now
        elif result == "all_passed":
            mpr_json = _request(
                "%s/repos/%s/%s/pulls/%s" % (API_BASE, OWNER, REPO, pr), token
            )
            mergeable = (
                mpr_json.get("mergeable_state", "unknown") if mpr_json else "unknown"
            )
            if mergeable in ("clean", "unstable"):
                print("PR#%s: Clear (mergeable_state=%s)" % (pr, mergeable))
                sys.stdout.flush()
                break
            elif mergeable in ("behind", "dirty"):
                print("PR#%s: Blocked (mergeable_state=%s)" % (pr, mergeable))
                sys.stdout.flush()
                break
            elif mergeable == "blocked":
                print("PR#%s: Infra (mergeable_state=blocked)" % pr)
                sys.stdout.flush()
                break
            else:
                print(
                    "PR#%s: all_passed mergeable_state=%s (still computing)"
                    % (pr, mergeable)
                )
                sys.stdout.flush()
                last_output_ts = time.time()
        elif result in ("Blocked", "Infra"):
            print("PR#%s: %s" % (pr, result))
            sys.stdout.flush()
            break
        elif result is not None:
            print("PR#%s: %s" % (pr, result))
            sys.stdout.flush()
            last_output_ts = time.time()

        time.sleep(30)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
