#!/usr/bin/env python3
"""post_pr_ci_summary_link.py--Link a PR to its build-and-test job summary.

CI produces a rich pass/fail summary in the `build-and-test` job's "Summary"
section (see `summarize_test_results.py`), but nothing on the PR points at it:
reviewers must hunt through the Checks tab to find it. This script posts (or
updates) a single sticky comment on the triggering PR with a direct link to
that job's summary anchor, e.g.:

    https://github.com/<owner>/<repo>/actions/runs/<run_id>#summary-<job_id>

It looks up the numeric job ID via the REST Jobs API (the anchor embeds the
job ID, not the run ID), matching on job name and the current run attempt so
re-runs don't pick up a stale job from a previous attempt.

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
    GITHUB_RUN_ATTEMPT    Run attempt number (disambiguates re-runs).
    WORKFLOW_RUN_PR_URL   The triggering PR's html_url (empty on a plain push).
    SUMMARY_WRITTEN       "true" when build-and-test actually wrote the
                          pass/fail summary (its needs_full_build output).
                          Docs-only PRs skip that step, so the job's
                          "Summary" section has nothing to link to, and the
                          comment says so instead of posting a misleading
                          link.
    JOB_NAME              Name of the job whose summary to link (default
                          "build-and-test").

Exit code is always 0 (display only; a missing link must never fail the build).
"""

import os
import re
import sys

import requests

MARKER = "<!-- gb4pc-ci-summary-link -->"

DEFAULT_JOB_NAME = "build-and-test"


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def find_job_id(
    token: str,
    repository: str,
    run_id: str,
    run_attempt: str,
    job_name: str,
) -> str | None:
    """Return the numeric ID of *job_name* in this run's current attempt.

    Filters on `run_attempt` (when known) so a re-run picks the job from the
    attempt actually in flight, not a stale one from an earlier attempt.
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
    return str(candidates[0]["id"])


def build_comment_body(summary_url: str, summary_written: bool) -> str:
    if summary_written:
        link_text = "View the build-and-test summary for this PR"
    else:
        link_text = "View this PR's build-and-test run"
    body = f"{MARKER}\n### CI test summary\n\n[{link_text}]({summary_url}).\n"
    if not summary_written:
        body += (
            "\n(This PR did not need a full build, so no pass/fail summary "
            "was written; the link above goes to the run itself.)\n"
        )
    return body


def find_existing_comment(token: str, repository: str, pr_number: str) -> int | None:
    """Return the ID of our prior sticky comment on the PR, if any."""
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
            if MARKER in (comment.get("body") or ""):
                return comment["id"]
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: failed to list comments on PR #{pr_number}: {exc}", file=sys.stderr)
    return None


def upsert_comment(token: str, repository: str, pr_number: str, body: str) -> bool:
    """Create the sticky comment, or edit it in place if it already exists."""
    existing = find_existing_comment(token, repository, pr_number)
    headers = _github_headers(token)
    try:
        if existing is not None:
            url = f"https://api.github.com/repos/{repository}/issues/comments/{existing}"
            resp = requests.patch(url, headers=headers, json={"body": body}, timeout=30)
        else:
            url = f"https://api.github.com/repos/{repository}/issues/{pr_number}/comments"
            resp = requests.post(url, headers=headers, json={"body": body}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        verb = "update" if existing is not None else "post"
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
    if summary_written:
        job_id = find_job_id(token, repository, run_id, run_attempt, job_name)
        summary_url = f"{run_url}#summary-{job_id}" if job_id else run_url
    else:
        # No summary was written for this run (for example, a docs-only PR
        # skips the heavy build/test steps); link to the bare run instead of
        # an anchor that has nothing meaningful behind it.
        summary_url = run_url

    body = build_comment_body(summary_url, summary_written)
    if upsert_comment(token, repository, pr_number, body):
        print(f"Posted CI summary link to PR #{pr_number}: {summary_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
