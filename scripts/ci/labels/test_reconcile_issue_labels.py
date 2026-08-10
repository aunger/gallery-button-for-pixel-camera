#!/usr/bin/env python3
"""Unit tests for reconcile_issue_labels.py."""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import reconcile_issue_labels as ril  # noqa: E402


# ---------------------------------------------------------------------------
# fetch_open_pr_numbers tests
# ---------------------------------------------------------------------------


class TestFetchOpenPrNumbers(unittest.TestCase):
    """What this function adds to the shared listing helper is the number projection.

    The pagination it inherits is asserted against
    emxl.fetch_open_pull_requests in test_enforce_mutually_exclusive_labels.py,
    which is where that contract lives.
    """

    def test_returns_the_numbers_of_the_listed_prs(self):
        with patch.object(ril.pil.emxl, "gh_api", side_effect=[[{"number": 5}, {"number": 7}], []]):
            result = ril.fetch_open_pr_numbers("owner/repo", "tok")
        self.assertEqual(result, [5, 7])

    def test_returns_empty_when_no_open_prs(self):
        with patch.object(ril.pil.emxl, "gh_api", return_value=[]):
            result = ril.fetch_open_pr_numbers("owner/repo", "tok")
        self.assertEqual(result, [])

    def test_requests_open_state(self):
        captured_paths = []

        def fake_api(path, token):
            captured_paths.append(path)
            return []

        with patch.object(ril.pil.emxl, "gh_api", side_effect=fake_api):
            ril.fetch_open_pr_numbers("owner/repo", "tok")

        self.assertIn("state=open", captured_paths[0])
        self.assertTrue(captured_paths[0].startswith("repos/owner/repo/pulls?"))


# ---------------------------------------------------------------------------
# main() tests
# ---------------------------------------------------------------------------


class TestMain(unittest.TestCase):
    def _env(self, **overrides):
        env = {"GITHUB_TOKEN": "tok", "GITHUB_REPOSITORY": "owner/repo"}
        env.update(overrides)
        return env

    def test_missing_token_returns_1(self):
        with patch.dict(os.environ, self._env(GITHUB_TOKEN=""), clear=True):
            self.assertEqual(ril.main(), 1)

    def test_missing_repo_returns_1(self):
        with patch.dict(os.environ, self._env(GITHUB_REPOSITORY=""), clear=True):
            self.assertEqual(ril.main(), 1)

    def test_malformed_repo_returns_1(self):
        with patch.dict(os.environ, self._env(GITHUB_REPOSITORY="not-a-slug"), clear=True):
            self.assertEqual(ril.main(), 1)

    def test_fetch_open_prs_failure_returns_1(self):
        with patch.dict(os.environ, self._env(), clear=True):
            with patch.object(ril, "fetch_open_pr_numbers", side_effect=RuntimeError("boom")):
                self.assertEqual(ril.main(), 1)

    def test_no_open_prs_returns_0_without_propagating(self):
        with patch.dict(os.environ, self._env(), clear=True):
            with patch.object(ril, "fetch_open_pr_numbers", return_value=[]):
                with patch.object(ril.pil, "propagate_to_pr") as mock_propagate:
                    result = ril.main()
        self.assertEqual(result, 0)
        mock_propagate.assert_not_called()

    def test_reconciles_every_open_pr(self):
        with patch.dict(os.environ, self._env(), clear=True):
            with patch.object(ril, "fetch_open_pr_numbers", return_value=[5, 7, 9]):
                with patch.object(ril.pil, "propagate_to_pr", return_value=True) as mock_propagate:
                    result = ril.main()

        self.assertEqual(result, 0)
        calls = [c.args for c in mock_propagate.call_args_list]
        self.assertEqual(
            calls,
            [
                ("owner", "repo", "owner/repo", 5, "tok"),
                ("owner", "repo", "owner/repo", 7, "tok"),
                ("owner", "repo", "owner/repo", 9, "tok"),
            ],
        )

    def test_one_failure_among_several_still_reports_failure(self):
        def fake_propagate(owner, name, repo, pr_number, token):
            return pr_number != 7

        with patch.dict(os.environ, self._env(), clear=True):
            with patch.object(ril, "fetch_open_pr_numbers", return_value=[5, 7, 9]):
                with patch.object(ril.pil, "propagate_to_pr", side_effect=fake_propagate):
                    result = ril.main()

        self.assertEqual(result, 1)

    def test_one_failure_does_not_stop_remaining_prs(self):
        def fake_propagate(owner, name, repo, pr_number, token):
            calls.append(pr_number)
            return pr_number != 7

        calls: list[int] = []
        with patch.dict(os.environ, self._env(), clear=True):
            with patch.object(ril, "fetch_open_pr_numbers", return_value=[5, 7, 9]):
                with patch.object(ril.pil, "propagate_to_pr", side_effect=fake_propagate):
                    ril.main()

        self.assertEqual(calls, [5, 7, 9])


if __name__ == "__main__":
    unittest.main()
