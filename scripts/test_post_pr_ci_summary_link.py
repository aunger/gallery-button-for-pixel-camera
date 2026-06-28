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
# result_label
# ---------------------------------------------------------------------------


class TestResultLabel(unittest.TestCase):
    def test_success_maps_to_pass(self):
        self.assertEqual(link.result_label("success"), "pass")

    def test_failure_maps_to_fail(self):
        self.assertEqual(link.result_label("failure"), "fail")

    def test_timed_out_maps_to_fail(self):
        self.assertEqual(link.result_label("timed_out"), "fail")

    def test_cancelled_maps_to_fail(self):
        self.assertEqual(link.result_label("cancelled"), "fail")

    def test_skipped_maps_to_skip(self):
        self.assertEqual(link.result_label("skipped"), "skip")

    def test_none_maps_to_unknown(self):
        self.assertEqual(link.result_label(None), "unknown")

    def test_unexpected_string_maps_to_unknown(self):
        self.assertEqual(link.result_label("action_required"), "unknown")

    def test_neutral_maps_to_unknown(self):
        self.assertEqual(link.result_label("neutral"), "unknown")


# ---------------------------------------------------------------------------
# parse_existing_items
# ---------------------------------------------------------------------------


class TestParseExistingItems(unittest.TestCase):
    def test_empty_body_returns_empty_list(self):
        self.assertEqual(link.parse_existing_items(""), [])

    def test_parses_single_item(self):
        body = (
            f"{link.MARKER}\n### CI test summary\n\n"
            "- [build-and-test 1234 pass](https://example.com/run#summary-111)\n"
        )
        items = link.parse_existing_items(body)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].job_name, "build-and-test")
        self.assertEqual(items[0].run_number, "1234")
        self.assertEqual(items[0].result, "pass")
        self.assertEqual(items[0].url, "https://example.com/run#summary-111")

    def test_parses_multiple_items(self):
        body = (
            f"{link.MARKER}\n### CI test summary\n\n"
            "- [build-and-test 1 pass](https://example.com/1)\n"
            "- [build-and-test 2 fail](https://example.com/2)\n"
            "- [build-and-test 3 skip](https://example.com/3)\n"
        )
        items = link.parse_existing_items(body)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0].run_number, "1")
        self.assertEqual(items[1].result, "fail")
        self.assertEqual(items[2].result, "skip")

    def test_ignores_header_and_non_list_lines(self):
        body = (
            f"{link.MARKER}\n### CI test summary\n\n"
            "Some random prose line.\n"
            "- [build-and-test 42 pass](https://example.com)\n"
            "Another prose line.\n"
        )
        items = link.parse_existing_items(body)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].run_number, "42")

    def test_tolerates_trailing_whitespace(self):
        body = "- [build-and-test 7 fail](https://example.com)  \n"
        items = link.parse_existing_items(body)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].run_number, "7")

    def test_parses_bare_run_url_without_anchor(self):
        body = "- [build-and-test 5 skip](https://github.com/owner/repo/actions/runs/999)\n"
        items = link.parse_existing_items(body)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].result, "skip")


# ---------------------------------------------------------------------------
# build_comment_body
# ---------------------------------------------------------------------------


class TestBuildCommentBody(unittest.TestCase):
    def test_raises_on_empty_items(self):
        with self.assertRaises(ValueError):
            link.build_comment_body([])

    def test_includes_marker_and_heading(self):
        items = [link.CIItem("build-and-test", "1234", "pass", "https://example.com")]
        body = link.build_comment_body(items)
        self.assertIn(link.MARKER, body)
        self.assertIn("### CI test summary", body)

    def test_renders_list_item_with_job_run_result(self):
        items = [link.CIItem("build-and-test", "1234", "pass", "https://example.com/run")]
        body = link.build_comment_body(items)
        self.assertIn("- [build-and-test 1234 pass](https://example.com/run)", body)

    def test_renders_multiple_items_in_order(self):
        items = [
            link.CIItem("build-and-test", "1", "pass", "https://example.com/1"),
            link.CIItem("build-and-test", "2", "fail", "https://example.com/2"),
        ]
        body = link.build_comment_body(items)
        pos1 = body.index("1 pass")
        pos2 = body.index("2 fail")
        self.assertLess(pos1, pos2)

    def test_renders_fail_result(self):
        items = [link.CIItem("build-and-test", "99", "fail", "https://example.com")]
        body = link.build_comment_body(items)
        self.assertIn("99 fail", body)

    def test_renders_skip_result(self):
        items = [link.CIItem("build-and-test", "5", "skip", "https://example.com")]
        body = link.build_comment_body(items)
        self.assertIn("5 skip", body)


