#!/usr/bin/env python3
"""Backfill title-based labels across every existing issue and pull request.

Applies the same rules as scripts/ci/labels/label_by_title.py (see that module for
the exact regexes) to the title of every issue and pull request in the
repository, and applies any label a title matches that is not already
present. Labels are only ever added, never removed.

On a pull request, label_by_title.FILE_DETERMINED_LABELS are neither applied
nor reported: those are decided from the PR's own changed files, so applying
one from a title would reapply exactly what the if-and-only-if rule forbids.
scripts/ci/labels/audit_labels_by_files.py is the tool that covers them.

Before exiting, prints a JSON report of what happened (or would happen):

    add     labels that would be applied and are not already present
    match   labels that would be applied and are already present
    miss    labels in TRACKED_LABELS that are already present on an
            issue/PR but that the title-matching rules would not apply

Each category maps a label name to the sorted list of issue/PR numbers
it applies to.

Usage:
    python3 scripts/ci/labels/backfill_labels_by_title.py (-n | -f) [-q] [--skip-matches[=LABELS]]

    -n, --dry-run   Compute and print the report; apply nothing.
    -f, --force     Apply the "add" category, then print the report.
    -q, --quiet     Suppress the JSON report.
    --skip-matches[=LABELS]
                    Omit LABELS (comma-separated) from the "match" report
                    category. Given with no LABELS, omit the "match"
                    category entirely.

    Exactly one of -n or -f is required, matching git clean's semantics:
    the script refuses to guess and does nothing without one.

Exit code:
    0  on success (whether or not any labels were applied)
    1  if required configuration is missing, the issue list can't be
       fetched at all, or (-f only) applying labels to at least one
       issue/PR failed

Required environment variables:
    GITHUB_TOKEN        Token with issues: write and pull-requests: write
    GITHUB_REPOSITORY   Owner/repo (e.g. "aunger/gallery-button-for-pixel-camera")
"""

import argparse
import json
import os
import sys
import urllib.error
from collections import defaultdict

import label_by_title

# ---------------------------------------------------------------------------
# Tracked labels
# ---------------------------------------------------------------------------

# Every label the title-matching rules can currently apply. A label with no
# rule (e.g. "product") can never appear in "matched", so it would sit in
# "miss" on every single run for every issue/PR that carries it--permanent
# noise that buries genuine rule drift instead of signaling it.
TRACKED_LABELS: frozenset[str] = frozenset(label_by_title.LABEL_PATTERNS)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def fetch_all_issues(repo: str, token: str) -> list[dict]:
    """Return every issue and pull request in *repo*, open or closed.

    The /issues endpoint returns both; pull requests carry a
    ``pull_request`` key but are labeled identically to issues.
    """
    items: list[dict] = []
    page = 1
    while True:
        batch = label_by_title.gh_api(
            f"repos/{repo}/issues?state=all&per_page=100&page={page}",
            token=token,
        )
        if not batch:
            break
        items.extend(batch)
        page += 1
    return items


def build_report(
    items: list[dict],
) -> tuple[
    dict[str, list[int]],
    dict[str, list[int]],
    dict[str, list[int]],
    dict[int, list[str]],
]:
    """Classify every item's title-matched labels against its current labels.

    Returns (add, match, miss, to_apply):
        add[label]        issue numbers missing a label the rules would apply
        match[label]       issue numbers already carrying a label the rules apply
        miss[label]        issue numbers carrying a TRACKED_LABELS label the
                            rules would not apply
        to_apply[number]   labels to add to that issue/PR (the -f payload)
    """
    add: dict[str, list[int]] = defaultdict(list)
    match: dict[str, list[int]] = defaultdict(list)
    miss: dict[str, list[int]] = defaultdict(list)
    to_apply: dict[int, list[str]] = defaultdict(list)

    for item in items:
        number = item["number"]
        current = {lbl["name"] for lbl in item.get("labels", [])}
        # The /issues endpoint marks a pull request with a "pull_request" key.
        is_pr = "pull_request" in item
        matched = set(label_by_title.matching_labels(item["title"], is_pull_request=is_pr))
        # Narrowing "tracked" as well as "matched" matters: leave it alone and
        # every PR carrying a file-determined label lands in "miss" on every
        # run, which is precisely the permanent noise TRACKED_LABELS exists to
        # avoid.
        tracked = (
            TRACKED_LABELS - label_by_title.FILE_DETERMINED_LABELS if is_pr else TRACKED_LABELS
        )

        for label in matched:
            if label in current:
                match[label].append(number)
            else:
                add[label].append(number)
                to_apply[number].append(label)

        for label in tracked:
            if label in current and label not in matched:
                miss[label].append(number)

    return add, match, miss, to_apply


