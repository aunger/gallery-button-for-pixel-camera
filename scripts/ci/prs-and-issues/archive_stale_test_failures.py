#!/usr/bin/env python3
"""Archive stale test-failure GitHub issues.

Finds all issues (open or closed) labelled ``test-failure`` whose
``updated_at`` timestamp is older than STALE_DAYS (default 21).  For
each such issue the ``test-failure`` label is removed and
``test-failure-archive`` is added, so it falls out of the Phase 4
dedup search scope.  Closed issues are included because a closed
issue that still carries ``test-failure`` remains within
``find_existing_issue``'s dedup search and could be reopened by a
later failure of the same test.

Usage:
    python3 scripts/ci/prs-and-issues/archive_stale_test_failures.py

Exit code is always 0; API failures are logged but do not fail the
CI run.

Required environment variables:
    GITHUB_TOKEN        Personal access token or Actions secret with
                        issues: write
    GITHUB_REPOSITORY   Owner/repo (e.g. "aunger/gallery-button-for-pixel-camera")

Optional environment variables:
    STALE_DAYS          Number of inactivity days before an issue is
                        archived (default: 21)
"""

import datetime
import json
import os
import sys
import urllib.request
import urllib.error


# ---------------------------------------------------------------------------
# Label name constants
# ---------------------------------------------------------------------------

LABEL_TEST_FAILURE: str = "test-failure"
LABEL_TEST_FAILURE_ARCHIVE: str = "test-failure-archive"


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
        return json.loads(r.read())


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def fetch_stale_issues(repo: str, token: str, cutoff: datetime.datetime) -> list[dict]:
    """Return all test-failure issues, open or closed, last updated before *cutoff*.

    Paginates through all pages automatically.
    """
    issues: list[dict] = []
    page = 1
    while True:
        batch = gh_api(
            f"repos/{repo}/issues?labels={LABEL_TEST_FAILURE}&per_page=100&page={page}",
            token=token,
        )
        if not batch:
            break
        issues.extend(batch)
        page += 1

    stale = []
    for issue in issues:
        updated_at = datetime.datetime.fromisoformat(issue["updated_at"].replace("Z", "+00:00"))
        if updated_at < cutoff:
            stale.append(issue)
    return stale


def archive_issue(issue: dict, repo: str, token: str) -> None:
    """Swap labels on *issue*: remove test-failure, add test-failure-archive."""
    n = issue["number"]
    current_labels = [
        label["name"] for label in issue["labels"] if label["name"] != LABEL_TEST_FAILURE
    ]
    current_labels.append(LABEL_TEST_FAILURE_ARCHIVE)
    gh_api(
        f"repos/{repo}/issues/{n}",
        token=token,
        method="PATCH",
        body={"labels": current_labels},
    )
    print(f"Archived issue #{n}: {issue['title']}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    stale_days = int(os.environ.get("STALE_DAYS", "21"))

    if not token:
        print("Warning: GITHUB_TOKEN not set; skipping archival.", file=sys.stderr)
        return 0
    if not repo:
        print("Warning: GITHUB_REPOSITORY not set; skipping archival.", file=sys.stderr)
        return 0

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=stale_days)

    try:
        stale = fetch_stale_issues(repo, token, cutoff)
    except Exception as exc:  # noqa: BLE001
        print(f"Error fetching issues: {exc}", file=sys.stderr)
        return 0

    archived = 0
    for issue in stale:
        try:
            archive_issue(issue, repo, token)
            archived += 1
        except Exception as exc:  # noqa: BLE001
            print(
                f"Error archiving issue #{issue['number']}: {exc}",
                file=sys.stderr,
            )

    print(f"Done. Archived {archived} issue(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
