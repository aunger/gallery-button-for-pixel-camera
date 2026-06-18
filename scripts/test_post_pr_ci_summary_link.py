#!/usr/bin/env python3
"""Unit tests for post_pr_ci_summary_link.py."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import post_pr_ci_summary_link as link  # noqa: E402


# ---------------------------------------------------------------------------
# pr_number_from_url
# ---------------------------------------------------------------------------


class TestPrNumberFromUrl(unittest.TestCase):
    def test_extracts_number_from_pull_url(self):
        url = "https://github.com/owner/repo/pull/42"
        self.assertEqual(link.pr_number_from_url(url), "42")

    def test_extracts_number_with_trailing_slash(self):
        url = "https://github.com/owner/repo/pull/7/"
        self.assertEqual(link.pr_number_from_url(url), "7")

    def test_returns_none_for_non_pull_url(self):
        url = "https://github.com/owner/repo/issues/9"
        self.assertIsNone(link.pr_number_from_url(url))

    def test_returns_none_for_empty_url(self):
        self.assertIsNone(link.pr_number_from_url(""))


# ---------------------------------------------------------------------------
# build_comment_body
# ---------------------------------------------------------------------------


class TestBuildCommentBody(unittest.TestCase):
    def test_includes_marker_and_link_when_summary_written(self):
        body = link.build_comment_body("https://example.com/run#summary-1", True)
        self.assertIn(link.MARKER, body)
        self.assertIn("https://example.com/run#summary-1", body)
        self.assertIn("View the build-and-test summary for this PR", body)

    def test_notes_missing_summary_when_not_written(self):
        body = link.build_comment_body("https://example.com/run", False)
        self.assertIn(link.MARKER, body)
        self.assertIn("https://example.com/run", body)
        # Should not claim there is a pass/fail summary to view.
        self.assertNotIn("build-and-test summary", body)
        self.assertIn("did not need a full build", body)


# ---------------------------------------------------------------------------
# find_job_id
# ---------------------------------------------------------------------------


class TestFindJobId(unittest.TestCase):
    @patch("post_pr_ci_summary_link.requests")
    def test_returns_id_for_matching_job_name(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jobs": [
                {"id": 111, "name": "build-and-test", "run_attempt": 1},
                {"id": 222, "name": "file-issues", "run_attempt": 1},
            ]
        }
        mock_requests.get.return_value = mock_resp
        result = link.find_job_id("token", "owner/repo", "999", "1", "build-and-test")
        self.assertEqual(result, "111")

    @patch("post_pr_ci_summary_link.requests")
    def test_filters_by_run_attempt_when_jobs_repeat(self, mock_requests):
        """A re-run leaves a stale job from a prior attempt; pick the live one."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jobs": [
                {"id": 111, "name": "build-and-test", "run_attempt": 1},
                {"id": 333, "name": "build-and-test", "run_attempt": 2},
            ]
        }
        mock_requests.get.return_value = mock_resp
        result = link.find_job_id("token", "owner/repo", "999", "2", "build-and-test")
        self.assertEqual(result, "333")

    @patch("post_pr_ci_summary_link.requests")
    def test_falls_back_to_unfiltered_match_when_attempt_unknown(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jobs": [{"id": 111, "name": "build-and-test", "run_attempt": 1}]
        }
        mock_requests.get.return_value = mock_resp
        result = link.find_job_id("token", "owner/repo", "999", "", "build-and-test")
        self.assertEqual(result, "111")

    @patch("post_pr_ci_summary_link.requests")
    def test_returns_none_when_no_matching_job(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jobs": [{"id": 222, "name": "file-issues", "run_attempt": 1}]
        }
        mock_requests.get.return_value = mock_resp
        result = link.find_job_id("token", "owner/repo", "999", "1", "build-and-test")
        self.assertIsNone(result)

    @patch("post_pr_ci_summary_link.requests")
    def test_returns_none_on_api_error(self, mock_requests):
        mock_requests.get.side_effect = Exception("network error")
        result = link.find_job_id("token", "owner/repo", "999", "1", "build-and-test")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# find_existing_comment / upsert_comment
# ---------------------------------------------------------------------------


class TestFindExistingComment(unittest.TestCase):
    @patch("post_pr_ci_summary_link.requests")
    def test_returns_id_of_comment_with_marker(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"id": 1, "body": "unrelated comment"},
            {"id": 2, "body": f"{link.MARKER}\nold link"},
        ]
        mock_requests.get.return_value = mock_resp
        result = link.find_existing_comment("token", "owner/repo", "42")
        self.assertEqual(result, 2)

    @patch("post_pr_ci_summary_link.requests")
    def test_returns_none_when_no_marker_present(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"id": 1, "body": "unrelated comment"}]
        mock_requests.get.return_value = mock_resp
        result = link.find_existing_comment("token", "owner/repo", "42")
        self.assertIsNone(result)

    @patch("post_pr_ci_summary_link.requests")
    def test_returns_none_on_api_error(self, mock_requests):
        mock_requests.get.side_effect = Exception("network error")
        result = link.find_existing_comment("token", "owner/repo", "42")
        self.assertIsNone(result)


