#!/usr/bin/env python3
"""Integration test: verify that automation-required GitHub labels exist in the repo.

This test calls the real GitHub API and is skipped when GITHUB_TOKEN or
GITHUB_REPOSITORY is not set.

Labels checked here are exactly those that this repo's GitHub Actions and
scripts apply or enforce by name.
These are the labels whose absence would cause silent misbehavior in the
automation (as happened with the stale spellings in issue #477).

Sources:
  scripts/enforce_mutually_exclusive_labels.py: MUTUALLY_EXCLUSIVE_SETS
  scripts/file_test_failure_issues.py:          LABELS constant
  scripts/archive_stale_test_failures.py:       LABEL_TEST_FAILURE_ARCHIVE constant
  .github/release.yml:                          changelog.exclude.labels
"""

import json
import os
import sys
import unittest
import urllib.error
import urllib.request

import yaml

# Scripts live in the same directory as this file; make them importable.
sys.path.insert(0, os.path.dirname(__file__))

from archive_stale_test_failures import LABEL_TEST_FAILURE_ARCHIVE  # noqa: E402
from enforce_mutually_exclusive_labels import MUTUALLY_EXCLUSIVE_SETS  # noqa: E402
from file_test_failure_issues import LABELS as _TEST_FAILURE_LABELS  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RELEASE_CONFIG_PATH = os.path.join(_REPO_ROOT, ".github", "release.yml")


def _release_notes_excluded_labels() -> frozenset[str]:
    """Return the labels excluded from release notes by .github/release.yml.

    Reads the YAML file itself rather than duplicating its label list as a
    separate Python constant, so there is exactly one place to update the
    exclusion list and no way for a second copy to drift out of sync with
    what GitHub's release-notes generator actually consumes.
    """
    with open(_RELEASE_CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    labels = config.get("changelog", {}).get("exclude", {}).get("labels", [])
    return frozenset(labels)


# ---------------------------------------------------------------------------
# Derive the set of required labels from their authoritative sources.
# When a new label is added to automation, updating the source constant
# (or MUTUALLY_EXCLUSIVE_SETS, or .github/release.yml) is sufficient; this
# test picks it up automatically.
# ---------------------------------------------------------------------------

REQUIRED_LABELS: frozenset[str] = (
    # All labels in every mutually-exclusive set defined in
    # enforce_mutually_exclusive_labels.py.
    frozenset().union(*MUTUALLY_EXCLUSIVE_SETS)
    # Label(s) applied to new test-failure issues.
    | frozenset(_TEST_FAILURE_LABELS)
    # Label swapped in when a test-failure issue goes stale.
    | frozenset({LABEL_TEST_FAILURE_ARCHIVE})
    # Labels excluded from GitHub's auto-generated release notes.
    | _release_notes_excluded_labels()
    # Note: the labels referenced in block-merge-on-blocking-labels.yml
    # (verification needed, changes requested, changes done, orchestrating)
    # and remove-verified-on-push.yml (verified) are all already members of
    # MUTUALLY_EXCLUSIVE_SETS, so no additional entries are needed here.
)


def _fetch_repo_label_names(token: str, repo: str) -> set[str]:
    """Return the set of all lowercased label names that exist in *repo*.

    Label names are lowercased so callers can compare case-insensitively,
    matching how the enforcement automation works.

    Fetches up to 10 pages of 100 labels each (1 000 labels total), which
    is far more than any real repo needs.
    Raises urllib.error.HTTPError / urllib.error.URLError on network failure.
    """
    names: set[str] = set()
    for page in range(1, 11):
        url = f"https://api.github.com/repos/{repo}/labels?per_page=100&page={page}"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        # URL is built from the GitHub API constant; the file:// risk does not apply.
        with urllib.request.urlopen(req) as resp:  # nosemgrep
            batch = json.loads(resp.read())
        if not batch:
            break
        for label in batch:
            names.add(label["name"].lower())
        if len(batch) < 100:
            # Last page.
            break
    return names


class TestRequiredLabelsExist(unittest.TestCase):
    """Integration tests that hit the real GitHub API."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.token = os.environ.get("GITHUB_TOKEN", "")
        cls.repo = os.environ.get("GITHUB_REPOSITORY", "")

        if not cls.token or not cls.repo:
            raise unittest.SkipTest(
                "GITHUB_TOKEN and GITHUB_REPOSITORY must both be set to run label"
                " existence integration tests."
            )

        cls.repo_labels = _fetch_repo_label_names(cls.token, cls.repo)

    def test_all_required_labels_exist(self) -> None:
        """Every label that automation depends on must exist in the repository."""
        missing = sorted(REQUIRED_LABELS - self.repo_labels)
        self.assertEqual(
            missing,
            [],
            msg=(
                "The following labels are required by automation but do not exist"
                f" in {self.repo}: {missing!r}.\n"
                "Create them in the repository's Labels settings page before the"
                " automation runs."
            ),
        )

    def test_individual_labels(self) -> None:
        """One sub-test per required label so failures name the missing label clearly."""
        for label in sorted(REQUIRED_LABELS):
            with self.subTest(label=label):
                self.assertIn(
                    label,
                    self.repo_labels,
                    msg=(f"Required label {label!r} does not exist in {self.repo}."),
                )


if __name__ == "__main__":
    unittest.main()
