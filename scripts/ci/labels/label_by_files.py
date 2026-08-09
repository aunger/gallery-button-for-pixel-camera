#!/usr/bin/env python3
"""Apply labels to a pull request based on the set of files it changed.

Unlike label_by_title.py (which matches a title against a regex per label),
this module classifies each *changed path* and derives the pull request's
labels from those classifications. Two rule shapes coexist; UNANIMOUS_LABELS
and EXHAUSTIVE_LABELS below say which labels take which shape, and
matching_labels()/labels_to_remove() carry the exact rules--not duplicated
here, to avoid this docstring drifting out of sync with the code.

Two properties keep the add-only unanimity shape safe:

- An unclassified path contributes the empty label set, which empties the
  intersection across all paths. So any file the map below does not
  recognize disables that half of the signal entirely for that PR: adding a
  path mapping can only ever make it fire more, and a gap in the map can
  only ever make it fire less.
- The empty file list returns [] (no labels) rather than vacuously matching
  every label, since intersecting zero sets has no defined result.

The if-and-only-if shape both adds and removes, which is sound only because
the path map *is* that label's complete definition. It stays honest about
missing evidence: an empty changed-file list, and one that may be truncated,
each yield no verdict at all--neither an add nor a removal.

Usage:
    python3 scripts/ci/labels/label_by_files.py

Exit code:
    0  no labels matched, the changed-file list may be truncated, or every
       label was applied and removed successfully (a 404 on removal means
       the label was already gone and is not a failure).
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

import enforce_mutually_exclusive_labels as emxl
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

# The repository owner's "agents" path set, recorded on issue #775 as the
# globs **/AGENTS.md, **/CLAUDE.md, **/agents/**/*, and .claude/**/*. The
# first two become basename matches, the third a directory segment at any
# depth, and the fourth a root-anchored prefix, matching how each was
# anchored. Note that .github/workflows/CLAUDE.md therefore justifies both
# "agents" and "ci", which is the owner's stated intent.
#
# Matching is case-sensitive, consistent with the androidTest and sharedTest
# segments above: a lowercase docs/agents.md is a different document, not a
# stale spelling of AGENTS.md.
AGENTS_BASENAMES = frozenset({"AGENTS.md", "CLAUDE.md"})
AGENTS_DIR_SEGMENT = "agents"
AGENTS_PATH_PREFIXES = (".claude/",)


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
    # segments[:-1] for the same reason: a file literally named "agents" is
    # not the directory the owner's **/agents/**/* glob names.
    if (
        segments[-1] in AGENTS_BASENAMES
        or AGENTS_DIR_SEGMENT in segments[:-1]
        or path.startswith(AGENTS_PATH_PREFIXES)
    ):
        labels.add("agents")
    return frozenset(labels)


# Apply only when *every* changed path justifies it. "Not every path" is an
# absence of evidence, not evidence of absence, so these are never removed:
# the dry run on issue #785 found the title rule legitimately applying `ci`
# to 63 of 220 recent merged PRs whose changed paths justify none of it, and
# `automated tests` to another 28.
UNANIMOUS_LABELS = frozenset({"ci", "automated tests"})

# The path map above is this label's complete definition, so "no changed path
# justifies it" *is* evidence of "not this label". Apply when any changed path
# justifies it, regardless of what else the PR changes; remove when none does.
# This is the if-and-only-if rule the repository owner recorded on issue #775.
EXHAUSTIVE_LABELS = frozenset({"agents"})


def matching_labels(paths: list[str]) -> list[str]:
    """Return the labels to apply to a PR changing *paths*; [] when *paths* is empty.

    Restricting the intersection to UNANIMOUS_LABELS and the union to
    EXHAUSTIVE_LABELS is what keeps the two rule shapes from contaminating
    each other.
    """
    if not paths:
        return []
    per_path = [labels_for_path(p) for p in paths]
    unanimous = frozenset.intersection(*per_path) & UNANIMOUS_LABELS
    existential = frozenset().union(*per_path) & EXHAUSTIVE_LABELS
    return sorted(unanimous | existential)


def labels_to_remove(paths: list[str]) -> list[str]:
    """Return the EXHAUSTIVE_LABELS no path in *paths* justifies; [] when *paths* is empty.

    This answers "which labels does the diff forbid", not "which labels does
    the PR carry"; filtering down to the ones actually present is the
    caller's job (see remove_unjustified_labels).
    """
    if not paths:
        return []
    justified = frozenset().union(*(labels_for_path(p) for p in paths))
    return sorted(EXHAUSTIVE_LABELS - justified)


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


def fetch_current_labels(repo: str, pr_number: int, token: str) -> list[str]:
    """Return the label names the pull request currently carries.

    Raises ``RuntimeError`` if the response is not the expected object, and
    the usual urllib errors on request failure.
    """
    pr = emxl.gh_api(f"repos/{repo}/issues/{pr_number}", token=token)
    if not isinstance(pr, dict):
        raise RuntimeError(f"unexpected response fetching #{pr_number}: {pr!r}")
    return [lbl["name"] for lbl in pr.get("labels", [])]


# ---------------------------------------------------------------------------
# Label writes
# ---------------------------------------------------------------------------


def add_labels(repo: str, pr_number: int, labels: list[str], token: str) -> bool:
    """Apply *labels* to the pull request. Returns True on success."""
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
        return False
    except urllib.error.URLError as exc:
        print(f"Network error applying labels {labels} to #{pr_number}: {exc}", file=sys.stderr)
        return False
    return True


def remove_unjustified_labels(repo: str, pr_number: int, labels: list[str], token: str) -> bool:
    """Remove whichever of *labels* the pull request actually carries.

    The PR's current labels are fetched first so a DELETE is only issued for
    a label that is really there. Most pull requests neither touch an
    EXHAUSTIVE_LABELS path nor carry the label (121 of the 220 in issue
    #785's dry run), and firing a DELETE for each would 404 and print a
    spurious "already removed?" line on every run.

    Removal itself is enforce_mutually_exclusive_labels.remove_labels(): the
    identical operation with the identical contract (delete each, treat a 404
    as already-gone), already URL-encoding the label name and already
    carrying that module's transient-error retry.

    Returns True on success, including when the PR carries none of *labels*.
    """
    try:
        current = fetch_current_labels(repo, pr_number, token)
    except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as exc:
        print(f"Error fetching current labels for #{pr_number}: {exc}", file=sys.stderr)
        return False

    unjustified = {label.lower() for label in labels}
    # Delete using the name string GitHub returned, not the one from the rule.
    carried = [name for name in current if name.lower() in unjustified]
    if not carried:
        return True

    print(
        f"Removing labels {carried} from #{pr_number}: no changed file justifies them "
        "(see EXHAUSTIVE_LABELS in scripts/ci/labels/label_by_files.py)."
    )
    return emxl.remove_labels(pr_number, carried, repo, token)


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

    # An empty changed-file list is missing evidence, not evidence of
    # absence: both helpers return [] for it, so neither an add nor a removal
    # is derived from one.
    to_add = matching_labels(paths)
    to_remove = labels_to_remove(paths)

    if not to_add and not to_remove:
        print(f"No label rules matched the changed files on #{pr_number}--nothing to do.")
        return 0

    exit_code = 0
    if to_add and not add_labels(repo, pr_number, to_add, token):
        exit_code = 1
    if to_remove and not remove_unjustified_labels(repo, pr_number, to_remove, token):
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
