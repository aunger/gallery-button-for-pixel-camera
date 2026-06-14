#!/usr/bin/env python3
"""ci_monitor.py — Poll a PR's CI and stream a terminal outcome plus per-test signals.

Invoked by the Orchestrator's Monitor tool call (see agents/dev_orchestration.md).
Each stdout line is consumed as a task-notification event, so output is the
interface: terminal outcome lines end the loop, while informational lines
(in_progress heartbeat, per-step deltas, per-test FAILs) keep it alive.

See scripts/ci_monitor/README.md for full usage instructions, including the
command-line arguments, per-outcome filter flags, and the outcome vocabulary.

Usage:
    python3 scripts/ci_monitor/ci_monitor.py --pr <PR_NUMBER> [filter flags]

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
import sys
import time
import urllib.error
import urllib.request
import zipfile

OWNER = "aunger"
REPO = "gallery-button-for-pixel-camera"
API_BASE = "https://api.github.com"

TEST_MARKER = "##GB4PC_TEST##"

# Suppress repeated informational lines (in_progress heartbeat, "could not fetch
# SHA", "still computing") until this many seconds have elapsed with no output of
# any kind, so a quiet poll loop stays quiet. Every emitted line resets the timer.
SILENCE_SECONDS = 120


# ── Parsers ───────────────────────────────────────────────────────────────────


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


def parse_fails(lines, seen, outcome_filters=None):
    """Emit FAIL/PASS/SKIP lines from ##GB4PC_TEST## ndjson markers.

    `lines` is an iterable of raw text lines; `seen` is a set of already-reported
    suite#name#outcome keys (mutated in place). Returns a list of output lines, each
    possibly carrying an indented (truncated) trace. Deduped across calls by
    suite#name#outcome.

    `outcome_filters` is a dict mapping outcome names ('FAIL', 'PASS', 'SKIP') to
    (enabled, pattern) tuples, where `enabled` is a bool and `pattern` is either
    None (match all) or a regex string (match only markers whose `name` matches).
    Default behavior (outcome_filters=None): report all FAIL, all SKIP, no PASS.
    """
    import re as _re

    if outcome_filters is None:
        outcome_filters = {
            "FAIL": (True, None),
            "SKIP": (True, None),
            "PASS": (False, None),
        }

    out = []
    for raw in lines:
        i = raw.find(TEST_MARKER)
        if i == -1:
            continue
        try:
            m = json.loads(raw[i + len(TEST_MARKER):].strip())
        except Exception:
            continue
        outcome = m.get("outcome", "")
        if outcome not in outcome_filters:
            continue
        enabled, pattern = outcome_filters[outcome]
        if not enabled:
            continue
        # Pattern is matched against the marker's `name` field.
        name = m.get("name", "")
        if pattern is not None and not _re.search(pattern, name):
            continue
        key = m.get("suite", "") + "#" + name + "#" + outcome
        if key in seen:
            continue
        seen.add(key)
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


def _request(url, token, raw=False):
    """Perform an authenticated GET. Returns parsed JSON (or raw bytes if raw).

    Returns None on any HTTP/URL/JSON error so the poll loop can survive blips.
    On an HTTP error the originating HTTPError is recorded on the function as
    `_request.last_error` so callers that retry (e.g. the SHA fetch) can inspect
    rate-limit headers; it is cleared to None on a successful request.
    """
    _request.last_error = None
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer %s" % token)
    req.add_header("Accept", "application/vnd.github+json")
    try:
        with urllib.request.urlopen(req) as resp:
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


def fetch_pr_with_retry(pr, token, attempts=3, base_delay=2):
    """Fetch /pulls/{n} with bounded exponential backoff and rate-limit handling.

    Tries up to `attempts` times. Between failed tries it sleeps with exponential
    backoff (base_delay, 2x, 4x, ...), unless the failure is a rate-limit
    response (HTTP 403/429) advertising a `Retry-After` or `X-RateLimit-Reset`
    hint, in which case it honors that delay instead. Returns the parsed JSON on
    success, or None if every attempt fails (the caller then handles the
    throttled "could not fetch SHA" line).
    """
    url = "%s/repos/%s/%s/pulls/%s" % (API_BASE, OWNER, REPO, pr)
    for attempt in range(attempts):
        pr_json = _request(url, token)
        if pr_json is not None:
            return pr_json
        if attempt == attempts - 1:
            break
        hint = _retry_after_seconds(_request.last_error, time.time())
        time.sleep(hint if hint is not None else base_delay * (2 ** attempt))
    return None


# ── Main poll loop ────────────────────────────────────────────────────────────


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
            # flag was not provided at all — use default
            return (default_enabled, None)
        # flag was provided; include_flag is either '' (no pattern) or a pattern string
        return (True, include_flag if include_flag != "" else None)

    return {
        "FAIL": _outcome(args.no_include_fail, args.include_fail, True),
        "SKIP": _outcome(args.no_include_skip, args.include_skip, True),
        "PASS": _outcome(args.no_include_pass, args.include_pass, False),
    }


class _DocstringParser(argparse.ArgumentParser):
    """ArgumentParser that prints the module docstring for --help/-h."""

    def print_help(self, file=None):
        if file is None:
            file = sys.stdout
        file.write(__doc__)
        file.write("\n")
        file.flush()

    def error(self, message):
        sys.stderr.write("%s: error: %s\nUse --help for usage information.\n" % (self.prog, message))
        sys.exit(2)


def main(argv):
    parser = _DocstringParser(
        prog="ci_monitor.py",
        description="Poll a PR's CI and stream a terminal outcome plus per-test signals.",
    )
    parser.add_argument(
        "--pr", required=True, metavar="PR_NUMBER", help="The pull request number to monitor."
    )

    # Per-outcome filter flags. Each outcome has an --include-* (optional regex)
    # and a --no-include-* suppressor. Defaults: all FAIL, all SKIP, no PASS.
    for outcome in ("fail", "skip", "pass"):
        parser.add_argument(
            "--include-%s" % outcome,
            dest="include_%s" % outcome,
            metavar="PATTERN",
            nargs="?",
            default=None,   # sentinel: flag not supplied
            const="",       # supplied with no argument: match all
            help="Include %s markers, optionally filtered by regex on name." % outcome.upper(),
        )
        parser.add_argument(
            "--no-include-%s" % outcome,
            dest="no_include_%s" % outcome,
            action="store_true",
            default=False,
            help="Suppress all %s markers." % outcome.upper(),
        )

    args = parser.parse_args(argv[1:])
    pr = args.pr
    token = os.environ.get("GITHUB_TOKEN", "")
    outcome_filters = _parse_outcome_filters(args)

    last_output_ts = time.time()

    # In-memory dedup state for the streamed test-result signals.
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

    # Gap D — advertise our PID so the Orchestrator can stop us out-of-band
    # (e.g. `kill -TERM <PID>`) if the Monitor tool's TaskStop is unavailable.
    print("monitor PID %d — if TaskStop is unavailable, send SIGTERM to this PID to stop me" % os.getpid())
    sys.stdout.flush()

    while True:
        # Gap C — retry the SHA fetch with backoff and rate-limit awareness
        # instead of a flat 30s retry, so transient blips and 403/429 throttles
        # are handled without hammering the API.
        pr_json = fetch_pr_with_retry(pr, token)
        sha = parse_pr_sha(pr_json) if pr_json else ""

        if not sha:
            # Throttle the noise: only surface the failure after >120s of
            # silence, matching the in_progress heartbeat suppression.
            now = time.time()
            if now - last_output_ts > SILENCE_SECONDS:
                print("PR#%s: could not fetch SHA" % pr)
                sys.stdout.flush()
                last_output_ts = now
            time.sleep(30)
            continue

        # Gap A — a closed or merged PR leaves mergeable_state "unknown"
        # forever; emit a terminal line and stop instead of spinning.
        terminal = parse_pr_terminal(pr_json)
        if terminal:
            print("PR#%s: %s" % (pr, terminal))
            sys.stdout.flush()
            break

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
                    emit_block(parse_fails(lines, seen_fails, outcome_filters))
                    # Mark the artifact seen only after a successful download
                    # and parse: a transient failure above hits `continue` and
                    # leaves the id unseen, so it is retried on the next poll.
                    seen_arts.add(aid)
        # ----------------------------------------------------------------------

        if result == "in_progress":
            now = time.time()
            if now - last_output_ts > SILENCE_SECONDS:
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
                # Gap B — throttle "still computing" to >120s of silence, just
                # like the in_progress heartbeat, so it does not print every poll.
                now = time.time()
                if now - last_output_ts > SILENCE_SECONDS:
                    print(
                        "PR#%s: all_passed mergeable_state=%s (still computing)"
                        % (pr, mergeable)
                    )
                    sys.stdout.flush()
                    last_output_ts = now
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
