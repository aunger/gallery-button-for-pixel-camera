#!/usr/bin/env python3
"""Apply labels to a pull request based on regex matches against its title.

Matching is case-insensitive. An underscore counts as a word break, in
addition to the normal ``\\b`` boundary, so e.g. ``ci_monitor`` matches the
``ci`` rule.

Label rules:
    ci:      \\bci\\b | \\bautomat\\w+\\b
    agents:  \\bagent\\w* | \\bdev_orchestration\\b | \\brules?\\b |
             \\battributions?\\b | \\bbylines?\\b | \\bverif\\w+\\b |
             \\bauthor\\b | \\breview\\w+\\b
    testing: \\be2e\\b | \\bunit\\b | \\btest\\w*

Labels are only ever added, never removed.

Usage:
    python3 scripts/label_pr_by_title.py

Exit code:
    0  always--API failures are logged but do not fail the CI run.

Required environment variables:
    GITHUB_TOKEN        Personal access token or Actions secret with
                        pull-requests: write
    GITHUB_REPOSITORY   Owner/repo (e.g. "aunger/gallery-button-for-pixel-camera")
    PR_NUMBER           Number of the pull request
    PR_TITLE            Title of the pull request
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request


# ---------------------------------------------------------------------------
# Label rules
# ---------------------------------------------------------------------------

_B = r"(\b|_)"

LABEL_PATTERNS: dict[str, re.Pattern[str]] = {
    "ci": re.compile(
        rf"{_B}ci{_B}|{_B}automat\w+{_B}",
        re.IGNORECASE,
    ),
    "agents": re.compile(
        rf"{_B}agent\w*"
        rf"|{_B}dev_orchestration{_B}"
        rf"|{_B}rules?{_B}"
        rf"|{_B}attributions?{_B}"
        rf"|{_B}bylines?{_B}"
        rf"|{_B}verif\w+{_B}"
        rf"|{_B}author{_B}"
        rf"|{_B}review\w+{_B}",
        re.IGNORECASE,
    ),
    "testing": re.compile(
        rf"{_B}e2e{_B}|{_B}unit{_B}|{_B}test\w*",
        re.IGNORECASE,
    ),
}


def matching_labels(title: str) -> list[str]:
    """Return the labels whose rule matches *title*."""
    return [label for label, pattern in LABEL_PATTERNS.items() if pattern.search(title)]


# ---------------------------------------------------------------------------
# GitHub API helper
# ---------------------------------------------------------------------------


def gh_api(path: str, token: str, method: str = "GET", body: object = None) -> object:
    """Make a GitHub API request and return the parsed JSON response.

    Raises ``urllib.error.HTTPError`` or ``urllib.error.URLError`` on
    failure so callers can handle errors explicitly.
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
    # URL is built from a GitHub API constant; the file:// risk does not apply.
    with urllib.request.urlopen(req, data) as r:  # nosemgrep
        response_body = r.read()
        return json.loads(response_body) if response_body else None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    pr_number_str = os.environ.get("PR_NUMBER", "")
    title = os.environ.get("PR_TITLE", "")

    if not token:
        print("Warning: GITHUB_TOKEN not set--skipping labeling.", file=sys.stderr)
        return 0
    if not repo:
        print("Warning: GITHUB_REPOSITORY not set--skipping labeling.", file=sys.stderr)
        return 0
    if not pr_number_str:
        print("Warning: PR_NUMBER not set--skipping labeling.", file=sys.stderr)
        return 0

    try:
        pr_number = int(pr_number_str)
    except ValueError:
        print(f"Error: PR_NUMBER is not a valid integer: {pr_number_str!r}", file=sys.stderr)
        return 0

    labels = matching_labels(title)
    if not labels:
        print(f"No label rules matched title {title!r} on #{pr_number}--nothing to do.")
        return 0

    try:
        gh_api(
            f"repos/{repo}/issues/{pr_number}/labels",
            token=token,
            method="POST",
            body={"labels": labels},
        )
        print(f"Applied labels {labels} to #{pr_number}.")
    except urllib.error.HTTPError as exc:
        print(f"Error applying labels {labels} to #{pr_number}: {exc}", file=sys.stderr)
    except urllib.error.URLError as exc:
        print(f"Network error applying labels {labels} to #{pr_number}: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
