#!/usr/bin/env python3
"""Apply labels to a pull request based on the set of files it changed.

Unlike label_by_title.py (which matches a title against a regex per label),
this module classifies each *changed path* and applies a label only when
every changed path justifies it. See matching_labels() below for the exact
rule--not duplicated here, to avoid this docstring drifting out of sync with
the code.

Two properties keep the rule safe:

- An unclassified path contributes the empty label set, which empties the
  intersection across all paths. So any file the map below does not
  recognize disables the file signal entirely for that PR: adding a path
  mapping can only ever make the signal fire more, and a gap in the map can
  only ever make it fire less.
- The empty file list returns [] (no labels) rather than vacuously matching
  every label, since intersecting zero sets has no defined result.

Labels are only ever added, never removed.

Usage:
    python3 scripts/ci/labels/label_by_files.py

Exit code:
    0  no labels matched, the changed-file list may be truncated, or labels
       were applied successfully.
    1  required configuration is missing/invalid, or a GitHub API call
       failed.

Required environment variables:
    GITHUB_TOKEN        Personal access token or Actions secret with
                        issues: write and pull-requests: write
    GITHUB_REPOSITORY   Owner/repo (e.g. "aunger/gallery-button-for-pixel-camera")
    PR_NUMBER           Number of the pull request
"""

import os
import re
import sys
import urllib.error

import label_by_title

# ---------------------------------------------------------------------------
# Path classification
# ---------------------------------------------------------------------------

TEST_DIR_SEGMENTS = frozenset({"test", "androidTest", "sharedTest"})
TEST_SCRIPT_BASENAME = re.compile(r"test_.+\.(py|sh)\Z")

# scripts/ci/ holds the repository's CI automation itself, so it maps to "ci"
# alongside the workflow YAML that invokes it. This repo's most common CI
# change is "a script under scripts/ci/ plus its test_* sibling": without the
# mapping the script is unclassified, which empties the intersection and the
# PR gets nothing; with it the script scores {ci} and its test scores
# {ci, automated tests}, so unanimity fires on {ci}.
#
# scripts/lint/ and scripts/ci_monitor/ are deliberately excluded: lint.sh is
# a local pre-commit tool as much as a CI step, and ci_monitor is the
# Orchestrator's own tool rather than repo CI.
CI_PATH_PREFIXES = (".github/workflows/", "scripts/ci/")


def labels_for_path(path: str) -> frozenset[str]:
    """Return the labels *path* alone justifies (empty when it justifies none)."""
    segments = path.split("/")
    labels = set()
    # segments[:-1] restricts this check to directories, so a file literally
    # named "test" is not itself a test.
    if TEST_DIR_SEGMENTS.intersection(segments[:-1]):
        labels.add("automated tests")
    if TEST_SCRIPT_BASENAME.match(segments[-1]):
        labels.add("automated tests")
    if path.startswith(CI_PATH_PREFIXES):
        labels.add("ci")
    return frozenset(labels)


def matching_labels(paths: list[str]) -> list[str]:
    """Return the labels every path in *paths* justifies; [] when *paths* is empty."""
    if not paths:
        return []
    return sorted(frozenset.intersection(*(labels_for_path(p) for p in paths)))


# ---------------------------------------------------------------------------
# GitHub API helper
# ---------------------------------------------------------------------------

# 10 pages of 100 files each. A PR with more than 1000 changed files is not a
# case worth optimizing for; fetch_changed_files() returns None rather than
# risk a verdict from a partial file list.
MAX_PAGES = 10
PER_PAGE = 100


def fetch_changed_files(repo: str, pr_number: int, token: str) -> list[str] | None:
    """Return every path changed in the pull request, or None if possibly truncated.

    Paginates GET pulls/{pr_number}/files?per_page=100. Stops as soon as a
    page comes back shorter than PER_PAGE (the normal end of the list) or
    empty. If MAX_PAGES pages are consumed without ever seeing a short page,
    the file list may continue beyond what was fetched, so this returns
    None instead of a partial list.
    """
    paths: list[str] = []
    page = 1
    while page <= MAX_PAGES:
        batch = label_by_title.gh_api(
            f"repos/{repo}/pulls/{pr_number}/files?per_page={PER_PAGE}&page={page}",
            token=token,
        )
        if not batch:
            return paths
        paths.extend(item["filename"] for item in batch)
        if len(batch) < PER_PAGE:
            return paths
        page += 1
    return None


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

    try:
        pr_number = int(pr_number_str)
    except ValueError:
        print(f"Error: PR_NUMBER is not a valid integer: {pr_number_str!r}", file=sys.stderr)
        return 1

    try:
        paths = fetch_changed_files(repo, pr_number, token)
    except urllib.error.HTTPError as exc:
        print(f"Error fetching changed files for #{pr_number}: {exc}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Network error fetching changed files for #{pr_number}: {exc}", file=sys.stderr)
        return 1

    if paths is None:
        print(
            f"Changed-file list for #{pr_number} may be truncated "
            f"(hit {MAX_PAGES} pages)--nothing to do."
        )
        return 0

    labels = matching_labels(paths)
    if not labels:
        print(f"No label rules matched the changed files on #{pr_number}--nothing to do.")
        return 0

    try:
        label_by_title.gh_api(
            f"repos/{repo}/issues/{pr_number}/labels",
            token=token,
            method="POST",
            body={"labels": labels},
        )
        print(f"Applied labels {labels} to #{pr_number}.")
    except urllib.error.HTTPError as exc:
        print(f"Error applying labels {labels} to #{pr_number}: {exc}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Network error applying labels {labels} to #{pr_number}: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
