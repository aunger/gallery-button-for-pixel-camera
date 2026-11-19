#!/usr/bin/env python3
"""GitHub REST helpers for post_dependency_checks.py.

- `gh_api`: one request, retried with backoff on a transient failure.
- `fetch_open_pull_requests`: every open pull request, page by page.
- `remove_labels`: one DELETE per label, a 404 counting as already gone.

This module has no `main`; it is imported, not run directly.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "prs-and-issues"))

from https_only import urlopen_https

# ---------------------------------------------------------------------------
# GitHub API helper
# ---------------------------------------------------------------------------

# Transient-error retry policy (issue #749). A GitHub API blip (a 5xx server
# error, a 429 rate-limit response, or a network-level URLError) may clear on a
# repeat, so it is retried with exponential backoff before the call is allowed
# to fail. This matches the "retry once, transient GitHub errors are expected"
# guidance already followed by github-supplement's update_gh_labels.sh and
# the orchestrate plugin's Orchestrator for this exact class of instability. A real error
# (a 4xx other than 429, e.g. a 404 already-gone or a 422 validation failure) is
# never retried: repeating it would not change the outcome.
MAX_ATTEMPTS = 4
INITIAL_BACKOFF_SECONDS = 1.0
BACKOFF_MULTIPLIER = 2.0


def _is_transient_error(exc: Exception) -> bool:
    """Return True if *exc* is a transient GitHub API failure worth retrying.

    Transient means the same request may succeed on a repeat: a 5xx server
    error, a 429 rate-limit response, or a network-level ``URLError``
    (connection reset, DNS blip). Any other ``HTTPError`` (a 4xx other than
    429, such as a 404 already-gone or a 422 validation failure) is a real
    error that a retry would not clear.

    ``HTTPError`` is a subclass of ``URLError``, so it is checked first.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or 500 <= exc.code <= 599
    return isinstance(exc, urllib.error.URLError)


def gh_api(
    path: str,
    token: str,
    method: str = "GET",
    body: object = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> object:
    """Make a GitHub API request and return the parsed JSON response.

    Returns ``None`` for responses with an empty body (e.g. 204 No Content).

    A transient failure (see ``_is_transient_error``) is retried up to
    *max_attempts* times with exponential backoff so a passing GitHub blip does
    not surface as a real error. Once the attempts are exhausted, or on a
    non-transient error, raises ``urllib.error.HTTPError`` or
    ``urllib.error.URLError`` so callers can handle errors explicitly (the
    already-gone 404 case in ``remove_labels`` is unchanged, since a 404 is not
    transient and is never retried here).
    """
    url = f"https://api.github.com/{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    else:
        data = None

    backoff = INITIAL_BACKOFF_SECONDS
    for attempt in range(1, max_attempts + 1):
        try:
            with urlopen_https(url, data, headers=headers, method=method) as r:
                response_body = r.read()
                return json.loads(response_body) if response_body else None
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            if attempt >= max_attempts or not _is_transient_error(exc):
                raise
            print(
                f"Transient error on {method} {path} "
                f"(attempt {attempt}/{max_attempts}): {exc}. "
                f"Retrying in {backoff:g}s.",
                file=sys.stderr,
            )
            time.sleep(backoff)
            backoff *= BACKOFF_MULTIPLIER
    # Unreachable: the final attempt above either returns or raises. Present so
    # the function has a definite terminal on every path.
    raise AssertionError("gh_api retry loop exited without returning or raising")


# GitHub caps a page of pull requests at 100, so a short page is the last page.
PULLS_PER_PAGE = 100


def fetch_open_pull_requests(repo: str, token: str) -> list[dict]:
    """Return every open pull request in *repo*, following pagination.

    Callers wanting a subset filter the returned objects themselves: the list
    endpoint offers no server-side filter finer than state, base branch, and
    head branch name.
    """
    pulls: list[dict] = []
    page = 1
    while True:
        batch = gh_api(
            f"repos/{repo}/pulls?state=open&per_page={PULLS_PER_PAGE}&page={page}",
            token=token,
        )
        if not batch:
            break
        pulls.extend(batch)
        if len(batch) < PULLS_PER_PAGE:
            break
        page += 1
    return pulls


def remove_labels(
    issue_number: int,
    to_remove: list[str],
    repo: str,
    token: str,
    reason: str = "conflicting",
) -> bool:
    """Remove each label in *to_remove* from the issue/PR, logging each removal.

    *reason* is substituted directly into the per-label log line ("Removed
    {reason} label '<name>' from #N."). It defaults to "conflicting" for this
    module's own mutual-exclusion callers, where that is literally what
    happened. scripts/ci/labels/label_by_files.py and
    scripts/ci/labels/audit_labels_by_files.py reuse this function for a
    different kind of removal--a label no changed file justifies under an
    if-and-only-if rule, where nothing conflicts--and pass reason="unjustified"
    so the log line describes what actually happened instead of contradicting
    it.

    Returns True if every removal succeeded or was already absent (404),
    False if any removal failed with a non-404 HTTPError or a URLError.
    """
    all_succeeded = True
    for label in to_remove:
        encoded = urllib.request.quote(label, safe="")
        try:
            gh_api(
                f"repos/{repo}/issues/{issue_number}/labels/{encoded}",
                token=token,
                method="DELETE",
            )
            print(f"Removed {reason} label '{label}' from #{issue_number}.")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                print(
                    f"Label '{label}' not found on #{issue_number} (already removed?).",
                    file=sys.stderr,
                )
            else:
                print(
                    f"Error removing label '{label}' from #{issue_number}: {exc}",
                    file=sys.stderr,
                )
                all_succeeded = False
        except urllib.error.URLError as exc:
            print(
                f"Network error removing label '{label}' from #{issue_number}: {exc}",
                file=sys.stderr,
            )
            all_succeeded = False
    return all_succeeded
