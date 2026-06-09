#!/usr/bin/env python3
"""Unit tests for strip_session_bylines.py."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, os.path.dirname(__file__))
import strip_session_bylines as ssb  # noqa: E402


# ---------------------------------------------------------------------------
# strip_bylines -- pure text transformation
# ---------------------------------------------------------------------------

class TestStripBylines(unittest.TestCase):

    def test_strips_single_byline(self):
        text = "Some text\nhttps://claude.ai/code/session_abc123"
        self.assertEqual(ssb.strip_bylines(text), "Some text")

    def test_strips_byline_mid_text(self):
        text = "Before\nhttps://claude.ai/code/session_xyz\nAfter"
        self.assertEqual(ssb.strip_bylines(text), "Before\nAfter")

    def test_strips_multiple_bylines(self):
        text = (
            "Line one\nhttps://claude.ai/code/session_aaa\n"
            "Line two\nhttps://claude.ai/code/session_bbb"
        )
        self.assertEqual(ssb.strip_bylines(text), "Line one\nLine two")

    def test_unchanged_when_no_byline(self):
        text = "Just a normal comment with no byline."
        self.assertEqual(ssb.strip_bylines(text), text)

    def test_unchanged_for_partial_url(self):
        # A URL without the session_ prefix must not be touched.
        text = "See https://claude.ai/code/other_thing for details."
        self.assertEqual(ssb.strip_bylines(text), text)

    def test_requires_leading_newline(self):
        # The byline must be preceded by a newline to be stripped.
        text = "https://claude.ai/code/session_abc123 is mentioned inline"
        self.assertEqual(ssb.strip_bylines(text), text)

    def test_strips_byline_with_long_token(self):
        token = "session_" + "a" * 50
        text = f"Final line\nhttps://claude.ai/code/{token}"
        self.assertEqual(ssb.strip_bylines(text), "Final line")

    def test_empty_string_unchanged(self):
        self.assertEqual(ssb.strip_bylines(""), "")


# ---------------------------------------------------------------------------
# handle_issue
# ---------------------------------------------------------------------------

class TestHandleIssue(unittest.TestCase):

    def _payload(self, number, body):
        return {"issue": {"number": number, "body": body}}

    @patch("strip_session_bylines.requests")
    def test_patches_issue_body_when_byline_found(self, mock_requests):
        mock_resp = MagicMock()
        mock_requests.patch.return_value = mock_resp
        payload = self._payload(7, "Good text\nhttps://claude.ai/code/session_abc")

        result = ssb.handle_issue("tok", "owner/repo", payload)

        self.assertTrue(result)
        mock_requests.patch.assert_called_once()
        url, kwargs = mock_requests.patch.call_args[0][0], mock_requests.patch.call_args[1]
        self.assertIn("/issues/7", url)
        self.assertEqual(kwargs["json"]["body"], "Good text")

    @patch("strip_session_bylines.requests")
    def test_skips_patch_when_no_byline(self, mock_requests):
        payload = self._payload(7, "Just text, no byline.")
        result = ssb.handle_issue("tok", "owner/repo", payload)
        self.assertFalse(result)
        mock_requests.patch.assert_not_called()

    @patch("strip_session_bylines.requests")
    def test_handles_none_body_gracefully(self, mock_requests):
        payload = {"issue": {"number": 1, "body": None}}
        result = ssb.handle_issue("tok", "owner/repo", payload)
        self.assertFalse(result)
        mock_requests.patch.assert_not_called()

    @patch("strip_session_bylines.requests")
    def test_returns_false_on_patch_error(self, mock_requests):
        mock_requests.patch.side_effect = Exception("network")
        payload = self._payload(9, "text\nhttps://claude.ai/code/session_z")
        result = ssb.handle_issue("tok", "owner/repo", payload)
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# handle_issue_comment
# ---------------------------------------------------------------------------

class TestHandleIssueComment(unittest.TestCase):

    def _payload(self, comment_id, body):
        return {"comment": {"id": comment_id, "body": body}}

    @patch("strip_session_bylines.requests")
    def test_patches_comment_body_when_byline_found(self, mock_requests):
        mock_resp = MagicMock()
        mock_requests.patch.return_value = mock_resp
        payload = self._payload(55, "text\nhttps://claude.ai/code/session_foo")

        result = ssb.handle_issue_comment("tok", "owner/repo", payload)

        self.assertTrue(result)
        url = mock_requests.patch.call_args[0][0]
        self.assertIn("/issues/comments/55", url)
        self.assertEqual(mock_requests.patch.call_args[1]["json"]["body"], "text")

    @patch("strip_session_bylines.requests")
    def test_skips_patch_when_no_byline(self, mock_requests):
        payload = self._payload(55, "No byline here.")
        result = ssb.handle_issue_comment("tok", "owner/repo", payload)
        self.assertFalse(result)
        mock_requests.patch.assert_not_called()


# ---------------------------------------------------------------------------
# handle_pull_request
# ---------------------------------------------------------------------------

class TestHandlePullRequest(unittest.TestCase):

    def _payload(self, number, body):
        return {"pull_request": {"number": number, "body": body}}

    @patch("strip_session_bylines.requests")
    def test_patches_pr_body_when_byline_found(self, mock_requests):
        mock_resp = MagicMock()
        mock_requests.patch.return_value = mock_resp
        payload = self._payload(12, "PR body\nhttps://claude.ai/code/session_qqq")

        result = ssb.handle_pull_request("tok", "owner/repo", payload)

        self.assertTrue(result)
        url = mock_requests.patch.call_args[0][0]
        self.assertIn("/pulls/12", url)
        self.assertEqual(mock_requests.patch.call_args[1]["json"]["body"], "PR body")

    @patch("strip_session_bylines.requests")
    def test_skips_patch_when_no_byline(self, mock_requests):
        payload = self._payload(12, "Clean PR body.")
        result = ssb.handle_pull_request("tok", "owner/repo", payload)
        self.assertFalse(result)
        mock_requests.patch.assert_not_called()

    @patch("strip_session_bylines.requests")
    def test_handles_none_body_gracefully(self, mock_requests):
        payload = {"pull_request": {"number": 3, "body": None}}
        result = ssb.handle_pull_request("tok", "owner/repo", payload)
        self.assertFalse(result)
        mock_requests.patch.assert_not_called()


# ---------------------------------------------------------------------------
# handle_pull_request_review_comment
# ---------------------------------------------------------------------------

class TestHandlePRReviewComment(unittest.TestCase):

    def _payload(self, comment_id, body):
        return {"comment": {"id": comment_id, "body": body}}

    @patch("strip_session_bylines.requests")
    def test_patches_review_comment_when_byline_found(self, mock_requests):
        mock_resp = MagicMock()
        mock_requests.patch.return_value = mock_resp
        payload = self._payload(77, "nit\nhttps://claude.ai/code/session_bar")

        result = ssb.handle_pull_request_review_comment("tok", "owner/repo", payload)

        self.assertTrue(result)
        url = mock_requests.patch.call_args[0][0]
        self.assertIn("/pulls/comments/77", url)
        self.assertEqual(mock_requests.patch.call_args[1]["json"]["body"], "nit")

    @patch("strip_session_bylines.requests")
    def test_skips_patch_when_no_byline(self, mock_requests):
        payload = self._payload(77, "Clean review comment.")
        result = ssb.handle_pull_request_review_comment("tok", "owner/repo", payload)
        self.assertFalse(result)
        mock_requests.patch.assert_not_called()


# ---------------------------------------------------------------------------
# main -- environment and dispatch
# ---------------------------------------------------------------------------

class TestMain(unittest.TestCase):

    def _write_payload(self, payload: dict) -> str:
        fh = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(payload, fh)
        fh.close()
        return fh.name

    def _env(self, **overrides):
        base = {
            "GITHUB_TOKEN": "tok",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_EVENT_NAME": "issues",
            "GITHUB_EVENT_PATH": "/tmp/event.json",
        }
        base.update(overrides)
        return base

    def test_skips_when_token_missing(self):
        with patch.dict(os.environ, self._env(GITHUB_TOKEN=""), clear=True):
            self.assertEqual(ssb.main([]), 0)

    def test_skips_when_repository_missing(self):
        with patch.dict(os.environ, self._env(GITHUB_REPOSITORY=""), clear=True):
            self.assertEqual(ssb.main([]), 0)

    def test_skips_when_event_name_missing(self):
        with patch.dict(os.environ, self._env(GITHUB_EVENT_NAME=""), clear=True):
            self.assertEqual(ssb.main([]), 0)

    def test_skips_when_event_path_missing(self):
        with patch.dict(os.environ, self._env(GITHUB_EVENT_PATH=""), clear=True):
            self.assertEqual(ssb.main([]), 0)

    def test_skips_for_unsupported_event_name(self):
        path = self._write_payload({})
        try:
            with patch.dict(
                os.environ,
                self._env(GITHUB_EVENT_NAME="push", GITHUB_EVENT_PATH=path),
                clear=True,
            ):
                self.assertEqual(ssb.main([]), 0)
        finally:
            os.unlink(path)

    def test_skips_when_event_path_unreadable(self):
        with patch.dict(
            os.environ,
            self._env(GITHUB_EVENT_PATH="/nonexistent/path.json"),
            clear=True,
        ):
            self.assertEqual(ssb.main([]), 0)

    @patch("strip_session_bylines.handle_issue")
    def test_dispatches_issues_event(self, mock_handler):
        mock_handler.return_value = True
        payload = {"issue": {"number": 1, "body": "body"}}
        path = self._write_payload(payload)
        try:
            with patch.dict(
                os.environ,
                self._env(GITHUB_EVENT_NAME="issues", GITHUB_EVENT_PATH=path),
                clear=True,
            ):
                self.assertEqual(ssb.main([]), 0)
        finally:
            os.unlink(path)
        mock_handler.assert_called_once_with("tok", "owner/repo", payload)

    @patch("strip_session_bylines.handle_issue_comment")
    def test_dispatches_issue_comment_event(self, mock_handler):
        mock_handler.return_value = False
        payload = {"comment": {"id": 5, "body": "text"}}
        path = self._write_payload(payload)
        try:
            with patch.dict(
                os.environ,
                self._env(GITHUB_EVENT_NAME="issue_comment", GITHUB_EVENT_PATH=path),
                clear=True,
            ):
                self.assertEqual(ssb.main([]), 0)
        finally:
            os.unlink(path)
        mock_handler.assert_called_once_with("tok", "owner/repo", payload)

    @patch("strip_session_bylines.handle_pull_request")
    def test_dispatches_pull_request_event(self, mock_handler):
        mock_handler.return_value = False
        payload = {"pull_request": {"number": 3, "body": "pr body"}}
        path = self._write_payload(payload)
        try:
            with patch.dict(
                os.environ,
                self._env(GITHUB_EVENT_NAME="pull_request", GITHUB_EVENT_PATH=path),
                clear=True,
            ):
                self.assertEqual(ssb.main([]), 0)
        finally:
            os.unlink(path)
        mock_handler.assert_called_once_with("tok", "owner/repo", payload)

    @patch("strip_session_bylines.handle_pull_request_review_comment")
    def test_dispatches_pr_review_comment_event(self, mock_handler):
        mock_handler.return_value = False
        payload = {"comment": {"id": 9, "body": "review"}}
        path = self._write_payload(payload)
        try:
            with patch.dict(
                os.environ,
                self._env(
                    GITHUB_EVENT_NAME="pull_request_review_comment",
                    GITHUB_EVENT_PATH=path,
                ),
                clear=True,
            ):
                self.assertEqual(ssb.main([]), 0)
        finally:
            os.unlink(path)
        mock_handler.assert_called_once_with("tok", "owner/repo", payload)


if __name__ == "__main__":
    unittest.main()
