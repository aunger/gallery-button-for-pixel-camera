#!/usr/bin/env python3
"""Copy a linked issue's labels onto a pull request that closes it.

When a pull request closes an issue via a closing-keyword relationship
("Fixes #N", "Closes #N", and the other variants GitHub recognizes) or via a
manually linked issue, this applies each of that issue's labels to the pull
request.

The relationship is detected through the GitHub GraphQL API's
``closingIssuesReferences`` field on the pull request, which is GitHub's own
detection of closing keywords and cross-references--not a regex scan of the
PR body, so every keyword variant and manually linked issue is covered.

Labels are only ever added, never removed. A label already present on the PR
is left alone. A candidate label is also skipped, rather than applied, when
applying it would cause scripts/ci/labels/enforce_mutually_exclusive_labels.py's
mutual-exclusion enforcement to remove a label the PR already carries--an
existing PR label always wins over a conflicting label being copied in from a
linked issue.

An issue's own orchestration-cycle state labels (PROCESS_STATE_LABELS, e.g.
`orchestrating`, `verification needed`, `changes done`) are never propagated
at all, even onto a PR that carries none of their conflicting siblings.
agents/dev_orchestration.md deliberately lets an issue and its PR diverge on
these mid-cycle (for example, `orchestrating` is removed from the PR alone,
not the issue, to clear the "No blocking labels" merge gate while
orchestration is still active), so blindly copying them from issue to PR
fights that state machine instead of just adding a convenience label.

Labels a pull request's own changed files determine under an if-and-only-if
rule (FILE_DETERMINED_LABELS, e.g. `agents`) are likewise never propagated:
what a linked issue is about is not evidence about the PR's diff. See
labels_to_propagate() for the exact rule.

The pull_request trigger for this module's main() (see
.github/workflows/propagate-issue-labels.yml) covers most sidebar-linked PRs
by re-running on their next ordinary activity, since GitHub fires no webhook
event for the sidebar-link action itself. This module is also imported by
scripts/ci/labels/reconcile_issue_labels.py, an on-demand tool that applies the same
rule across every open PR for the residual case of a PR that sees no further
activity before merge. See reconcile_issue_labels.py's docstring for the
full rationale.

Usage:
    python3 scripts/ci/labels/propagate_issue_labels.py

Exit code:
    0  the PR has no closing issue references, its closing issues carry no
       labels, every eligible label was applied successfully, or every
       candidate was skipped due to a mutual-exclusion conflict or excluded
       as orchestration-cycle state or file-determined (none of these is
       itself a failure).
    1  required configuration is missing/invalid, or fetching or applying
       labels failed.

Required environment variables:
    GITHUB_TOKEN        Token with issues: write and pull-requests: write
    GITHUB_REPOSITORY   Owner/repo (e.g. "aunger/gallery-button-for-pixel-camera")
    PR_NUMBER           Number of the pull request
"""

import json
import os
import sys
import urllib.error
import urllib.request

import enforce_mutually_exclusive_labels as emxl
import label_by_title

# ---------------------------------------------------------------------------
# GraphQL
# ---------------------------------------------------------------------------

