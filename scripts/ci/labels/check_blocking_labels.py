#!/usr/bin/env python3
"""Fail the merge gate when any open PR at this head commit carries a blocking label.

Backs the "No blocking labels" required status check
(.github/workflows/block-merge-on-blocking-labels.yml), which enforces
agents/dev_orchestration.md's "do not merge while process state is
outstanding" rule.

Why this asks about a commit rather than about one pull request (issue #833):
a check run is stored against a head commit, not against a pull request, and
GitHub evaluates a required check by taking the most recent run of that check
name on the commit with no notion of which PR produced it. A check that
answered only "does the PR that fired this event carry a blocking label?"
therefore let two open PRs sharing a head commit answer for each other: the
one that fired most recently overwrote the other's verdict. That is not
hypothetical, since agents/pr_verify.md's live-fire recipe opens a test PR
from the head of the PR being verified, i.e. exactly on the PRs the gate is
supposed to be holding.

So the question asked here is the one the storage can actually answer: does
*any* open pull request whose head is this commit carry a blocking label? The
answer is then true for every PR on the commit, whichever one triggered the
run.

Two consequences of that scoping are deliberate:

  - A PR can be blocked by a sibling PR's label. The failure output names the
    offending PR number and label, so the log says whose label it is. Blocking
    is the safe direction: the alternative is a gate that silently opens.
  - Base branch is not consulted. A PR onto a non-`main` base sharing this head
    commit counts too, because its check run lands on the same commit and would
    otherwise overwrite this verdict.

Only open PRs count. A closed PR cannot be merged, so its labels must not
block anyone. That is a statement about this script's verdict, not about the
gate: what gates a merge is the stored check run, so a failure a since-closed
PR earned outlives it until something re-runs the check. That is why the
workflow also triggers on `closed`. That run excludes the closing PR from both
of this script's sources by construction (see main()), rather than trusting the
open-PR listing to have caught up with the close, so it is what actually
retires the verdict that PR earned.

The triggering PR's own labels are read from the event payload as well as from
the API listing (see main()). Both are snapshots that go stale in opposite
directions, so unioning them is what keeps either staleness blocking rather
than opening, and it also makes this check never weaker about the triggering PR
than the payload-only check it replaced.

The price of that is a verdict that can name a label the PR no longer carries,
since a run whose event has been superseded, or whose listing lagged a label
removal, reports the labels of the moment it read. Re-running the job re-reads
both sources and clears it. Over-blocking is the safe direction, so this is the
trade taken deliberately. Note that no agent reads this log: the Orchestrator's
`labelGateBlock` branch deliberately does not diagnose which label the gate
reported, or whose, because doing so would need a second window into CI. A human
reads it, or the CI Monitor grows a terminal that carries the distinction.

Usage:
    python3 scripts/ci/labels/check_blocking_labels.py

Exit code:
    0  no open pull request at HEAD_SHA carries a blocking label.
    1  at least one does, or the check could not be evaluated (missing
       configuration, an unparseable PR_LABELS value, or a GitHub API
       failure). gh_api retries only a 429, a 5xx, or a network error, so a
       single 403, which is how GitHub answers some rate-limit and
       abuse-detection cases, fails the gate on the first attempt. Failing
       closed is the point: a gate that cannot prove the merge is safe must
       not open it.

Required environment variables:
    GITHUB_TOKEN        Token with pull-requests: read
    GITHUB_REPOSITORY   Owner/repo (e.g. "aunger/gallery-button-for-pixel-camera")
    HEAD_SHA            github.event.pull_request.head.sha, the commit this
                        check run is stored against
    PR_NUMBER           github.event.pull_request.number
    PR_STATE            github.event.pull_request.state ("open" or "closed")
    PR_LABELS           toJson(github.event.pull_request.labels.*.name)
"""

import json
import os
import sys

import enforce_mutually_exclusive_labels as emxl

# ---------------------------------------------------------------------------
# Blocking labels
# ---------------------------------------------------------------------------

# Process state that must be resolved before a merge (issue #516). Every member
# is also a member of an enforce_mutually_exclusive_labels.MUTUALLY_EXCLUSIVE_SETS
# set, which is how scripts/ci/test_label_existence_integration.py confirms they
# all exist in the repository. Matching is case-insensitive.
BLOCKING_LABELS: frozenset[str] = frozenset(
    {
        "verification needed",
        "changes requested",
        "changes done",
        "orchestrating",
    }
)


def blocking_labels_in(labels: list[str]) -> list[str]:
    """Return the blocking labels among *labels*, lowercased, deduplicated, sorted."""
    return sorted({lbl.lower() for lbl in labels} & BLOCKING_LABELS)


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def fetch_open_prs_at_head(repo: str, token: str, head_sha: str) -> list[dict]:
    """Return every open pull request in *repo* whose head commit is *head_sha*.

    Filters the full open-PR listing locally, because no endpoint answers this
    question server-side. The list endpoint's `head` filter takes a branch
    name, and a branch is not what the check run is keyed on. The nearest
    alternative, GET /repos/{owner}/{repo}/commits/{sha}/pulls, matches every
    PR whose branch *contains* the commit rather than the PRs headed by it, so
    it would over-match every descendant PR. In this repo the listing is one
    API call in the ordinary case.
    """
    return [
        pr
        for pr in emxl.fetch_open_pull_requests(repo, token)
        if (pr.get("head") or {}).get("sha") == head_sha
    ]


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def label_names(pull_request: dict) -> list[str]:
    """Return the label names carried by an API pull request object."""
    return [lbl["name"] for lbl in pull_request.get("labels", [])]


