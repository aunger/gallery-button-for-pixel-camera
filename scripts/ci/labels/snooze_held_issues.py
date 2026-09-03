#!/usr/bin/env python3
"""Close an issue when a snooze label is added to it.

Part of the issue #821 snooze mechanism, for issues whose revisit condition is
elapsed time with no observable upstream signal. Applying one of the fixed
`SNOOZE_LABEL_DAYS` labels (see enforce_mutually_exclusive_labels.py) is how a
human snoozes such an issue: this script closes it so it stops reading as
open, dispatchable work, and scripts/ci/labels/wake_held_issues.py reopens it
once the label's day count has elapsed.

enforce_mutually_exclusive_labels.py already keeps at most one snooze label on
an issue at a time (SNOOZE_LABELS is one of its MUTUALLY_EXCLUSIVE_SETS), so
this script only has to react to the label that was just added; it does not
need to reconcile the full label set itself.

Already-closed issues are left alone (idempotent no-op), which also covers
the case where a snooze label is swapped for a longer one on an issue that is
already snoozed.

Usage:
    python3 scripts/ci/labels/snooze_held_issues.py

Exit code:
    0  ADDED_LABEL is not a snooze label, the issue was already closed, or the
       issue was closed successfully.
    1  required configuration is missing/invalid, or fetching or closing the
       issue failed.

Required environment variables:
    GITHUB_TOKEN        Personal access token or Actions secret with
                        issues: write
    GITHUB_REPOSITORY   Owner/repo (e.g. "aunger/gallery-button-for-pixel-camera")
    ISSUE_NUMBER        Number of the issue
    ADDED_LABEL         Name of the label that was just added
"""

import os
import sys

import enforce_mutually_exclusive_labels as emxl

# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def snooze_issue(issue_number: int, snooze_label: str, repo: str, token: str) -> bool:
    """Close *issue_number* for its *snooze_label* snooze.

    Returns True on success. Raises on a fetch or PATCH failure so main()
    can report a nonzero exit; there is nothing benign about either failing
    here (contrast with the already-gone 404 tolerated when removing labels
    elsewhere in this package).
    """
    issue = emxl.gh_api(f"repos/{repo}/issues/{issue_number}", token=token)
    if not isinstance(issue, dict):
        raise RuntimeError(f"unexpected response fetching issue #{issue_number}: {issue!r}")

    if issue.get("state") == "closed":
        print(f"#{issue_number} is already closed--nothing to do.")
        return True

    emxl.gh_api(
        f"repos/{repo}/issues/{issue_number}",
        token=token,
        method="PATCH",
        body={"state": "closed"},
    )
    print(f"Closed #{issue_number} for its '{snooze_label}' snooze.")
    return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    issue_number_str = os.environ.get("ISSUE_NUMBER", "")
    added_label = os.environ.get("ADDED_LABEL", "")

    if not token:
        print("Warning: GITHUB_TOKEN not set--skipping snooze.", file=sys.stderr)
        return 0
    if not repo:
        print("Warning: GITHUB_REPOSITORY not set--skipping snooze.", file=sys.stderr)
        return 0
    if not issue_number_str:
        print("Warning: ISSUE_NUMBER not set--skipping snooze.", file=sys.stderr)
        return 0
    if not added_label:
        print("Warning: ADDED_LABEL not set--skipping snooze.", file=sys.stderr)
        return 0

    if added_label.lower() not in emxl.SNOOZE_LABEL_DAYS:
        print(f"Label '{added_label}' is not a snooze label--nothing to do.")
        return 0

    try:
        issue_number = int(issue_number_str)
    except ValueError:
        print(
            f"Error: ISSUE_NUMBER is not a valid integer: {issue_number_str!r}",
            file=sys.stderr,
        )
        return 1

    try:
        snooze_issue(issue_number, added_label, repo, token)
    except Exception as exc:  # noqa: BLE001
        print(f"Error snoozing #{issue_number}: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