def apply_labels(to_apply: dict[int, list[str]], repo: str, token: str) -> bool:
    """POST each issue/PR's missing labels, logging failures without aborting.

    Returns True if every application succeeded, False if any failed.
    """
    all_succeeded = True
    for number, labels in to_apply.items():
        try:
            label_by_title.gh_api(
                f"repos/{repo}/issues/{number}/labels",
                token=token,
                method="POST",
                body={"labels": labels},
            )
            print(f"Applied labels {labels} to #{number}.")
        except urllib.error.HTTPError as exc:
            print(f"Error applying labels {labels} to #{number}: {exc}", file=sys.stderr)
            all_succeeded = False
        except urllib.error.URLError as exc:
            print(f"Network error applying labels {labels} to #{number}: {exc}", file=sys.stderr)
            all_succeeded = False
    return all_succeeded


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def format_categories(categories: list[tuple[str, dict[str, list[int]]]]) -> str:
    """Render named label-to-issue-numbers *categories* as JSON.

    Minimized except for a newline after each ``"category":`` and after
    each label's number list, per the requested output shape. Shared with
    scripts/ci/labels/audit_labels_by_files.py so both reports have the
    same shape.
    """
    category_parts = []
    for name, labels in categories:
        label_parts = [
            f"{json.dumps(label)}:{json.dumps(sorted(labels[label]), separators=(',', ':'))}\n"
            for label in sorted(labels)
        ]
        category_parts.append(f"{json.dumps(name)}:\n{{{','.join(label_parts)}}}")
    return "{" + ",".join(category_parts) + "}"


def format_report(
    add: dict[str, list[int]],
    match: dict[str, list[int]],
    miss: dict[str, list[int]],
    include_match: bool = True,
) -> str:
    """Render the add/match/miss categories as JSON.

    Pass ``include_match=False`` to omit the "match" key entirely (the
    ``--skip-matches`` flag given with no value).
    """
    categories = [("add", add)]
    if include_match:
        categories.append(("match", match))
    categories.append(("miss", miss))
    return format_categories(categories)


def parse_skip_matches(value: str | None) -> tuple[bool, frozenset[str]]:
    """Interpret the --skip-matches value.

    Returns (include_match, labels_to_skip):
        value is None   flag not given: include "match" in full
        value is ""     flag given with no LABELS: omit "match" entirely
        value is a CSV  flag given with LABELS: include "match" minus those

    Each comma-separated entry is stripped of surrounding whitespace and,
    since a shell only strips one layer of quoting, a further layer of
    literal quote characters (e.g. --skip-matches='"ci","agents"').
    """
    if value is None:
        return True, frozenset()
    if value == "":
        return False, frozenset()
    labels = frozenset(
        stripped for label in value.split(",") if (stripped := label.strip().strip("'\""))
    )
    return True, labels


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("-n", "--dry-run", action="store_true", help="Report only; apply nothing.")
    mode.add_argument("-f", "--force", action="store_true", help="Apply the missing labels.")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress the JSON report.")
    parser.add_argument(
        "--skip-matches",
        nargs="?",
        const="",
        default=None,
        metavar="LABELS",
        help=(
            'Omit LABELS (comma-separated) from the "match" report category. '
            'With no LABELS, omit the "match" category entirely.'
        ),
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

    try:
        items = fetch_all_issues(repo, token)
    except Exception as exc:  # noqa: BLE001
        print(f"Error fetching issues: {exc}", file=sys.stderr)
        return 1

    add, match, miss, to_apply = build_report(items)

    exit_code = 0
    if args.force and not apply_labels(to_apply, repo, token):
        exit_code = 1

    if not args.quiet:
        include_match, labels_to_skip = parse_skip_matches(args.skip_matches)
        if labels_to_skip:
            match = {label: nums for label, nums in match.items() if label not in labels_to_skip}
        print(format_report(add, match, miss, include_match=include_match))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
