#!/usr/bin/env python3
"""Audit merged pull requests against the changed-file label rules.

Applies scripts/ci/labels/label_by_files.py's rules (see that module for the
exact rules) to the real changed-file list of every merged pull request, and
compares the verdict against the labels the pull request actually carries.

This does two jobs with one walk. It is the repeatable dry run that makes
re-running the evidence the cheapest regression check when the path map is
extended, and, with -f, the backfill for pull requests that merged before a
rule existed. label-by-files.yml only ever sees pull requests that are open
when it runs, so history is otherwise never revisited.

Before exiting, prints a JSON report of what happened (or would happen):

    add     labels the rules apply that the pull request lacks
    match   labels the rules apply that the pull request already carries
    remove  labels the pull request carries that the rules forbid

Only label_by_files.EXHAUSTIVE_LABELS can appear under "remove": the
unanimity labels are add-only by design, since for them "no changed path
justifies it" is an absence of evidence rather than evidence of absence.

Each category maps a label name to the sorted list of pull request numbers
it applies to.

Usage:
    python3 scripts/ci/labels/audit_labels_by_files.py (-n | -f) [-q] [--limit N]

    -n, --dry-run   Compute and print the report; apply nothing.
    -f, --force     Apply the "add" and "remove" categories, then print the
                    report.
    -q, --quiet     Suppress the JSON report.
    --limit N       Consider only the N most recently opened merged pull
                    requests.

    Exactly one of -n or -f is required, matching backfill_labels_by_title.py
    and git clean's semantics: the script refuses to guess and does nothing
    without one.

A pull request whose changed-file list may be truncated, or cannot be
fetched, yields no verdict at all: it is skipped and counted rather than
judged on a partial diff.

Cost is roughly one API call per pull request examined, against a 5000/hour
authenticated limit. --limit keeps a routine regression check after a
path-map change on the recent tail instead of the whole history.

Exit code:
    0  on success (whether or not any labels were changed)
    1  if required configuration is missing, --limit is not positive, the
       pull request list can't be fetched at all, or (-f only) writing
       labels to at least one pull request failed

Required environment variables:
    GITHUB_TOKEN        Token with issues: write and pull-requests: write
    GITHUB_REPOSITORY   Owner/repo (e.g. "aunger/gallery-button-for-pixel-camera")
"""

import argparse
import os
import sys
import urllib.error
from collections import defaultdict
from typing import Callable, NamedTuple

import backfill_labels_by_title
import enforce_mutually_exclusive_labels as emxl
import label_by_files
import label_by_title

PER_PAGE = 100


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def fetch_merged_pulls(repo: str, token: str, limit: int | None = None) -> list[dict]:
    """Return merged pull requests, most recently opened first.

    Walks repos/{repo}/pulls?state=closed newest-first and keeps the ones
    carrying a ``merged_at`` timestamp, since a closed pull request may
    simply have been closed unmerged. Stops as soon as *limit* of them have
    been collected, so a small --limit costs a page or two rather than a walk
    of the whole history.
    """
    pulls: list[dict] = []
    page = 1
    while True:
        batch = label_by_title.gh_api(
            f"repos/{repo}/pulls?state=closed&sort=created&direction=desc"
            f"&per_page={PER_PAGE}&page={page}",
            token=token,
        )
        if not batch:
            return pulls
        for pull in batch:
            if not pull.get("merged_at"):
                continue
            pulls.append(pull)
            if limit is not None and len(pulls) >= limit:
                return pulls
        page += 1


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


class Report(NamedTuple):
    """What the audit found, plus the writes -f would issue.

    add/match/remove map a label name to the pull request numbers it applies
    to (the report). to_add/to_remove map a pull request number to the labels
    to write (the -f payload). skipped lists the pull requests that yielded
    no verdict.
    """

    add: dict[str, list[int]]
    match: dict[str, list[int]]
    remove: dict[str, list[int]]
    to_add: dict[int, list[str]]
    to_remove: dict[int, list[str]]
    skipped: list[int]


