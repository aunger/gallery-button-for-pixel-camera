#!/usr/bin/env python3
"""Unit tests for snooze_held_issues.py."""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import snooze_held_issues as shi  # noqa: E402


# ---------------------------------------------------------------------------
# snooze_issue tests
# ---------------------------------------------------------------------------


class TestSnoozeIssue(unittest.TestCase):
    def test_closes_open_issue(self):
        with patch.object(
            shi.emxl,
            "gh_api",
            side_effect=[{"number": 5, "state": "open"}, None],
        ) as mock_api:
            result = shi.snooze_issue(5, "snooze 30 days", "owner/repo", "tok")

        self.assertTrue(result)
        self.assertEqual(mock_api.call_count, 2)
        patch_call = mock_api.call_args_list[1]
        self.assertEqual(patch_call[1]["method"], "PATCH")
        self.assertEqual(patch_call[1]["body"], {"state": "closed"})
        self.assertIn("5", patch_call[0][0])

    def test_already_closed_is_a_noop(self):
        with patch.object(
            shi.emxl,
            "gh_api",
            side_effect=[{"number": 5, "state": "closed"}],
        ) as mock_api:
            result = shi.snooze_issue(5, "snooze 30 days", "owner/repo", "tok")

        self.assertTrue(result)
        # Only the GET was made; no PATCH followed for an already-closed issue.
        self.assertEqual(mock_api.call_count, 1)

    def test_raises_on_non_dict_response(self):
        with patch.object(shi.emxl, "gh_api", return_value=None):
            with self.assertRaises(RuntimeError):
                shi.snooze_issue(5, "snooze 30 days", "owner/repo", "tok")

    def test_raises_when_fetch_fails(self):
        with patch.object(shi.emxl, "gh_api", side_effect=Exception("network error")):
            with self.assertRaises(Exception):
                shi.snooze_issue(5, "snooze 30 days", "owner/repo", "tok")

    def test_raises_when_patch_fails(self):
        with patch.object(
            shi.emxl,
            "gh_api",
            side_effect=[{"number": 5, "state": "open"}, Exception("boom")],
        ):
            with self.assertRaises(Exception):
                shi.snooze_issue(5, "snooze 30 days", "owner/repo", "tok")


# ---------------------------------------------------------------------------
# main() tests
# ---------------------------------------------------------------------------


class TestMain(unittest.TestCase):
    def setUp(self):
        self._env_patch = patch.dict(
            os.environ,
            {
                "GITHUB_TOKEN": "test-token",
                "GITHUB_REPOSITORY": "owner/repo",
                "ISSUE_NUMBER": "42",
                "ADDED_LABEL": "snooze 30 days",
            },
        )
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()

    def test_exit_0_when_token_missing(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
            result = shi.main()
        self.assertEqual(result, 0)

    def test_exit_0_when_repository_missing(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": ""}):
            result = shi.main()
        self.assertEqual(result, 0)

    def test_exit_0_when_issue_number_missing(self):
        with patch.dict(os.environ, {"ISSUE_NUMBER": ""}):
            result = shi.main()
        self.assertEqual(result, 0)

    def test_exit_0_when_added_label_missing(self):
        with patch.dict(os.environ, {"ADDED_LABEL": ""}):
            result = shi.main()
        self.assertEqual(result, 0)

    def test_exit_0_when_label_not_a_snooze_label(self):
        with patch.dict(os.environ, {"ADDED_LABEL": "bug"}):
            with patch.object(shi.emxl, "gh_api") as mock_api:
                result = shi.main()
        self.assertEqual(result, 0)
        mock_api.assert_not_called()

    def test_exit_1_when_issue_number_non_integer(self):
        with patch.dict(os.environ, {"ISSUE_NUMBER": "abc"}):
            result = shi.main()
        self.assertEqual(result, 1)

    def test_snoozes_for_each_snooze_label(self):
        """Every rung snoozes, in the current spelling and the legacy one."""
        for label in (*sorted(shi.emxl.SNOOZE_LABELS), "SNOOZE 30 DAYS", "HOLD 30 DAYS"):
            with self.subTest(label=label):
                with patch.dict(os.environ, {"ADDED_LABEL": label}):
                    with patch.object(shi, "snooze_issue", return_value=True) as mock_snooze:
                        result = shi.main()
                self.assertEqual(result, 0)
                mock_snooze.assert_called_once_with(42, label, "owner/repo", "test-token")

    def test_exit_1_when_snooze_raises(self):
        with patch.object(shi, "snooze_issue", side_effect=Exception("boom")):
            result = shi.main()
        self.assertEqual(result, 1)

    def test_exit_0_on_success(self):
        with patch.object(shi, "snooze_issue", return_value=True) as mock_snooze:
            result = shi.main()
        self.assertEqual(result, 0)
        mock_snooze.assert_called_once()


if __name__ == "__main__":
    unittest.main()
