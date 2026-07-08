#!/usr/bin/env python3
"""Backfill title-based labels across every existing issue and pull request.

Applies the same rules as scripts/label_by_title.py (see that module for
the exact regexes) to the title of every issue and pull request in the
repository, and applies any label a title matches that is not already
present. Labels are only ever added, never removed.

Before exiting, prints a JSON report of what happened (or would happen):

    add     labels that would be applied and are not already present
    match   labels that would be applied and are already present
    miss    labels in TRACKED_LABELS that are already present on an
            issue/PR but that the title-matching rules would not apply

Each category maps a label name to the sorted list of issue/PR numbers
it applies to.

Usage:
    python3 scripts/backfill_labels_by_title.py (-n | -f)

    -n, --dry-run   Compute and print the report; apply nothing.
    -f, --force     Apply the "add" category, then print the report.

    Exactly one of -n or -f is required, matching git clean's semantics:
    the script refuses to guess and does nothing without one.

Exit code:
    0  on success (whether or not any labels were applied)
    1  if required configuration is missing, or the issue list can't be
       fetched at all

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

# "product" plus every label the title-matching rules can currently apply.
TRACKED_LABELS: frozenset[str] = frozenset({"product"} | set(label_by_title.LABEL_PATTERNS))


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
        matched = set(label_by_title.matching_labels(item["title"]))

        for label in matched:
            if label in current:
                match[label].append(number)
            else:
                add[label].append(number)
                to_apply[number].append(label)

        for label in TRACKED_LABELS:
            if label in current and label not in matched:
                miss[label].append(number)

    return add, match, miss, to_apply


def apply_labels(to_apply: dict[int, list[str]], repo: str, token: str) -> None:
    """POST each issue/PR's missing labels, logging failures without aborting."""
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
        except urllib.error.URLError as exc:
            print(f"Network error applying labels {labels} to #{number}: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def format_report(
    add: dict[str, list[int]],
    match: dict[str, list[int]],
    miss: dict[str, list[int]],
) -> str:
    """Render the add/match/miss categories as JSON.

    Minimized except for a newline after each ``"category":`` and after
    each label's number list, per the requested output shape.
    """
    categories = {"add": add, "match": match, "miss": miss}
    category_parts = []
    for name, labels in categories.items():
        label_parts = [
            f"{json.dumps(label)}:{json.dumps(sorted(labels[label]), separators=(',', ':'))}\n"
            for label in sorted(labels)
        ]
        category_parts.append(f"{json.dumps(name)}:\n{{{','.join(label_parts)}}}")
    return "{" + ",".join(category_parts) + "}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("-n", "--dry-run", action="store_true", help="Report only; apply nothing.")
    mode.add_argument("-f", "--force", action="store_true", help="Apply the missing labels.")
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

    if args.force:
        apply_labels(to_apply, repo, token)

    print(format_report(add, match, miss))
    return 0


if __name__ == "__main__":
    sys.exit(main())
