#!/usr/bin/env python3
"""post_pr_ci_summary_link.py--Link a PR to its build-and-test job summary.

CI produces a rich pass/fail summary in the `build-and-test` job's "Summary"
section (see `summarize_test_results.py`), but nothing on the PR points at it:
reviewers must hunt through the Checks tab to find it. This script posts (or
updates) a single sticky comment on the triggering PR with a direct link to
that job's summary anchor, e.g.:

    https://github.com/<owner>/<repo>/actions/runs/<run_id>#summary-<job_id>

The comment accumulates a chronological list of CI runs for the PR. Each
invocation appends one new list item of the form:

    - [build-and-test 1234 pass](https://.../#summary-<job_id>)

If a list item for the same job name and run number already exists it is
replaced in place, making the script safe to re-run within the same workflow
run. The list is capped to the most recent 20 entries.

The comment is "sticky": a hidden HTML marker lets subsequent runs on the same
PR find and edit the existing comment in place rather than piling up a new one
per push.

Usage:
    python3 scripts/post_pr_ci_summary_link.py

Environment:
    GITHUB_TOKEN          GitHub token for REST calls (required).
    GITHUB_REPOSITORY     "owner/repo" (required).
    GITHUB_SERVER_URL     Server base URL, e.g. "https://github.com".
    GITHUB_RUN_ID         Workflow run ID.
    GITHUB_RUN_NUMBER     Human-facing run number shown in the GitHub UI.
    GITHUB_RUN_ATTEMPT    Run attempt number (disambiguates re-runs).
    WORKFLOW_RUN_PR_URL   The triggering PR's html_url (empty on a plain push).
    SUMMARY_WRITTEN       "true" when build-and-test actually wrote the
                          pass/fail summary (its needs_full_build output).
                          Docs-only PRs skip that step, so no summary table
                          exists; these runs show result "skip" and link to
                          the bare run URL.
    JOB_NAME              Name of the job whose summary to link (default
                          "build-and-test").

Exit code is always 0 (display only; a missing link must never fail the build).
"""

import os
import re
import sys
from typing import NamedTuple

import requests

MARKER = "<!-- gb4pc-ci-summary-link -->"

DEFAULT_JOB_NAME = "build-and-test"

# Maximum number of list items to keep in the comment (oldest are dropped).
MAX_ITEMS = 20

# Regex matching a single Markdown list item as written by build_comment_body.
# Captures: job_name, run_number (str), result, url.
_ITEM_RE = re.compile(r"^- \[([^\s\]]+) (\d+) ([^\]]+)\]\(([^)]+)\)\s*$")


