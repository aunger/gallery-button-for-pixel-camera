#!/usr/bin/env python3
"""Reconcile linked-issue label propagation across every open pull request.

scripts/propagate_issue_labels.py runs from a `pull_request` webhook event
(opened, or edited with a body change) and only ever looks at the one PR that
triggered it. That covers a closing relationship declared in the PR body
("Fixes #N" and its variants), but GitHub has no webhook event for linking a
PR to an issue via the "Development" sidebar after the PR is already open.
A PR linked that way, with no subsequent body edit, would otherwise never be
revisited, and its issue's labels would silently never propagate--exactly
the "cross-reference" case the GraphQL-based detection in
propagate_issue_labels.py was chosen to handle in the first place (see
issue #621).

This script closes that gap by periodically walking every open pull request
and applying scripts/propagate_issue_labels.py's exact propagation rule
(including the mutual-exclusion skip) to each one. It is meant to run on a
schedule (see .github/workflows/reconcile-issue-labels.yml), not per event.

Usage:
    python3 scripts/reconcile_issue_labels.py

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
    numbers: list[int] = []
    page = 1
    while True:
        batch = pil.emxl.gh_api(
            f"repos/{repo}/pulls?state=open&per_page=100&page={page}",
            token=token,
        )
        if not batch:
            break
        numbers.extend(pr["number"] for pr in batch)
        page += 1
    return numbers


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
