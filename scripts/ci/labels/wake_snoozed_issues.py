#!/usr/bin/env python3
"""Reopen issues whose snooze period has elapsed.

The other half of the issue #821 snooze mechanism: scripts/ci/labels/
snooze_issues.py closes an issue when a snooze label (SNOOZE_LABEL_DAYS in
enforce_mutually_exclusive_labels.py) is added to it. This script runs daily
and, for every issue still carrying a snooze label, checks whether that many
days have passed since the label was applied. Once they have, the issue is
reopened, the snooze label is removed, and so is every label in
propagate_issue_labels.PROCESS_STATE_LABELS (orchestrate, orchestrating,
verification needed, verified, changes requested, changes done)--a woken
issue should land in plain triage, not resume mid-cycle in whatever
orchestration state it was closed in, and not silently look dispatchable via
a leftover `orchestrate`.

The elapsed time is measured from the most recent "labeled" event naming the
issue's snooze rung, in either of its spellings (the GitHub issue events API;
see find_label_applied_at), not from the issue's `updated_at`, which any
comment or other label change would also bump and so would silently extend or
reset the snooze. If that event cannot be found (should not happen in
practice; every label application produces one), the issue is left alone
rather than guessed at--acting on evidence the script does not have would be
worse than staying snoozed for one more run.

Before acting on an issue, wake_issue() re-fetches it and confirms the snooze
label is still present, and removes labels one at a time by name rather than
PATCHing a computed full array. Both guard against a run that takes long
enough (many issues, events-API pagination, transient-error retries) for a
label to change underneath it--in particular an escalation to a longer snooze,
which replaces the shorter one via mutual-exclusion enforcement and must not
be silently undone by a wake decided before the escalation happened.

Usage:
    python3 scripts/ci/labels/wake_snoozed_issues.py

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
    snooze labels are scoped to issues (see snooze_issues.py).
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
    labels: list[str],
    repo: str,
    token: str,
) -> datetime.datetime | None:
    """Return when any of *labels* was last applied to *issue_number*, or None.

    Reads the issue events API for "labeled" events matching one of *labels*
    (case-insensitively) and returns the latest one's timestamp. Returns None
    if no such event is found--for example if the label predates the
    repository's event history, though GitHub does not appear to prune it.

    *labels* is every spelling of a single ladder rung, never a mix of rungs.
    Both spellings have to count: renaming a label on GitHub does not rewrite
    the "labeled" events already recorded under its old name, so once the
    issue #1019 follow-up renames "hold N days" to "snooze N days", an issue
    carrying the new name still owes its snooze to an event naming the old
    one. Matching only the current name would find no event at all and leave
    such an issue snoozed forever.
    """
    wanted = {label.lower() for label in labels}
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
            if event_label.lower() not in wanted:
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


def wake_issue(issue_number: int, snooze_label: str, repo: str, token: str) -> bool:
    """Reopen *issue_number* if it is still snoozed under *snooze_label*.

    *snooze_label* is a ladder label, and both of its spellings are stripped:
    the issue must come out of this carrying none of its rung, or it would be
    open and snoozed at once.

    Re-fetches the issue's current labels immediately before acting, rather
    than reusing the list-call snapshot main() saw when it decided this
    issue's snooze had elapsed, and removes only the specific labels this
    function targets (via enforce_mutually_exclusive_labels.remove_labels(),
    one DELETE per label) instead of PATCHing a computed full label array.
    Together these mean a label change made after main()'s snapshot--most
    importantly someone escalating to a longer snooze, which replaces
    *snooze_label* via mutual-exclusion enforcement--is never silently
    reverted: an escalation is caught by the live re-fetch below (the
    now-current label list would no longer contain *snooze_label*, so this
    returns without touching anything), and even a label added in the
    narrower window between this fetch and the DELETE calls survives, since
    remove_labels() only ever removes the labels it is explicitly told to.

    Returns True if the issue was woken (reopened and stripped), False if it
    turned out to no longer be snoozed under *snooze_label* (left untouched).
    """
    issue = emxl.gh_api(f"repos/{repo}/issues/{issue_number}", token=token)
    if not isinstance(issue, dict):
        raise RuntimeError(f"unexpected response fetching issue #{issue_number}: {issue!r}")

    snooze_lower = snooze_label.lower()
    current_labels = [lbl["name"] for lbl in issue.get("labels", [])]
    if snooze_lower not in {lbl.lower() for lbl in current_labels}:
        print(
            f"#{issue_number} no longer carries '{snooze_label}' (label changed since "
            "this run started)--leaving it alone."
        )
        return False

    # Every spelling of this rung, not just the one main() found the issue
    # under. Stripping only that one would leave a woken issue open and still
    # wearing the other spelling, which tomorrow's run would find, date from
    # the same elapsed event, and wake all over again.
    rung_lower = {
        lbl.lower() for lbl in emxl.snooze_labels_for_days(emxl.SNOOZE_LABEL_DAYS[snooze_lower])
    }

    to_remove = [
        lbl
        for lbl in current_labels
        if lbl.lower() in rung_lower or lbl.lower() in pil.PROCESS_STATE_LABELS
    ]

    # Reopening the issue's state never touches its labels at all--no PATCH
    # body key for labels is sent here, so there is nothing for this call to
    # clobber even if the label list changes again immediately afterward.
    emxl.gh_api(
        f"repos/{repo}/issues/{issue_number}",
        token=token,
        method="PATCH",
        body={"state": "open"},
    )
    emxl.remove_labels(issue_number, to_remove, repo, token, reason="expired-snooze")

    other_stripped = sorted(lbl for lbl in to_remove if lbl.lower() not in rung_lower)
    stripped_note = ""
    if other_stripped:
        stripped_note = f" Also removed stale process-state label(s): {', '.join(other_stripped)}."

    try:
        emxl.gh_api(
            f"repos/{repo}/issues/{issue_number}/comments",
            token=token,
            method="POST",
            body={
                "body": (
                    f"The `{snooze_label}` snooze has elapsed. Reopening and returning this "
                    f"issue to triage.{stripped_note}"
                )
            },
        )
    except Exception as exc:  # noqa: BLE001
        # The reopen and label strip above already succeeded and are not
        # undone by a failed notification comment; report this narrowly so
        # the Actions log does not read this issue as untouched.
        print(
            f"#{issue_number} was reopened and stripped of its snooze, but posting the "
            f"wake comment failed: {exc}",
            file=sys.stderr,
        )

    print(f"Woke #{issue_number} from its '{snooze_label}' snooze.")
    return True


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

    for snooze_days in emxl.SNOOZE_LADDER_DAYS:
        rung_labels = emxl.snooze_labels_for_days(snooze_days)

        # Which spelling of the rung an issue carries decides only what the
        # wake comment and log lines name it as. Mutual exclusion keeps an
        # issue from carrying two spellings at once, but dedupe by issue
        # number anyway so a pair that slipped through is woken once rather
        # than reopened and commented on twice in the same run.
        snoozed: dict[int, str] = {}
        for label in rung_labels:
            try:
                for issue in fetch_issues_with_label(repo, token, label):
                    snoozed.setdefault(issue["number"], label)
            except Exception as exc:  # noqa: BLE001
                print(f"Error fetching issues snoozed with '{label}': {exc}", file=sys.stderr)

        cutoff = now - datetime.timedelta(days=snooze_days)

        for issue_number, snooze_label in sorted(snoozed.items()):
            try:
                applied_at = find_label_applied_at(issue_number, rung_labels, repo, token)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"Error reading label history for #{issue_number}: {exc}",
                    file=sys.stderr,
                )
                continue

            if applied_at is None:
                print(
                    f"Could not find when '{snooze_label}' was applied to #{issue_number}; "
                    "leaving it snoozed.",
                    file=sys.stderr,
                )
                continue

            if applied_at > cutoff:
                continue  # still within the snooze period

            try:
                if wake_issue(issue_number, snooze_label, repo, token):
                    woken += 1
            except Exception as exc:  # noqa: BLE001
                print(f"Error waking #{issue_number}: {exc}", file=sys.stderr)

    print(f"Done. Woke {woken} issue(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
