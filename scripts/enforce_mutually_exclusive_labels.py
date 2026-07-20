#!/usr/bin/env python3
"""Enforce mutually exclusive label sets on GitHub issues and pull requests.

When a label that belongs to a mutually exclusive set is added to an issue or
pull request, remove all other labels in the same set so that at most one
member of each set is present at any time.

Mutually exclusive sets (fixed):
    [p1, p2, p3]
    [verification needed, verified]
    [changes requested, changes done]
    [orchestrate, orchestrating]

Mutually exclusive prefix groups (any label sharing a prefix is exclusive):
    c-a-*          (author model, e.g. c-a-haiku, c-a-sonnet, c-a-opus)
    c-r-*          (reviewer model, e.g. c-r-haiku, c-r-sonnet, c-r-opus)
    test-failure   (test-failure vs. test-failure-archive; an issue should
                    carry at most one, since test-failure-archive replaces
                    test-failure once the archive window has passed)

Usage:
    python3 scripts/enforce_mutually_exclusive_labels.py

Exit code:
    0  no conflicting labels were found, or every conflicting label was
       removed successfully (a 404 on removal means it was already gone,
       e.g. via a race with another process, and does not count as a
       failure).
    1  ISSUE_NUMBER is not a valid integer, the issue/PR's current labels
       could not be fetched, or removing a conflicting label failed for a
       reason other than 404. A transient GitHub blip (a 5xx, a 429, or a
       network error) is retried with exponential backoff first, so a nonzero
       exit reflects a persistent failure, not a passing blip.

Required environment variables:
    GITHUB_TOKEN        Personal access token or Actions secret with
                        issues: write
    GITHUB_REPOSITORY   Owner/repo  (e.g. "aunger/gallery-button-for-pixel-camera")
    ISSUE_NUMBER        Number of the issue or pull request
    ADDED_LABEL         Name of the label that was just added
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request


# ---------------------------------------------------------------------------
# Mutually exclusive label sets
# ---------------------------------------------------------------------------

MUTUALLY_EXCLUSIVE_SETS: list[frozenset[str]] = [
    frozenset({"p1", "p2", "p3"}),
    frozenset({"verification needed", "verified"}),
    frozenset({"changes requested", "changes done"}),
    frozenset({"orchestrate", "orchestrating"}),
]

# Prefix-based exclusive groups: any two labels sharing a prefix are exclusive.
# Matching is case-insensitive.
MUTUALLY_EXCLUSIVE_PREFIXES: list[str] = [
    "c-a-",
    "c-r-",
    "test-failure",
]


# ---------------------------------------------------------------------------
# GitHub API helper
# ---------------------------------------------------------------------------

# Transient-error retry policy (issue #749). A GitHub API blip (a 5xx server
# error, a 429 rate-limit response, or a network-level URLError) may clear on a
# repeat, so it is retried with exponential backoff before the call is allowed
# to fail. This matches the "retry once, transient GitHub errors are expected"
# guidance already followed by scripts/update_gh_labels.sh and
# agents/dev_orchestration.md for this exact class of instability. A real error
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
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    else:
        data = None

    backoff = INITIAL_BACKOFF_SECONDS
    for attempt in range(1, max_attempts + 1):
        try:
            # URL is built from a GitHub API constant; the file:// risk does not apply.
            with urllib.request.urlopen(req, data) as r:  # nosemgrep
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


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def find_conflicting_set(added_label: str) -> frozenset[str] | None:
    """Return the mutually exclusive set that *added_label* belongs to, or None."""
    lower = added_label.lower()
    for label_set in MUTUALLY_EXCLUSIVE_SETS:
        if lower in label_set:
            return label_set
    return None


def find_conflicting_prefix(added_label: str) -> str | None:
    """Return the exclusive prefix that *added_label* matches, or None."""
    lower = added_label.lower()
    for prefix in MUTUALLY_EXCLUSIVE_PREFIXES:
        if lower.startswith(prefix):
            return prefix
    return None


def labels_to_remove(
    added_label: str,
    current_labels: list[str],
    label_set: frozenset[str],
) -> list[str]:
    """Return the current labels that conflict with *added_label* (fixed-set groups).

    Conflicts are labels in *label_set* other than *added_label* itself.
    """
    added_lower = added_label.lower()
    return [
        lbl for lbl in current_labels if lbl.lower() in label_set and lbl.lower() != added_lower
    ]


def labels_to_remove_by_prefix(
    added_label: str,
    current_labels: list[str],
    prefix: str,
) -> list[str]:
    """Return the current labels that conflict with *added_label* (prefix groups).

    Conflicts are labels that share *prefix* (case-insensitive) but differ from
    *added_label* itself.
    """
    added_lower = added_label.lower()
    return [
        lbl
        for lbl in current_labels
        if lbl.lower().startswith(prefix) and lbl.lower() != added_lower
    ]


def remove_labels(
    issue_number: int,
    to_remove: list[str],
    repo: str,
    token: str,
) -> bool:
    """Remove each label in *to_remove* from the issue/PR, logging each removal.

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
            print(f"Removed conflicting label '{label}' from #{issue_number}.")
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    issue_number_str = os.environ.get("ISSUE_NUMBER", "")
    added_label = os.environ.get("ADDED_LABEL", "")

    if not token:
        print("Warning: GITHUB_TOKEN not set--skipping enforcement.", file=sys.stderr)
        return 0
    if not repo:
        print("Warning: GITHUB_REPOSITORY not set--skipping enforcement.", file=sys.stderr)
        return 0
    if not issue_number_str:
        print("Warning: ISSUE_NUMBER not set--skipping enforcement.", file=sys.stderr)
        return 0
    if not added_label:
        print("Warning: ADDED_LABEL not set--skipping enforcement.", file=sys.stderr)
        return 0

    try:
        issue_number = int(issue_number_str)
    except ValueError:
        print(
            f"Error: ISSUE_NUMBER is not a valid integer: {issue_number_str!r}",
            file=sys.stderr,
        )
        return 1

    label_set = find_conflicting_set(added_label)
    conflicting_prefix = find_conflicting_prefix(added_label)

    if label_set is None and conflicting_prefix is None:
        print(f"Label '{added_label}' is not in any mutually exclusive set--nothing to do.")
        return 0

    try:
        issue = gh_api(f"repos/{repo}/issues/{issue_number}", token=token)
    except Exception as exc:  # noqa: BLE001
        print(f"Error fetching issue #{issue_number}: {exc}", file=sys.stderr)
        return 1

    if not isinstance(issue, dict):
        print(
            f"Error: unexpected response fetching issue #{issue_number}: {issue!r}",
            file=sys.stderr,
        )
        return 1

    current_labels = [lbl["name"] for lbl in issue.get("labels", [])]

    to_remove: list[str] = []
    if label_set is not None:
        to_remove.extend(labels_to_remove(added_label, current_labels, label_set))
    if conflicting_prefix is not None:
        to_remove.extend(
            labels_to_remove_by_prefix(added_label, current_labels, conflicting_prefix)
        )

    if not to_remove:
        print(f"No conflicting labels found for '{added_label}' on #{issue_number}--nothing to do.")
        return 0

    if not remove_labels(issue_number, to_remove, repo, token):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