class CIItem(NamedTuple):
    """One CI-run entry in the sticky comment list."""

    job_name: str
    run_number: str  # kept as str to avoid integer-parsing surprises
    result: str
    url: str


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def find_job(
    token: str,
    repository: str,
    run_id: str,
    run_attempt: str,
    job_name: str,
) -> dict | None:
    """Return the job dict for *job_name* in this run's current attempt.

    Filters on `run_attempt` (when known) so a re-run picks the job from the
    attempt actually in flight, not a stale one from an earlier attempt.
    Returns the full job dict (including `id` and `conclusion`), or None.
    """
    url = f"https://api.github.com/repos/{repository}/actions/runs/{run_id}/jobs"
    try:
        resp = requests.get(
            url,
            headers=_github_headers(token),
            params={"per_page": 100},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: failed to list jobs for run {run_id}: {exc}", file=sys.stderr)
        return None

    candidates = [j for j in data.get("jobs", []) if j.get("name") == job_name]
    if run_attempt:
        attempt_matches = [
            j for j in candidates if str(j.get("run_attempt", "")) == str(run_attempt)
        ]
        if attempt_matches:
            candidates = attempt_matches

    if not candidates:
        return None
    return candidates[0]


def find_job_id(
    token: str,
    repository: str,
    run_id: str,
    run_attempt: str,
    job_name: str,
) -> str | None:
    """Return the numeric ID of *job_name* in this run's current attempt.

    Thin wrapper around find_job for callers that only need the job ID.
    """
    job = find_job(token, repository, run_id, run_attempt, job_name)
    return str(job["id"]) if job is not None else None


def result_label(conclusion: str | None) -> str:
    """Map a GitHub job conclusion to a short human-readable result label.

    Returns one of: "pass", "fail", "skip", "unknown".
    """
    if conclusion == "success":
        return "pass"
    if conclusion in ("failure", "timed_out", "cancelled"):
        return "fail"
    if conclusion == "skipped":
        return "skip"
    return "unknown"


def parse_existing_items(body: str) -> list[CIItem]:
    """Extract CI run list items already present in a comment body.

    Returns a list of CIItem tuples in the order they appear in the body,
    skipping any lines that do not match the expected list-item pattern.
    """
    items: list[CIItem] = []
    for line in body.splitlines():
        m = _ITEM_RE.match(line)
        if m:
            items.append(
                CIItem(
                    job_name=m.group(1),
                    run_number=m.group(2),
                    result=m.group(3),
                    url=m.group(4),
                )
            )
    return items


def build_comment_body(items: list[CIItem]) -> str:
    """Render the sticky comment body from a list of CIItem entries."""
    lines = [f"- [{item.job_name} {item.run_number} {item.result}]({item.url})" for item in items]
    list_block = "\n".join(lines)
    return f"{MARKER}\n### CI test summary\n\n{list_block}\n"


def find_existing_comment(
    token: str, repository: str, pr_number: str
) -> tuple[int, str] | tuple[None, None]:
    """Return (id, body) of our prior sticky comment on the PR, if any.

    Returns (None, None) when no marker comment is found or on API error.
    """
    url = f"https://api.github.com/repos/{repository}/issues/{pr_number}/comments"
    try:
        resp = requests.get(
            url,
            headers=_github_headers(token),
            params={"per_page": 100},
            timeout=30,
        )
        resp.raise_for_status()
        for comment in resp.json():
            body = comment.get("body") or ""
            if MARKER in body:
                return comment["id"], body
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: failed to list comments on PR #{pr_number}: {exc}", file=sys.stderr)
    return None, None


def upsert_comment(
    token: str, repository: str, pr_number: str, body: str, existing_id: int | None = None
) -> bool:
    """Create the sticky comment, or edit it in place if it already exists.

    When *existing_id* is provided the caller has already fetched the comment;
    pass it here so we do not make a redundant list-comments API call.
    """
    headers = _github_headers(token)
    try:
        if existing_id is not None:
            url = f"https://api.github.com/repos/{repository}/issues/comments/{existing_id}"
            resp = requests.patch(url, headers=headers, json={"body": body}, timeout=30)
        else:
            url = f"https://api.github.com/repos/{repository}/issues/{pr_number}/comments"
            resp = requests.post(url, headers=headers, json={"body": body}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        verb = "update" if existing_id is not None else "post"
        print(f"Warning: failed to {verb} CI summary link comment: {exc}", file=sys.stderr)
        return False


def pr_number_from_url(pr_url: str) -> str | None:
    """Extract the PR number from its html_url, e.g. ".../pull/42" -> "42"."""
    match = re.search(r"/pull/(\d+)/?$", pr_url)
    return match.group(1) if match else None


def main(argv: list[str] | None = None) -> int:
    del argv  # No CLI arguments; configuration comes entirely from the environment.

    token = os.environ.get("GITHUB_TOKEN", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    run_number = os.environ.get("GITHUB_RUN_NUMBER", "")
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    pr_url = os.environ.get("WORKFLOW_RUN_PR_URL", "")
    summary_written = os.environ.get("SUMMARY_WRITTEN", "") == "true"
    job_name = os.environ.get("JOB_NAME", DEFAULT_JOB_NAME)

    if not token or not repository:
        print(
            "Note: GITHUB_TOKEN/GITHUB_REPOSITORY not set--skipping CI summary link.",
            file=sys.stderr,
        )
        return 0
    if not run_id:
        print("Note: GITHUB_RUN_ID not set--skipping CI summary link.", file=sys.stderr)
        return 0
    if not pr_url:
        print("Note: not a pull_request run--skipping CI summary link.", file=sys.stderr)
        return 0

    pr_number = pr_number_from_url(pr_url)
    if pr_number is None:
        print(f"Note: could not parse PR number from '{pr_url}'--skipping.", file=sys.stderr)
        return 0

    run_url = f"{server_url}/{repository}/actions/runs/{run_id}"

    # Always look up the job so we can read its conclusion (needed for the
    # result label regardless of whether a summary was written).
    job = find_job(token, repository, run_id, run_attempt, job_name)
    job_id = str(job["id"]) if job is not None else None
    conclusion = job.get("conclusion") if job is not None else None

    if summary_written and job_id:
        item_url = f"{run_url}#summary-{job_id}"
    else:
        item_url = run_url

    if summary_written:
        label = result_label(conclusion)
    else:
        # Docs-only PR: no full build ran; show "skip" to convey that.
        label = "skip"

    current_item = CIItem(
        job_name=job_name,
        run_number=run_number or run_id,
        result=label,
        url=item_url,
    )

    # Fetch existing comment (id and body) once, so we can preserve prior items
    # and avoid a second list-comments call in upsert_comment.
    existing_id, existing_body = find_existing_comment(token, repository, pr_number)

    prior_items = parse_existing_items(existing_body) if existing_body is not None else []

    # Merge: replace any existing item for the same (job_name, run_number) pair,
    # otherwise append. This makes the script idempotent within a run.
    merged: list[CIItem] = []
    replaced = False
    for item in prior_items:
        if item.job_name == current_item.job_name and item.run_number == current_item.run_number:
            merged.append(current_item)
            replaced = True
        else:
            merged.append(item)
    if not replaced:
        merged.append(current_item)

    # Cap to the most recent MAX_ITEMS entries.
    if len(merged) > MAX_ITEMS:
        merged = merged[-MAX_ITEMS:]

    body = build_comment_body(merged)

    # When existing_body fetch failed (existing_id is None but we know a prior
    # comment exists): prefer not to post a fresh comment that would skip history.
    # However we cannot distinguish "no prior comment" from "fetch failed" when
    # existing_id is None and existing_body is None. The safe path: if
    # existing_id is None we either POST (first run) or POST again (fetch
    # failed). The latter risks a duplicate comment, but preserves history in
    # the existing comment and only adds a new one. This matches the recommended
    # policy: do not PATCH without the existing id.
    if upsert_comment(token, repository, pr_number, body, existing_id):
        print(f"Posted CI summary link to PR #{pr_number}: {item_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