CLOSING_ISSUES_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      closingIssuesReferences(first: 50) {
        nodes {
          number
          repository {
            nameWithOwner
          }
          labels(first: 100) {
            nodes {
              name
            }
          }
        }
      }
    }
  }
}
"""


def graphql_query(query: str, variables: dict, token: str) -> dict:
    """Execute a GitHub GraphQL query and return its "data" object.

    Raises ``RuntimeError`` if the response carries a top-level "errors"
    list (GraphQL reports errors this way even on an HTTP 200), or
    ``urllib.error.HTTPError``/``URLError`` on request failure.
    """
    url = "https://api.github.com/graphql"
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(url, method="POST", data=body)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    # URL is a fixed GitHub API constant; the file:// risk does not apply.
    with urllib.request.urlopen(req) as r:  # nosemgrep
        payload = json.loads(r.read())
    if payload.get("errors"):
        raise RuntimeError(f"GraphQL errors: {payload['errors']}")
    return payload.get("data") or {}


def fetch_closing_issue_labels(
    owner: str,
    name: str,
    pr_number: int,
    token: str,
) -> list[str]:
    """Return the labels of every same-repo issue that *pr_number* closes.

    Cross-repository closing references (a PR in this repo closing an issue
    in a different repo) are skipped--this repo's label automation is scoped
    to a single repository throughout, matching the other scripts here.

    Labels are returned in the order GitHub lists the closing issues and,
    within each issue, the order it lists that issue's labels. Duplicates
    across multiple closing issues are not removed here; that happens in
    labels_to_propagate().
    """
    data = graphql_query(
        CLOSING_ISSUES_QUERY,
        {"owner": owner, "name": name, "number": pr_number},
        token,
    )
    pull_request = (data.get("repository") or {}).get("pullRequest")
    if not pull_request:
        return []
    nodes = (pull_request.get("closingIssuesReferences") or {}).get("nodes") or []
    repo_full_name = f"{owner}/{name}"

    labels: list[str] = []
    for node in nodes:
        if not node:
            continue
        node_repo = (node.get("repository") or {}).get("nameWithOwner")
        if node_repo != repo_full_name:
            continue
        labels.extend(lbl["name"] for lbl in (node.get("labels") or {}).get("nodes") or [])
    return labels


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

# Labels agents/dev_orchestration.md manages as orchestration-cycle state,
# not ordinary content/category labels (contrast with e.g. "p1"-"p3", "ci",
# "bug", which are fine to copy as-is). The issue and its PR are deliberately
# allowed, or required, to carry these differently while a PR is under
# active orchestration:
#   - `orchestrate`/`orchestrating` is applied to both when orchestration
#     starts, but `orchestrating` is removed from the PR alone (not the
#     issue) to clear the "No blocking labels" merge gate mid-cycle
#     (dev_orchestration.md's labelGateBlock), while the issue keeps it for
#     the rest of the active cycle.
#   - `changes requested`/`changes done` transitions apply "to the PR if one
#     exists; apply it to the issue otherwise"--once a PR exists, these live
#     on the PR only, and the issue's copy (if any) goes stale by design.
#   - `verification needed`/`verified` are likewise driven by the PR's own
#     CI/verification state, not the issue's.
# Blindly copying any of these from issue to PR does not add a harmless
# convenience label: it fights this state machine and can re-obstruct a
# merge gate an Orchestrator just cleared (observed live on PR #713, where a
# routine push re-applied "orchestrating" moments after it had been removed
# from the PR alone). So these are never eligible for propagation, even onto
# a PR that carries none of their conflicting siblings.
PROCESS_STATE_LABELS: frozenset[str] = frozenset(
    {
        "orchestrate",
        "orchestrating",
        "verification needed",
        "verified",
        "changes requested",
        "changes done",
    }
)

# Labels a PR's own changed files determine under an if-and-only-if rule (see
# scripts/ci/labels/label_by_files.py). What a linked issue is *about* is not
# evidence about the PR's diff, so copying one of these across would
# contradict the PR's own files. It would also fight the file signal on
# timing: propagation runs on `opened` and on body edits, the file signal on
# `opened` and `synchronize`, so a propagated label would sit on the PR until
# the next push. Observed on 5 of the 21 PRs in issue #785's dry run that
# carry `agents` without touching any agents path.
#
# References label_by_title.FILE_DETERMINED_LABELS directly rather than
# repeating its own literal, for the same reason label_by_files.py does: three
# independently written copies of "agents" could silently drift apart, which
# would break the "exactly one writer owns this label on a PR" precedence
# claim without any test noticing. See the comment on
# label_by_title.FILE_DETERMINED_LABELS for the full rationale.
#
# Disjoint from PROCESS_STATE_LABELS by construction, so the two exclusion
# reasons partition the excluded list cleanly for logging.
FILE_DETERMINED_LABELS: frozenset[str] = label_by_title.FILE_DETERMINED_LABELS


def labels_to_propagate(
    current_pr_labels: list[str],
    issue_labels: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """Partition *issue_labels* into what to add, skip, and exclude.

    Returns (to_add, skipped, excluded):
        to_add    issue labels, not already on the PR, that are safe to add
        skipped   issue labels omitted because applying them would cause
                  enforce_mutually_exclusive_labels.py to remove a label
                  already staged on the PR (a real current PR label, or one
                  accepted earlier in this same call)
        excluded  issue labels never considered at all: PROCESS_STATE_LABELS
                  (orchestration-cycle state the issue and its PR are allowed
                  to carry differently by design) and FILE_DETERMINED_LABELS
                  (decided from the PR's own changed files, so the issue is
                  not evidence about them)

    A label already present on the PR (case-insensitively) is dropped from
    every list--there is nothing to do for it. A label repeated across
    multiple closing issues is considered once.

    *issue_labels* is processed in order, staging each accepted label into a
    working copy of the PR's labels so later candidates in the same call see
    earlier additions. This keeps one propagation run internally consistent
    when several linked issues supply conflicting labels, while a real
    pre-existing PR label always wins, since it seeds the working set before
    any candidate is considered.
    """
    staged = list(current_pr_labels)
    already_lower = {lbl.lower() for lbl in current_pr_labels}
    to_add: list[str] = []
    skipped: list[str] = []
    excluded: list[str] = []
    seen_lower: set[str] = set()

    for label in issue_labels:
        lower = label.lower()
        if lower in seen_lower:
            continue
        seen_lower.add(lower)

        if lower in PROCESS_STATE_LABELS or lower in FILE_DETERMINED_LABELS:
            excluded.append(label)
            continue
        if lower in already_lower:
            continue

        conflict = False
        label_set = emxl.find_conflicting_set(label)
        if label_set is not None and emxl.labels_to_remove(label, staged, label_set):
            conflict = True
        prefix = emxl.find_conflicting_prefix(label)
        if not conflict and prefix is not None:
            if emxl.labels_to_remove_by_prefix(label, staged, prefix):
                conflict = True

        if conflict:
            skipped.append(label)
            continue

        to_add.append(label)
        staged.append(label)

    return to_add, skipped, excluded


# ---------------------------------------------------------------------------
# Single-PR propagation (shared by main() and reconcile_issue_labels.py)
# ---------------------------------------------------------------------------


def propagate_to_pr(owner: str, name: str, repo: str, pr_number: int, token: str) -> bool:
    """Propagate *pr_number*'s closing issues' labels onto it.

    Returns True on success, including every no-op case (no closing issues,
    no labels on them, or every candidate skipped as a conflict). Returns
    False only if fetching the closing issues, fetching the PR, or applying
    labels failed.
    """
    try:
        issue_labels = fetch_closing_issue_labels(owner, name, pr_number, token)
    except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as exc:
        print(f"Error fetching closing issues for PR #{pr_number}: {exc}", file=sys.stderr)
        return False

    if not issue_labels:
        print(f"PR #{pr_number} has no labeled closing issue references--nothing to do.")
        return True

    try:
        pr = emxl.gh_api(f"repos/{repo}/issues/{pr_number}", token=token)
    except Exception as exc:  # noqa: BLE001
        print(f"Error fetching PR #{pr_number}: {exc}", file=sys.stderr)
        return False

    if not isinstance(pr, dict):
        print(f"Error: unexpected response fetching PR #{pr_number}: {pr!r}", file=sys.stderr)
        return False

    current_pr_labels = [lbl["name"] for lbl in pr.get("labels", [])]

    to_add, skipped, excluded = labels_to_propagate(current_pr_labels, issue_labels)

    # The two exclusions have different reasons, and the Actions log is the
    # only debugging surface these scripts have, so they are reported apart.
    process_state = [lbl for lbl in excluded if lbl.lower() in PROCESS_STATE_LABELS]
    file_determined = [lbl for lbl in excluded if lbl.lower() in FILE_DETERMINED_LABELS]

    if process_state:
        print(
            f"Excluding orchestration-cycle labels {process_state} on PR #{pr_number}: the "
            "issue and its PR are allowed to carry these differently while orchestration "
            "is active (see agents/dev_orchestration.md)."
        )

    if file_determined:
        print(
            f"Excluding file-determined labels {file_determined} on PR #{pr_number}: these "
            "are decided from the PR's own changed files, so what the linked issue is about "
            "is not evidence about them (see scripts/ci/labels/label_by_files.py)."
        )

    if skipped:
        print(
            f"Skipping labels {skipped} on PR #{pr_number}: applying them would let "
            "mutual-exclusion enforcement remove a label the PR already carries."
        )

    if not to_add:
        print(f"No labels to propagate to PR #{pr_number}--nothing to do.")
        return True

    try:
        emxl.gh_api(
            f"repos/{repo}/issues/{pr_number}/labels",
            token=token,
            method="POST",
            body={"labels": to_add},
        )
        print(f"Applied labels {to_add} to PR #{pr_number}.")
    except urllib.error.HTTPError as exc:
        print(f"Error applying labels {to_add} to PR #{pr_number}: {exc}", file=sys.stderr)
        return False
    except urllib.error.URLError as exc:
        print(f"Network error applying labels {to_add} to PR #{pr_number}: {exc}", file=sys.stderr)
        return False

    return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    pr_number_str = os.environ.get("PR_NUMBER", "")

    if not token:
        print("Error: GITHUB_TOKEN not set.", file=sys.stderr)
        return 1
    if not repo:
        print("Error: GITHUB_REPOSITORY not set.", file=sys.stderr)
        return 1
    if not pr_number_str:
        print("Error: PR_NUMBER not set.", file=sys.stderr)
        return 1
    if "/" not in repo:
        print(f"Error: GITHUB_REPOSITORY is not owner/repo: {repo!r}", file=sys.stderr)
        return 1

    try:
        pr_number = int(pr_number_str)
    except ValueError:
        print(f"Error: PR_NUMBER is not a valid integer: {pr_number_str!r}", file=sys.stderr)
        return 1

    owner, name = repo.split("/", 1)

    return 0 if propagate_to_pr(owner, name, repo, pr_number, token) else 1


if __name__ == "__main__":
    sys.exit(main())
