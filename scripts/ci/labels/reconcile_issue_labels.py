#!/usr/bin/env python3
"""Reconcile linked-issue label propagation across every open pull request.

scripts/ci/labels/propagate_issue_labels.py runs from a `pull_request` webhook event
and only ever looks at the one PR that triggered it. GitHub fires no webhook
event for linking a PR to an issue via the "Development" sidebar after the
PR is already open, so propagate-issue-labels.yml widened its trigger types
to cover that PR the next time it sees ordinary activity (a push, a reopen,
a ready-for-review flip, a review request)--but a PR that is sidebar-linked
and then sees none of those events before merge would still never be
revisited, and its issue's labels would silently never propagate.

This script closes that residual gap by walking every open pull request and
applying scripts/ci/labels/propagate_issue_labels.py's exact propagation rule
(including the mutual-exclusion skip) to each one. It is meant to be run
on demand--locally, or by dispatching
.github/workflows/reconcile-issue-labels.yml in Actions--rather than on a
schedule: the gap it covers is already narrow after the trigger widening
above, so polling every open PR indefinitely on a timer was judged not
worth the ongoing cost (see issue #621).

Usage:
    python3 scripts/ci/labels/reconcile_issue_labels.py

Exit code:
    0  every open PR was reconciled without error (whether or not any
       labels were applied to any of them).
    1  required configuration is missing/invalid, the open PR list could not
       be fetched, or reconciling at least one PR failed.

Required environment variables:
    GITHUB_TOKEN        Token with issues: write and pull-requests: write
    GITHUB_REPOSITORY   Owner/repo (e.g. "aunger/gallery-button-for-pixel-camera")
"""

import os
import sys

import propagate_issue_labels as pil

# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def fetch_open_pr_numbers(repo: str, token: str) -> list[int]:
    """Return the number of every open pull request in *repo*."""
    return [pr["number"] for pr in pil.emxl.fetch_open_pull_requests(repo, token)]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")

    if not token:
        print("Error: GITHUB_TOKEN not set.", file=sys.stderr)
        return 1
    if not repo:
        print("Error: GITHUB_REPOSITORY not set.", file=sys.stderr)
        return 1
    if "/" not in repo:
        print(f"Error: GITHUB_REPOSITORY is not owner/repo: {repo!r}", file=sys.stderr)
        return 1

    owner, name = repo.split("/", 1)

    try:
        pr_numbers = fetch_open_pr_numbers(repo, token)
    except Exception as exc:  # noqa: BLE001
        print(f"Error fetching open pull requests: {exc}", file=sys.stderr)
        return 1

    if not pr_numbers:
        print("No open pull requests--nothing to reconcile.")
        return 0

    all_succeeded = True
    for pr_number in pr_numbers:
        if not pil.propagate_to_pr(owner, name, repo, pr_number, token):
            all_succeeded = False

    return 0 if all_succeeded else 1


if __name__ == "__main__":
    sys.exit(main())