def blocked_prs(candidates: dict[int, list[str]]) -> list[tuple[int, list[str]]]:
    """Return (pr number, blocking labels) for each candidate that carries one.

    Ordered by PR number so the failure output is stable.
    """
    blocked = []
    for number in sorted(candidates):
        blocking = blocking_labels_in(candidates[number])
        if blocking:
            blocked.append((number, blocking))
    return blocked


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    head_sha = os.environ.get("HEAD_SHA", "")
    pr_number_raw = os.environ.get("PR_NUMBER", "")
    pr_state = os.environ.get("PR_STATE", "")
    pr_labels_raw = os.environ.get("PR_LABELS", "")

    # Unlike the repo's other label scripts, missing configuration is fatal
    # rather than a skip: those scripts apply a convenience label, this one
    # decides whether a merge may proceed.
    missing = [
        name
        for name, value in (
            ("GITHUB_TOKEN", token),
            ("GITHUB_REPOSITORY", repo),
            ("HEAD_SHA", head_sha),
            ("PR_NUMBER", pr_number_raw),
            ("PR_STATE", pr_state),
            ("PR_LABELS", pr_labels_raw),
        )
        if not value
    ]
    if missing:
        print(
            f"Error: required environment variable(s) not set: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1

    try:
        pr_number = int(pr_number_raw)
    except ValueError:
        print(f"Error: PR_NUMBER is not a valid integer: {pr_number_raw!r}", file=sys.stderr)
        return 1

    try:
        event_labels = [str(name) for name in json.loads(pr_labels_raw)]
    except (ValueError, TypeError) as exc:
        print(
            f"Error: PR_LABELS is not a JSON array of names ({exc}): {pr_labels_raw!r}",
            file=sys.stderr,
        )
        return 1

    try:
        open_prs = fetch_open_prs_at_head(repo, token, head_sha)
    except Exception as exc:  # noqa: BLE001
        print(f"Error listing open pull requests at {head_sha}: {exc}", file=sys.stderr)
        print(
            "Cannot prove this commit is unblocked, so it is treated as blocked.", file=sys.stderr
        )
        return 1

    # The triggering PR's labels are taken from both sources and unioned rather
    # than either replacing the other, because each source is stale in the
    # direction the other covers. The payload is fixed at event time, so the
    # listing is what catches a label applied after this run's event fired,
    # which a superseded run would otherwise report as a clean commit. The
    # listing is a separate read, so the payload is what catches a listing that
    # has not yet caught up with a PR just opened or just labeled. Blocking on
    # either is also why the verdict is never weaker about the triggering PR
    # than the payload alone would have made it.
    #
    # The union can therefore name a label that is no longer applied: a
    # superseded run landing last, or a listing lagging a removal. That is
    # over-blocking, it is cleared by re-running the job, and it is the safe
    # direction; the alternative is a gate that opens on a stale reading.
    #
    # A closed triggering PR is the mirror case, and gets the opposite
    # treatment: it is dropped from both sources, the payload by its state and
    # the listing by its number. It cannot be merged, so its label must not
    # block a sibling that can. Dropping it from the listing too is what makes
    # the workflow's `closed` trigger a reliable recovery rather than a race:
    # that run is the last event the commit will see, so a copy of the listing
    # that has not yet caught up with the close would otherwise leave the
    # failing verdict the closing PR earned as the newest run on the commit,
    # with a sibling blocked by a label nothing carries and no automatic exit.
    # It cannot wrongly unblock, because a payload whose state is stale in the
    # other direction (closed here, reopened since) is followed by a `reopened`
    # event that re-runs this check.
    candidates: dict[int, list[str]] = {}
    if pr_state == "open":
        candidates[pr_number] = list(event_labels)
    for pull_request in open_prs:
        number = pull_request["number"]
        if number == pr_number and pr_state != "open":
            continue
        candidates.setdefault(number, []).extend(label_names(pull_request))

    listed = ", ".join(f"#{number}" for number in sorted(candidates)) or "none"
    print(f"Open pull request(s) whose head is {head_sha}: {listed}.")

    blocked = blocked_prs(candidates)
    if not blocked:
        print("OK: No blocking labels are present.")
        return 0

    for number, labels in blocked:
        blamed = (
            "This PR" if number == pr_number else f"PR #{number}, which shares this head commit,"
        )
        print(f"ERROR: {blamed} has a blocking label: {', '.join(labels)}")
    print("Remove the label once the blocking condition is resolved.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