class TestUpsertComment(unittest.TestCase):
    @patch("post_pr_ci_summary_link.find_existing_comment")
    @patch("post_pr_ci_summary_link.requests")
    def test_creates_comment_when_none_exists(self, mock_requests, mock_find):
        mock_find.return_value = None
        mock_resp = MagicMock()
        mock_requests.post.return_value = mock_resp
        result = link.upsert_comment("token", "owner/repo", "42", "body")
        self.assertTrue(result)
        mock_requests.post.assert_called_once()
        mock_requests.patch.assert_not_called()
        url = mock_requests.post.call_args[0][0]
        self.assertIn("/issues/42/comments", url)

    @patch("post_pr_ci_summary_link.find_existing_comment")
    @patch("post_pr_ci_summary_link.requests")
    def test_edits_existing_comment_in_place(self, mock_requests, mock_find):
        mock_find.return_value = 99
        mock_resp = MagicMock()
        mock_requests.patch.return_value = mock_resp
        result = link.upsert_comment("token", "owner/repo", "42", "body")
        self.assertTrue(result)
        mock_requests.patch.assert_called_once()
        mock_requests.post.assert_not_called()
        url = mock_requests.patch.call_args[0][0]
        self.assertIn("/issues/comments/99", url)

    @patch("post_pr_ci_summary_link.find_existing_comment")
    @patch("post_pr_ci_summary_link.requests")
    def test_returns_false_on_api_error(self, mock_requests, mock_find):
        mock_find.return_value = None
        mock_requests.post.side_effect = Exception("server error")
        result = link.upsert_comment("token", "owner/repo", "42", "body")
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain(unittest.TestCase):
    def _env(self, **overrides):
        env = {
            "GITHUB_TOKEN": "token",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_RUN_ID": "999",
            "GITHUB_RUN_ATTEMPT": "1",
            "WORKFLOW_RUN_PR_URL": "https://github.com/owner/repo/pull/42",
            "SUMMARY_WRITTEN": "true",
        }
        env.update(overrides)
        return env

    @patch("post_pr_ci_summary_link.upsert_comment")
    @patch("post_pr_ci_summary_link.find_job_id")
    def test_posts_link_with_summary_anchor_when_job_id_found(self, mock_find_job, mock_upsert):
        mock_find_job.return_value = "111"
        mock_upsert.return_value = True
        with patch.dict(os.environ, self._env(), clear=True):
            self.assertEqual(link.main([]), 0)
        body = mock_upsert.call_args[0][3]
        self.assertIn("https://github.com/owner/repo/actions/runs/999#summary-111", body)

    @patch("post_pr_ci_summary_link.upsert_comment")
    @patch("post_pr_ci_summary_link.find_job_id")
    def test_falls_back_to_run_url_when_job_id_missing(self, mock_find_job, mock_upsert):
        mock_find_job.return_value = None
        mock_upsert.return_value = True
        with patch.dict(os.environ, self._env(), clear=True):
            self.assertEqual(link.main([]), 0)
        body = mock_upsert.call_args[0][3]
        self.assertIn("https://github.com/owner/repo/actions/runs/999", body)
        self.assertNotIn("#summary-", body)

    @patch("post_pr_ci_summary_link.upsert_comment")
    @patch("post_pr_ci_summary_link.find_job_id")
    def test_links_to_bare_run_and_explains_when_summary_not_written(
        self, mock_find_job, mock_upsert
    ):
        """Docs-only PRs skip the summary step; do not claim one exists."""
        mock_upsert.return_value = True
        with patch.dict(os.environ, self._env(SUMMARY_WRITTEN="false"), clear=True):
            self.assertEqual(link.main([]), 0)
        body = mock_upsert.call_args[0][3]
        self.assertIn("https://github.com/owner/repo/actions/runs/999", body)
        self.assertNotIn("#summary-", body)
        self.assertNotIn("build-and-test summary", body)
        self.assertIn("did not need a full build", body)
        # The job-ID lookup is unnecessary work when there's no summary to
        # link to anyway.
        mock_find_job.assert_not_called()

    @patch("post_pr_ci_summary_link.upsert_comment")
    @patch("post_pr_ci_summary_link.find_job_id")
    def test_treats_missing_summary_written_as_false(self, mock_find_job, mock_upsert):
        mock_upsert.return_value = True
        env = self._env()
        del env["SUMMARY_WRITTEN"]
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(link.main([]), 0)
        body = mock_upsert.call_args[0][3]
        self.assertNotIn("#summary-", body)
        mock_find_job.assert_not_called()

    @patch("post_pr_ci_summary_link.upsert_comment")
    def test_skips_when_not_a_pull_request_run(self, mock_upsert):
        with patch.dict(os.environ, self._env(WORKFLOW_RUN_PR_URL=""), clear=True):
            self.assertEqual(link.main([]), 0)
        mock_upsert.assert_not_called()

    @patch("post_pr_ci_summary_link.upsert_comment")
    def test_skips_when_token_missing(self, mock_upsert):
        with patch.dict(os.environ, self._env(GITHUB_TOKEN=""), clear=True):
            self.assertEqual(link.main([]), 0)
        mock_upsert.assert_not_called()

    @patch("post_pr_ci_summary_link.upsert_comment")
    def test_skips_when_run_id_missing(self, mock_upsert):
        with patch.dict(os.environ, self._env(GITHUB_RUN_ID=""), clear=True):
            self.assertEqual(link.main([]), 0)
        mock_upsert.assert_not_called()

    @patch("post_pr_ci_summary_link.upsert_comment")
    def test_skips_when_pr_url_unparseable(self, mock_upsert):
        with patch.dict(os.environ, self._env(WORKFLOW_RUN_PR_URL="not-a-url"), clear=True):
            self.assertEqual(link.main([]), 0)
        mock_upsert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
