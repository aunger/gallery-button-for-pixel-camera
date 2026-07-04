#!/usr/bin/env python3
"""post_pr_ci_placeholder_comment.py--Reserve the first PR comment for CI status.

Issue #561: the sticky CI-summary comment (see `post_pr_ci_summary_link.py`)
is not posted until the `build-and-test` job finishes, which can take a long
time (full E2E suite). Until then, nothing marks where CI status will show
up, so the sticky comment often lands after human review comments instead of
being the first comment on the PR.

This script runs as soon as a PR is opened and posts a placeholder comment
carrying the same hidden marker `post_pr_ci_summary_link.py` looks for. That
script's `find_existing_comment` / `upsert_comment` logic then edits this
placeholder in place once real results are available, rather than creating a
new comment later.

Usage:
    python3 scripts/post_pr_ci_placeholder_comment.py

Environment:
    GITHUB_TOKEN          GitHub token for REST calls (required).
    GITHUB_REPOSITORY     "owner/repo" (required).
    WORKFLOW_RUN_PR_URL   The triggering PR's html_url (required).

Exit code is always 0 (display only; a missing placeholder must never fail
the build).
"""

import os
import sys

# Reuse the marker constant and GitHub-comment helpers rather than
# duplicating them (they must stay in lockstep with post_pr_ci_summary_link.py,
# which later finds and edits this same comment).
from post_pr_ci_summary_link import (
    MARKER,
    find_existing_comment,
    pr_number_from_url,
    upsert_comment,
)

PLACEHOLDER_BODY = f"{MARKER}\n### CI test summary\n\nCI has not reported results yet.\n"


def main(argv: list[str] | None = None) -> int:
    del argv  # No CLI arguments; configuration comes entirely from the environment.

    token = os.environ.get("GITHUB_TOKEN", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    pr_url = os.environ.get("WORKFLOW_RUN_PR_URL", "")

    if not token or not repository:
        print(
            "Note: GITHUB_TOKEN/GITHUB_REPOSITORY not set--skipping placeholder comment.",
            file=sys.stderr,
        )
        return 0
    if not pr_url:
        print("Note: not a pull_request run--skipping placeholder comment.", file=sys.stderr)
        return 0

    pr_number = pr_number_from_url(pr_url)
    if pr_number is None:
        print(f"Note: could not parse PR number from '{pr_url}'--skipping.", file=sys.stderr)
        return 0

    lookup = find_existing_comment(token, repository, pr_number)
    if not lookup.fetch_ok:
        print(
            "Warning: skipping placeholder comment: could not fetch existing comments.",
            file=sys.stderr,
        )
        return 0

    if lookup.comment_id is not None:
        # A sticky CI comment is already there (e.g. a duplicate "opened"
        # delivery); leave it alone rather than clobbering real results.
        print(f"Note: PR #{pr_number} already has a CI summary comment; skipping placeholder.")
        return 0

    if upsert_comment(token, repository, pr_number, PLACEHOLDER_BODY):
        print(f"Posted placeholder CI summary comment to PR #{pr_number}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