# ---------------------------------------------------------------------------
# find_job / find_job_id
# ---------------------------------------------------------------------------


class TestFindJob(unittest.TestCase):
    @patch("post_pr_ci_summary_link.requests")
    def test_returns_full_dict_for_matching_job(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jobs": [
                {"id": 111, "name": "build-and-test", "run_attempt": 1, "conclusion": "success"},
                {"id": 222, "name": "file-issues", "run_attempt": 1, "conclusion": "success"},
            ]
        }
        mock_requests.get.return_value = mock_resp
        result = link.find_job("token", "owner/repo", "999", "1", "build-and-test")
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], 111)
        self.assertEqual(result["conclusion"], "success")

    @patch("post_pr_ci_summary_link.requests")
    def test_filters_by_run_attempt_when_jobs_repeat(self, mock_requests):
        """A re-run leaves a stale job from a prior attempt; pick the live one."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jobs": [
                {"id": 111, "name": "build-and-test", "run_attempt": 1, "conclusion": "failure"},
                {"id": 333, "name": "build-and-test", "run_attempt": 2, "conclusion": "success"},
            ]
        }
        mock_requests.get.return_value = mock_resp
        result = link.find_job("token", "owner/repo", "999", "2", "build-and-test")
        self.assertEqual(result["id"], 333)

    @patch("post_pr_ci_summary_link.requests")
    def test_falls_back_to_unfiltered_match_when_attempt_unknown(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jobs": [
                {"id": 111, "name": "build-and-test", "run_attempt": 1, "conclusion": "success"}
            ]
        }
        mock_requests.get.return_value = mock_resp
        result = link.find_job("token", "owner/repo", "999", "", "build-and-test")
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], 111)

    @patch("post_pr_ci_summary_link.requests")
    def test_returns_none_when_no_matching_job(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jobs": [{"id": 222, "name": "file-issues", "run_attempt": 1}]
        }
        mock_requests.get.return_value = mock_resp
        result = link.find_job("token", "owner/repo", "999", "1", "build-and-test")
        self.assertIsNone(result)

    @patch("post_pr_ci_summary_link.requests")
    def test_returns_none_on_api_error(self, mock_requests):
        mock_requests.get.side_effect = Exception("network error")
        result = link.find_job("token", "owner/repo", "999", "1", "build-and-test")
        self.assertIsNone(result)


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
    def test_returns_id_and_body_of_comment_with_marker(self, mock_requests):
        mock_resp = MagicMock()
        old_body = f"{link.MARKER}\nold link"
        mock_resp.json.return_value = [
            {"id": 1, "body": "unrelated comment"},
            {"id": 2, "body": old_body},
        ]
        mock_requests.get.return_value = mock_resp
        result = link.find_existing_comment("token", "owner/repo", "42")
        self.assertTrue(result.fetch_ok)
        self.assertEqual(result.comment_id, 2)
        self.assertEqual(result.body, old_body)

    @patch("post_pr_ci_summary_link.requests")
    def test_returns_fetch_ok_with_none_when_no_marker_present(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"id": 1, "body": "unrelated comment"}]
        mock_requests.get.return_value = mock_resp
        result = link.find_existing_comment("token", "owner/repo", "42")
        self.assertTrue(result.fetch_ok)
        self.assertIsNone(result.comment_id)
        self.assertIsNone(result.body)

    @patch("post_pr_ci_summary_link.requests")
    def test_returns_fetch_ok_false_on_api_error(self, mock_requests):
        mock_requests.get.side_effect = Exception("network error")
        result = link.find_existing_comment("token", "owner/repo", "42")
        self.assertFalse(result.fetch_ok)
        self.assertIsNone(result.comment_id)
        self.assertIsNone(result.body)


class TestUpsertComment(unittest.TestCase):
    @patch("post_pr_ci_summary_link.requests")
    def test_creates_comment_when_no_existing_id(self, mock_requests):
        mock_resp = MagicMock()
        mock_requests.post.return_value = mock_resp
        result = link.upsert_comment("token", "owner/repo", "42", "body", existing_id=None)
        self.assertTrue(result)
        mock_requests.post.assert_called_once()
        mock_requests.patch.assert_not_called()
        url = mock_requests.post.call_args[0][0]
        self.assertIn("/issues/42/comments", url)

    @patch("post_pr_ci_summary_link.requests")
    def test_edits_existing_comment_in_place(self, mock_requests):
        mock_resp = MagicMock()
        mock_requests.patch.return_value = mock_resp
        result = link.upsert_comment("token", "owner/repo", "42", "body", existing_id=99)
        self.assertTrue(result)
        mock_requests.patch.assert_called_once()
        mock_requests.post.assert_not_called()
        url = mock_requests.patch.call_args[0][0]
        self.assertIn("/issues/comments/99", url)

    @patch("post_pr_ci_summary_link.requests")
    def test_returns_false_on_api_error(self, mock_requests):
        mock_requests.post.side_effect = Exception("server error")
        result = link.upsert_comment("token", "owner/repo", "42", "body", existing_id=None)
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# merge / append / dedup logic (exercised via main)
# ---------------------------------------------------------------------------


class TestMergeLogic(unittest.TestCase):
    """Integration-style tests for the merge/append/dedup behaviour in main."""

    def _env(self, **overrides):
        env = {
            "GITHUB_TOKEN": "token",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_RUN_ID": "999",
            "GITHUB_RUN_NUMBER": "42",
            "GITHUB_RUN_ATTEMPT": "1",
            "WORKFLOW_RUN_PR_URL": "https://github.com/owner/repo/pull/7",
            "SUMMARY_WRITTEN": "true",
        }
        env.update(overrides)
        return env

    def _job_resp(self, conclusion="success"):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jobs": [
                {
                    "id": 111,
                    "name": "build-and-test",
                    "run_attempt": 1,
                    "conclusion": conclusion,
                }
            ]
        }
        return mock_resp

    @patch("post_pr_ci_summary_link.find_existing_comment")
    @patch("post_pr_ci_summary_link.upsert_comment")
    @patch("post_pr_ci_summary_link.requests")
    def test_first_run_creates_single_item_list(
        self, mock_requests, mock_upsert, mock_find_existing
    ):
        mock_requests.get.return_value = self._job_resp("success")
        mock_find_existing.return_value = link.CommentLookup(
            fetch_ok=True, comment_id=None, body=None
        )
        mock_upsert.return_value = True
        with patch.dict(os.environ, self._env(), clear=True):
            self.assertEqual(link.main([]), 0)
        body = mock_upsert.call_args[0][3]
        items = link.parse_existing_items(body)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].run_number, "42")
        self.assertEqual(items[0].result, "pass")

    @patch("post_pr_ci_summary_link.find_existing_comment")
    @patch("post_pr_ci_summary_link.upsert_comment")
    @patch("post_pr_ci_summary_link.requests")
    def test_second_push_appends_new_item(self, mock_requests, mock_upsert, mock_find_existing):
        """A new push (different run number) appends a second list item."""
        existing_body = (
            f"{link.MARKER}\n### CI test summary\n\n"
            "- [build-and-test 41 pass]"
            "(https://github.com/owner/repo/actions/runs/998#summary-111)\n"
        )
        mock_requests.get.return_value = self._job_resp("failure")
        mock_find_existing.return_value = link.CommentLookup(
            fetch_ok=True, comment_id=55, body=existing_body
        )
        mock_upsert.return_value = True
        with patch.dict(
            os.environ, self._env(GITHUB_RUN_NUMBER="42", GITHUB_RUN_ID="999"), clear=True
        ):
            self.assertEqual(link.main([]), 0)
        body = mock_upsert.call_args[0][3]
        items = link.parse_existing_items(body)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].run_number, "41")
        self.assertEqual(items[0].result, "pass")
        self.assertEqual(items[1].run_number, "42")
        self.assertEqual(items[1].result, "fail")

    @patch("post_pr_ci_summary_link.find_existing_comment")
    @patch("post_pr_ci_summary_link.upsert_comment")
    @patch("post_pr_ci_summary_link.requests")
    def test_same_run_number_replaces_in_place(
        self, mock_requests, mock_upsert, mock_find_existing
    ):
        """Re-running the same run number replaces the item, not duplicates it."""
        existing_body = (
            f"{link.MARKER}\n### CI test summary\n\n"
            "- [build-and-test 42 fail]"
            "(https://github.com/owner/repo/actions/runs/999#summary-111)\n"
        )
        mock_requests.get.return_value = self._job_resp("success")
        mock_find_existing.return_value = link.CommentLookup(
            fetch_ok=True, comment_id=55, body=existing_body
        )
        mock_upsert.return_value = True
        with patch.dict(os.environ, self._env(GITHUB_RUN_NUMBER="42"), clear=True):
            self.assertEqual(link.main([]), 0)
        body = mock_upsert.call_args[0][3]
        items = link.parse_existing_items(body)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].run_number, "42")
        self.assertEqual(items[0].result, "pass")

    @patch("post_pr_ci_summary_link.find_existing_comment")
    @patch("post_pr_ci_summary_link.upsert_comment")
    @patch("post_pr_ci_summary_link.requests")
    def test_list_capped_to_max_items(self, mock_requests, mock_upsert, mock_find_existing):
        """When existing items exceed MAX_ITEMS, oldest are dropped."""
        existing_lines = "\n".join(
            f"- [build-and-test {i} pass](https://example.com/{i})"
            for i in range(1, link.MAX_ITEMS + 1)
        )
        existing_body = f"{link.MARKER}\n### CI test summary\n\n{existing_lines}\n"
        mock_requests.get.return_value = self._job_resp("success")
        mock_find_existing.return_value = link.CommentLookup(
            fetch_ok=True, comment_id=55, body=existing_body
        )
        mock_upsert.return_value = True
        new_run = str(link.MAX_ITEMS + 1)
        with patch.dict(os.environ, self._env(GITHUB_RUN_NUMBER=new_run), clear=True):
            self.assertEqual(link.main([]), 0)
        body = mock_upsert.call_args[0][3]
        items = link.parse_existing_items(body)
        self.assertEqual(len(items), link.MAX_ITEMS)
        # Oldest (run 1) should be gone; newest should be present.
        run_numbers = [item.run_number for item in items]
        self.assertNotIn("1", run_numbers)
        self.assertIn(new_run, run_numbers)


# ---------------------------------------------------------------------------
# main: environment variable handling
# ---------------------------------------------------------------------------


class TestMain(unittest.TestCase):
    def _env(self, **overrides):
        env = {
            "GITHUB_TOKEN": "token",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_RUN_ID": "999",
            "GITHUB_RUN_NUMBER": "42",
            "GITHUB_RUN_ATTEMPT": "1",
            "WORKFLOW_RUN_PR_URL": "https://github.com/owner/repo/pull/42",
            "SUMMARY_WRITTEN": "true",
        }
        env.update(overrides)
        return env

    def _job_resp(self, job_id=111, conclusion="success"):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "jobs": [
                {
                    "id": job_id,
                    "name": "build-and-test",
                    "run_attempt": 1,
                    "conclusion": conclusion,
                }
            ]
        }
        return mock_resp

    @patch("post_pr_ci_summary_link.find_existing_comment")
    @patch("post_pr_ci_summary_link.upsert_comment")
    @patch("post_pr_ci_summary_link.requests")
    def test_posts_link_with_summary_anchor_when_job_id_found(
        self, mock_requests, mock_upsert, mock_find_existing
    ):
        mock_requests.get.return_value = self._job_resp(111, "success")
        mock_find_existing.return_value = link.CommentLookup(
            fetch_ok=True, comment_id=None, body=None
        )
        mock_upsert.return_value = True
        with patch.dict(os.environ, self._env(), clear=True):
            self.assertEqual(link.main([]), 0)
        body = mock_upsert.call_args[0][3]
        self.assertIn("https://github.com/owner/repo/actions/runs/999#summary-111", body)

    @patch("post_pr_ci_summary_link.find_existing_comment")
    @patch("post_pr_ci_summary_link.upsert_comment")
    @patch("post_pr_ci_summary_link.requests")
    def test_posts_pass_result_when_job_succeeded(
        self, mock_requests, mock_upsert, mock_find_existing
    ):
        mock_requests.get.return_value = self._job_resp(111, "success")
        mock_find_existing.return_value = link.CommentLookup(
            fetch_ok=True, comment_id=None, body=None
        )
        mock_upsert.return_value = True
        with patch.dict(os.environ, self._env(), clear=True):
            self.assertEqual(link.main([]), 0)
        body = mock_upsert.call_args[0][3]
        self.assertIn("42 pass", body)

    @patch("post_pr_ci_summary_link.find_existing_comment")
    @patch("post_pr_ci_summary_link.upsert_comment")
    @patch("post_pr_ci_summary_link.requests")
    def test_posts_fail_result_when_job_failed(
        self, mock_requests, mock_upsert, mock_find_existing
    ):
        mock_requests.get.return_value = self._job_resp(111, "failure")
        mock_find_existing.return_value = link.CommentLookup(
            fetch_ok=True, comment_id=None, body=None
        )
        mock_upsert.return_value = True
        with patch.dict(os.environ, self._env(), clear=True):
            self.assertEqual(link.main([]), 0)
        body = mock_upsert.call_args[0][3]
        self.assertIn("42 fail", body)

    @patch("post_pr_ci_summary_link.find_existing_comment")
    @patch("post_pr_ci_summary_link.upsert_comment")
    @patch("post_pr_ci_summary_link.requests")
    def test_falls_back_to_run_url_when_job_id_missing(
        self, mock_requests, mock_upsert, mock_find_existing
    ):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"jobs": []}
        mock_requests.get.return_value = mock_resp
        mock_find_existing.return_value = link.CommentLookup(
            fetch_ok=True, comment_id=None, body=None
        )
        mock_upsert.return_value = True
        with patch.dict(os.environ, self._env(), clear=True):
            self.assertEqual(link.main([]), 0)
        body = mock_upsert.call_args[0][3]
        self.assertIn("https://github.com/owner/repo/actions/runs/999", body)
        self.assertNotIn("#summary-", body)

    @patch("post_pr_ci_summary_link.find_existing_comment")
    @patch("post_pr_ci_summary_link.upsert_comment")
    @patch("post_pr_ci_summary_link.requests")
    def test_docs_only_pr_shows_skip_result(self, mock_requests, mock_upsert, mock_find_existing):
        """Docs-only PRs (SUMMARY_WRITTEN=false) show 'skip' as the result."""
        mock_requests.get.return_value = self._job_resp(111, "success")
        mock_find_existing.return_value = link.CommentLookup(
            fetch_ok=True, comment_id=None, body=None
        )
        mock_upsert.return_value = True
        with patch.dict(os.environ, self._env(SUMMARY_WRITTEN="false"), clear=True):
            self.assertEqual(link.main([]), 0)
        body = mock_upsert.call_args[0][3]
        self.assertIn("skip", body)
        self.assertNotIn("#summary-", body)

    @patch("post_pr_ci_summary_link.find_existing_comment")
    @patch("post_pr_ci_summary_link.upsert_comment")
    @patch("post_pr_ci_summary_link.requests")
    def test_treats_missing_summary_written_as_false(
        self, mock_requests, mock_upsert, mock_find_existing
    ):
        mock_requests.get.return_value = self._job_resp(111, "success")
        mock_find_existing.return_value = link.CommentLookup(
            fetch_ok=True, comment_id=None, body=None
        )
        mock_upsert.return_value = True
        env = self._env()
        del env["SUMMARY_WRITTEN"]
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(link.main([]), 0)
        body = mock_upsert.call_args[0][3]
        self.assertNotIn("#summary-", body)
        self.assertIn("skip", body)

    @patch("post_pr_ci_summary_link.find_existing_comment")
    @patch("post_pr_ci_summary_link.upsert_comment")
    @patch("post_pr_ci_summary_link.requests")
    def test_reads_github_run_number(self, mock_requests, mock_upsert, mock_find_existing):
        """The run number should appear as the second token in the list item."""
        mock_requests.get.return_value = self._job_resp(111, "success")
        mock_find_existing.return_value = link.CommentLookup(
            fetch_ok=True, comment_id=None, body=None
        )
        mock_upsert.return_value = True
        with patch.dict(os.environ, self._env(GITHUB_RUN_NUMBER="1337"), clear=True):
            self.assertEqual(link.main([]), 0)
        body = mock_upsert.call_args[0][3]
        self.assertIn("1337", body)

    @patch("post_pr_ci_summary_link.find_existing_comment")
    @patch("post_pr_ci_summary_link.upsert_comment")
    def test_does_not_clobber_history_when_fetch_fails(self, mock_upsert, mock_find_existing):
        """When find_existing_comment returns fetch_ok=False (API error), main
        must not call upsert_comment at all.
        Posting a fresh comment when the prior one may exist would orphan the
        prior history."""
        mock_find_existing.return_value = link.CommentLookup(
            fetch_ok=False, comment_id=None, body=None
        )
        with patch("post_pr_ci_summary_link.find_job") as mock_find_job:
            mock_find_job.return_value = {"id": 111, "conclusion": "success"}
            with patch.dict(os.environ, self._env(), clear=True):
                self.assertEqual(link.main([]), 0)
        mock_upsert.assert_not_called()

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
