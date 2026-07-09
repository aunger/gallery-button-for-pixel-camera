#!/usr/bin/env python3
"""Apply labels to an issue or pull request based on regex matches against its title.

Matching is case-insensitive. An underscore counts as a word break, in
addition to the normal ``\\b`` boundary, so e.g. ``ci_monitor`` matches the
``ci`` rule. See LABEL_PATTERNS below for the exact rules--not duplicated
here, to avoid this docstring drifting out of sync with the code.

Labels are only ever added, never removed.

Usage:
    python3 scripts/label_by_title.py

Exit code:
    0  no labels matched, or labels were applied successfully.
    1  required configuration is missing/invalid, or applying labels failed.

Required environment variables:
    GITHUB_TOKEN        Personal access token or Actions secret with
                        issues: write and pull-requests: write
    GITHUB_REPOSITORY   Owner/repo (e.g. "aunger/gallery-button-for-pixel-camera")
    ISSUE_NUMBER        Number of the issue or pull request
    ISSUE_TITLE         Title of the issue or pull request
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

# "E2ETest" (no boundary around "E2E" inside a CamelCase identifier like
# GalleryButtonVisualE2ETest) needs both sides open.
_E2E_TEST_IDENTIFIER = r"\w*e2etest\w*"

# "merge" is only recognized alongside a companion word, checked in either
# order since natural phrasing varies ("merge gate" vs "block merge"): two
# lookaheads express that AND without duplicating the whole sub-pattern for
# both orderings.
_MERGE_COMPANION = rf"{_B}gat(ing|e[ds]?){_B}|{_B}(un)?block\w*|{_B}prs?{_B}"
_MERGE = rf"(?=.*{_B}merg\w+)(?=.*({_MERGE_COMPANION}))"

LABEL_PATTERNS: dict[str, re.Pattern[str]] = {
    "ci": re.compile(
        rf"{_B}ci{_B}"
        rf"|{_B}automat\w+{_B}"
        rf"|{_B}workflow\w*"
        rf"|{_B}github[\W_]*action\w*"
        rf"|{_B}e2e\w*"
        rf"|{_MERGE}"
        rf"|{_B}pre-?flight{_B}"
        rf"|{_B}codeql{_B}",
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
        rf"|{_B}review\w+{_B}"
        rf"|{_B}orchestrat\w*"
        rf"|{_B}ci[\W_]*monitor\w*"
        rf"|{_B}sub[\W_]?agent\w*",
        re.IGNORECASE,
    ),
    "testing": re.compile(
        rf"{_B}e2e{_B}"
        rf"|{_B}unit{_B}"
        rf"|{_B}test\w*"
        rf"|{_E2E_TEST_IDENTIFIER}"
        rf"|{_B}pre-?flight{_B}"
        rf"|(?-i:\w*Test(\b|_))",
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
    issue_number_str = os.environ.get("ISSUE_NUMBER", "")
    title = os.environ.get("ISSUE_TITLE", "")

    if not token:
        print("Error: GITHUB_TOKEN not set.", file=sys.stderr)
        return 1
    if not repo:
        print("Error: GITHUB_REPOSITORY not set.", file=sys.stderr)
        return 1
    if not issue_number_str:
        print("Error: ISSUE_NUMBER not set.", file=sys.stderr)
        return 1

    try:
        issue_number = int(issue_number_str)
    except ValueError:
        print(f"Error: ISSUE_NUMBER is not a valid integer: {issue_number_str!r}", file=sys.stderr)
        return 1

    labels = matching_labels(title)
    if not labels:
        print(f"No label rules matched title {title!r} on #{issue_number}--nothing to do.")
        return 0

    try:
        gh_api(
            f"repos/{repo}/issues/{issue_number}/labels",
            token=token,
            method="POST",
            body={"labels": labels},
        )
        print(f"Applied labels {labels} to #{issue_number}.")
    except urllib.error.HTTPError as exc:
        print(f"Error applying labels {labels} to #{issue_number}: {exc}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Network error applying labels {labels} to #{issue_number}: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
