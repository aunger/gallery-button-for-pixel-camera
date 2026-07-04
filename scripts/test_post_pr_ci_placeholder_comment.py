#!/usr/bin/env python3
"""Unit tests for post_pr_ci_placeholder_comment.py."""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import post_pr_ci_placeholder_comment as placeholder  # noqa: E402
import post_pr_ci_summary_link as link  # noqa: E402


class TestPlaceholderBody(unittest.TestCase):
    def test_contains_marker(self):
        self.assertIn(link.MARKER, placeholder.PLACEHOLDER_BODY)

    def test_is_parseable_by_find_existing_comment_marker_check(self):
        # The body must contain the same MARKER post_pr_ci_summary_link.py
        # scans for, so the later real-results run edits this comment in
        # place instead of creating a second one.
        self.assertTrue(placeholder.PLACEHOLDER_BODY.startswith(link.MARKER))


class TestMain(unittest.TestCase):
    def _env(self, **overrides):
        env = {
            "GITHUB_TOKEN": "token",
            "GITHUB_REPOSITORY": "owner/repo",
            "WORKFLOW_RUN_PR_URL": "https://github.com/owner/repo/pull/42",
        }
        env.update(overrides)
        return env

    @patch("post_pr_ci_placeholder_comment.upsert_comment")
    @patch("post_pr_ci_placeholder_comment.find_existing_comment")
    def test_posts_placeholder_when_no_comment_exists(self, mock_find, mock_upsert):
        mock_find.return_value = link.CommentLookup(fetch_ok=True, comment_id=None, body=None)
        mock_upsert.return_value = True
        with patch.dict(os.environ, self._env(), clear=True):
            self.assertEqual(placeholder.main([]), 0)
        mock_upsert.assert_called_once_with(
            "token", "owner/repo", "42", placeholder.PLACEHOLDER_BODY
        )

    @patch("post_pr_ci_placeholder_comment.upsert_comment")
    @patch("post_pr_ci_placeholder_comment.find_existing_comment")
    def test_does_not_overwrite_existing_sticky_comment(self, mock_find, mock_upsert):
        mock_find.return_value = link.CommentLookup(
            fetch_ok=True, comment_id=7, body=f"{link.MARKER}\nreal results"
        )
        with patch.dict(os.environ, self._env(), clear=True):
            self.assertEqual(placeholder.main([]), 0)
        mock_upsert.assert_not_called()

    @patch("post_pr_ci_placeholder_comment.upsert_comment")
    @patch("post_pr_ci_placeholder_comment.find_existing_comment")
    def test_does_not_post_when_fetch_fails(self, mock_find, mock_upsert):
        mock_find.return_value = link.CommentLookup(fetch_ok=False, comment_id=None, body=None)
        with patch.dict(os.environ, self._env(), clear=True):
            self.assertEqual(placeholder.main([]), 0)
        mock_upsert.assert_not_called()

    @patch("post_pr_ci_placeholder_comment.upsert_comment")
    def test_skips_when_token_missing(self, mock_upsert):
        with patch.dict(os.environ, self._env(GITHUB_TOKEN=""), clear=True):
            self.assertEqual(placeholder.main([]), 0)
        mock_upsert.assert_not_called()

    @patch("post_pr_ci_placeholder_comment.upsert_comment")
    def test_skips_when_repository_missing(self, mock_upsert):
        with patch.dict(os.environ, self._env(GITHUB_REPOSITORY=""), clear=True):
            self.assertEqual(placeholder.main([]), 0)
        mock_upsert.assert_not_called()

    @patch("post_pr_ci_placeholder_comment.upsert_comment")
    def test_skips_when_pr_url_missing(self, mock_upsert):
        with patch.dict(os.environ, self._env(WORKFLOW_RUN_PR_URL=""), clear=True):
            self.assertEqual(placeholder.main([]), 0)
        mock_upsert.assert_not_called()

    @patch("post_pr_ci_placeholder_comment.upsert_comment")
    def test_skips_when_pr_url_unparseable(self, mock_upsert):
        with patch.dict(os.environ, self._env(WORKFLOW_RUN_PR_URL="not-a-url"), clear=True):
            self.assertEqual(placeholder.main([]), 0)
        mock_upsert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
