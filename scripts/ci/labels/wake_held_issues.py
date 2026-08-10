#!/usr/bin/env python3
"""Reopen issues whose "hold N days" snooze period has elapsed.

The other half of the issue #821 snooze mechanism: scripts/ci/labels/
snooze_held_issues.py closes an issue when a hold label
(HOLD_LABEL_DAYS in enforce_mutually_exclusive_labels.py) is added to it.
This script runs daily and, for every issue still carrying a hold label,
checks whether that many days have passed since the label was applied. Once
they have, the issue is reopened, the hold label is removed, and so is every
label in propagate_issue_labels.PROCESS_STATE_LABELS (orchestrate,
orchestrating, verification needed, verified, changes requested, changes
done)--a woken issue should land in plain triage, not resume mid-cycle in
whatever orchestration state it was closed in, and not silently look
dispatchable via a leftover `orchestrate`.

The elapsed time is measured from the most recent "labeled" event for the
issue's current hold label (the GitHub issue events API), not from the
issue's `updated_at`, which any comment or other label change would also
bump and so would silently extend or reset the hold. If that event cannot be
found (should not happen in practice; every label application produces one),
the issue is left alone rather than guessed at--acting on evidence the script
does not have would be worse than staying snoozed for one more run.

Usage:
    python3 scripts/ci/labels/wake_held_issues.py

Exit code is always 0; API failures are logged but do not fail the CI run,
matching scripts/ci/prs-and-issues/archive_stale_test_failures.py.

Required environment variables:
    GITHUB_TOKEN        Personal access token or Actions secret with
                        issues: write
    GITHUB_REPOSITORY   Owner/repo (e.g. "aunger/gallery-button-for-pixel-camera")
"""

import datetime
import os
import sys
import urllib.parse

import enforce_mutually_exclusive_labels as emxl
import propagate_issue_labels as pil

# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def fetch_issues_with_label(repo: str, token: str, label: str) -> list[dict]:
    """Return every issue (not pull request) carrying *label*, open or closed.

    Paginates through all pages automatically. Pull requests are excluded:
    GitHub's issues-list endpoint returns them alongside real issues for any
    matching label, identifiable by the presence of a "pull_request" key, and
    the hold-label snooze is scoped to issues (see snooze_held_issues.py).
    """
    encoded = urllib.parse.quote(label, safe="")
    issues: list[dict] = []
    page = 1
    while True:
        batch = emxl.gh_api(
            f"repos/{repo}/issues?labels={encoded}&state=all&per_page=100&page={page}",
            token=token,
        )
        if not batch:
            break
        issues.extend(item for item in batch if "pull_request" not in item)
        page += 1
    return issues


def find_label_applied_at(
    issue_number: int,
    label: str,
    repo: str,
    token: str,
) -> datetime.datetime | None:
    """Return when *label* was most recently applied to *issue_number*, or None.

    Reads the issue events API for "labeled" events matching *label*
    (case-insensitively) and returns the latest one's timestamp. Returns None
    if no such event is found--for example if the label predates the
    repository's event history, though GitHub does not appear to prune it.
    """
    lower = label.lower()
    latest: datetime.datetime | None = None
    page = 1
    while True:
        batch = emxl.gh_api(
            f"repos/{repo}/issues/{issue_number}/events?per_page=100&page={page}",
            token=token,
        )
        if not batch:
            break
        for event in batch:
            if event.get("event") != "labeled":
                continue
            event_label = (event.get("label") or {}).get("name", "")
            if event_label.lower() != lower:
                continue
            created_at = datetime.datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))
            if latest is None or created_at > latest:
                latest = created_at
        if len(batch) < 100:
            break
        page += 1
    return latest


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def wake_issue(issue: dict, hold_label: str, repo: str, token: str) -> None:
    """Reopen *issue*, stripping its hold label and PROCESS_STATE_LABELS."""
    issue_number = issue["number"]
    current_labels = [lbl["name"] for lbl in issue.get("labels", [])]
    hold_lower = hold_label.lower()
    remaining = [
        lbl
        for lbl in current_labels
        if lbl.lower() != hold_lower and lbl.lower() not in pil.PROCESS_STATE_LABELS
    ]
    stripped = [lbl for lbl in current_labels if lbl not in remaining]

    emxl.gh_api(
        f"repos/{repo}/issues/{issue_number}",
        token=token,
        method="PATCH",
        body={"state": "open", "labels": remaining},
    )

    stripped_note = ""
    other_stripped = sorted(lbl for lbl in stripped if lbl.lower() != hold_lower)
    if other_stripped:
        stripped_note = f" Also removed stale process-state label(s): {', '.join(other_stripped)}."

    emxl.gh_api(
        f"repos/{repo}/issues/{issue_number}/comments",
        token=token,
        method="POST",
        body={
            "body": (
                f"The `{hold_label}` hold has elapsed. Reopening and returning this "
                f"issue to triage.{stripped_note}"
            )
        },
    )
    print(f"Woke #{issue_number} from its '{hold_label}' hold.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")

    if not token:
        print("Warning: GITHUB_TOKEN not set; skipping wake check.", file=sys.stderr)
        return 0
    if not repo:
        print("Warning: GITHUB_REPOSITORY not set; skipping wake check.", file=sys.stderr)
        return 0

    now = datetime.datetime.now(datetime.timezone.utc)
    woken = 0

    for hold_label, hold_days in emxl.HOLD_LABEL_DAYS.items():
        try:
            issues = fetch_issues_with_label(repo, token, hold_label)
        except Exception as exc:  # noqa: BLE001
            print(f"Error fetching issues held with '{hold_label}': {exc}", file=sys.stderr)
            continue

        cutoff = now - datetime.timedelta(days=hold_days)

        for issue in issues:
            issue_number = issue["number"]
            try:
                applied_at = find_label_applied_at(issue_number, hold_label, repo, token)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"Error reading label history for #{issue_number}: {exc}",
                    file=sys.stderr,
                )
                continue

            if applied_at is None:
                print(
                    f"Could not find when '{hold_label}' was applied to #{issue_number}; "
                    "leaving it held.",
                    file=sys.stderr,
                )
                continue

            if applied_at > cutoff:
                continue  # still within the hold period

            try:
                wake_issue(issue, hold_label, repo, token)
                woken += 1
            except Exception as exc:  # noqa: BLE001
                print(f"Error waking #{issue_number}: {exc}", file=sys.stderr)

    print(f"Done. Woke {woken} issue(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