def build_report(pulls: list[dict], fetch_paths: Callable[[int], list[str] | None]) -> Report:
    """Classify each pull request in *pulls* against its own changed files.

    *fetch_paths* maps a pull request number to its changed paths, or to None
    when no reliable list is available (label_by_files.fetch_changed_files's
    truncation contract, plus any per-pull-request fetch failure the caller
    folds into it). None is missing evidence, so that pull request is skipped
    entirely rather than judged on a partial diff.
    """
    add: dict[str, list[int]] = defaultdict(list)
    match: dict[str, list[int]] = defaultdict(list)
    remove: dict[str, list[int]] = defaultdict(list)
    to_add: dict[int, list[str]] = defaultdict(list)
    to_remove: dict[int, list[str]] = defaultdict(list)
    skipped: list[int] = []

    for pull in pulls:
        number = pull["number"]
        paths = fetch_paths(number)
        if paths is None:
            skipped.append(number)
            continue

        # Keyed by lowercase name, valued by the spelling GitHub returned, so
        # a removal deletes the label by the name the API knows.
        carried = {lbl["name"].lower(): lbl["name"] for lbl in pull.get("labels", [])}

        for label in label_by_files.matching_labels(paths):
            if label.lower() in carried:
                match[label].append(number)
            else:
                add[label].append(number)
                to_add[number].append(label)

        for label in label_by_files.labels_to_remove(paths):
            name = carried.get(label.lower())
            if name is not None:
                remove[label].append(number)
                to_remove[number].append(name)

    return Report(dict(add), dict(match), dict(remove), dict(to_add), dict(to_remove), skipped)


def apply_changes(
    to_add: dict[int, list[str]],
    to_remove: dict[int, list[str]],
    repo: str,
    token: str,
) -> bool:
    """Apply every pending add and removal, logging failures without aborting.

    Like backfill_labels_by_title.apply_labels, one pull request's failure
    does not abort the run: the remaining pull requests are still processed
    and the failure is reflected in the return value.

    Returns True if every write succeeded, False if any failed.
    """
    all_succeeded = True
    for number, labels in to_add.items():
        if not label_by_files.add_labels(repo, number, labels, token):
            all_succeeded = False
    for number, labels in to_remove.items():
        if not emxl.remove_labels(number, labels, repo, token, reason="unjustified"):
            all_succeeded = False
    return all_succeeded


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def format_report(report: Report) -> str:
    """Render *report*'s three categories in backfill_labels_by_title's shape."""
    return backfill_labels_by_title.format_categories(
        [("add", report.add), ("match", report.match), ("remove", report.remove)]
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("-n", "--dry-run", action="store_true", help="Report only; apply nothing.")
    mode.add_argument("-f", "--force", action="store_true", help="Apply the adds and removals.")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress the JSON report.")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Consider only the N most recently opened merged pull requests.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")

    if not token:
        print("Error: GITHUB_TOKEN not set.", file=sys.stderr)
        return 1
    if not repo:
        print("Error: GITHUB_REPOSITORY not set.", file=sys.stderr)
        return 1
    if args.limit is not None and args.limit < 1:
        print(f"Error: --limit must be at least 1: {args.limit}", file=sys.stderr)
        return 1

    try:
        pulls = fetch_merged_pulls(repo, token, args.limit)
    except Exception as exc:  # noqa: BLE001
        print(f"Error fetching pull requests: {exc}", file=sys.stderr)
        return 1

    def changed_paths(number: int) -> list[str] | None:
        try:
            return label_by_files.fetch_changed_files(repo, number, token)
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            print(f"Error fetching changed files for #{number}: {exc}", file=sys.stderr)
            return None

    report = build_report(pulls, changed_paths)

    exit_code = 0
    if args.force and not apply_changes(report.to_add, report.to_remove, repo, token):
        exit_code = 1

    if report.skipped:
        print(
            f"Skipped {len(report.skipped)} of {len(pulls)} pull requests with no reliable "
            f"changed-file list: {sorted(report.skipped)}",
            file=sys.stderr,
        )

    if not args.quiet:
        print(format_report(report))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
