#!/usr/bin/env python3
"""Unit tests for archive_stale_test_failures.py."""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import archive_stale_test_failures as astf  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 25, 4, 0, 0, tzinfo=timezone.utc)
_CUTOFF = _NOW - timedelta(days=21)  # 2026-05-04T04:00:00+00:00


def _make_issue(
    number: int, title: str, updated_at: datetime, labels: list[str], state: str = "open"
) -> dict:
    """Build a minimal GitHub issue dict as returned by the API."""
    return {
        "number": number,
        "title": title,
        "updated_at": updated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "labels": [{"name": lbl} for lbl in labels],
        "state": state,
    }


# ---------------------------------------------------------------------------
# gh_api tests
# ---------------------------------------------------------------------------


class TestGhApi(unittest.TestCase):
    @patch("archive_stale_test_failures.urllib.request.urlopen")
    def test_get_request_sets_correct_headers(self, mock_urlopen):
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_cm)
        mock_cm.__exit__ = MagicMock(return_value=False)
        mock_cm.read.return_value = b"[]"
        mock_urlopen.return_value = mock_cm

        astf.gh_api("repos/owner/repo/issues", token="mytoken")

        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.get_header("Authorization"), "Bearer mytoken")
        self.assertEqual(req.get_header("Accept"), "application/vnd.github+json")
        self.assertEqual(req.get_header("X-github-api-version"), "2022-11-28")

    @patch("archive_stale_test_failures.urllib.request.urlopen")
    def test_patch_request_sends_json_body(self, mock_urlopen):
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_cm)
        mock_cm.__exit__ = MagicMock(return_value=False)
        mock_cm.read.return_value = b"{}"
        mock_urlopen.return_value = mock_cm

        astf.gh_api(
            "repos/owner/repo/issues/1",
            token="tok",
            method="PATCH",
            body={"labels": ["test-failure-archive"]},
        )

        req, data = mock_urlopen.call_args[0]
        self.assertEqual(req.method, "PATCH")
        self.assertIsNotNone(data)

    @patch("archive_stale_test_failures.urllib.request.urlopen")
    def test_returns_parsed_json(self, mock_urlopen):
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_cm)
        mock_cm.__exit__ = MagicMock(return_value=False)
        mock_cm.read.return_value = b'{"number": 42}'
        mock_urlopen.return_value = mock_cm

        result = astf.gh_api("repos/owner/repo/issues/42", token="tok")
        self.assertEqual(result, {"number": 42})


# ---------------------------------------------------------------------------
# fetch_stale_issues tests
# ---------------------------------------------------------------------------


class TestFetchStaleIssues(unittest.TestCase):
    def _gh_api_side_effect(self, pages: list[list[dict]]):
        """Return a side_effect function that yields successive pages."""
        call_count = [0]

        def side_effect(path, token, method="GET", body=None):
            idx = call_count[0]
            call_count[0] += 1
            if idx < len(pages):
                return pages[idx]
            return []

        return side_effect

    def test_stale_issue_returned(self):
        stale_ts = _CUTOFF - timedelta(days=1)
        issue = _make_issue(1, "Old failure", stale_ts, ["test-failure"])

        with patch.object(astf, "gh_api", side_effect=self._gh_api_side_effect([[issue], []])):
            result = astf.fetch_stale_issues("owner/repo", "tok", _CUTOFF)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["number"], 1)

    def test_recent_issue_not_returned(self):
        fresh_ts = _CUTOFF + timedelta(days=1)
        issue = _make_issue(2, "Recent failure", fresh_ts, ["test-failure"])

        with patch.object(astf, "gh_api", side_effect=self._gh_api_side_effect([[issue], []])):
            result = astf.fetch_stale_issues("owner/repo", "tok", _CUTOFF)

        self.assertEqual(result, [])

    def test_issue_exactly_at_cutoff_not_returned(self):
        """updated_at == cutoff means still active (cutoff is exclusive lower bound)."""
        issue = _make_issue(3, "Exact cutoff", _CUTOFF, ["test-failure"])

        with patch.object(astf, "gh_api", side_effect=self._gh_api_side_effect([[issue], []])):
            result = astf.fetch_stale_issues("owner/repo", "tok", _CUTOFF)

        self.assertEqual(result, [])

    def test_pagination_stops_on_empty_page(self):
        """An empty page stops fetching; only one page of results is processed."""
        stale_ts = _CUTOFF - timedelta(days=5)
        issue = _make_issue(10, "Issue", stale_ts, ["test-failure"])

        calls = []

        def side_effect(path, token, method="GET", body=None):
            calls.append(path)
            if len(calls) == 1:
                return [issue]
            return []  # second call returns empty

        with patch.object(astf, "gh_api", side_effect=side_effect):
            result = astf.fetch_stale_issues("owner/repo", "tok", _CUTOFF)

        self.assertEqual(len(result), 1)
        self.assertEqual(len(calls), 2)  # fetched page 1 then page 2 (empty)

    def test_pagination_two_full_pages(self):
        """Two pages of results are both collected."""
        stale_ts = _CUTOFF - timedelta(days=10)
        page1 = [_make_issue(i, f"Issue {i}", stale_ts, ["test-failure"]) for i in range(1, 4)]
        page2 = [_make_issue(i, f"Issue {i}", stale_ts, ["test-failure"]) for i in range(4, 7)]

        with patch.object(astf, "gh_api", side_effect=self._gh_api_side_effect([page1, page2, []])):
            result = astf.fetch_stale_issues("owner/repo", "tok", _CUTOFF)

        self.assertEqual(len(result), 6)

    def test_mixed_stale_and_fresh(self):
        stale_ts = _CUTOFF - timedelta(days=3)
        fresh_ts = _CUTOFF + timedelta(days=3)
        issues = [
            _make_issue(1, "Stale", stale_ts, ["test-failure"]),
            _make_issue(2, "Fresh", fresh_ts, ["test-failure"]),
        ]

        with patch.object(astf, "gh_api", side_effect=self._gh_api_side_effect([issues, []])):
            result = astf.fetch_stale_issues("owner/repo", "tok", _CUTOFF)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["number"], 1)

    def test_stale_closed_issue_returned(self):
        """A closed, stale, test-failure issue is included (not just open ones)."""
        stale_ts = _CUTOFF - timedelta(days=1)
        issue = _make_issue(4, "Closed stale failure", stale_ts, ["test-failure"], state="closed")

        with patch.object(astf, "gh_api", side_effect=self._gh_api_side_effect([[issue], []])):
            result = astf.fetch_stale_issues("owner/repo", "tok", _CUTOFF)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["number"], 4)

    def test_query_does_not_filter_by_state(self):
        """The issues query omits state=open so closed issues are also fetched."""
        with patch.object(
            astf, "gh_api", side_effect=self._gh_api_side_effect([[], []])
        ) as mock_api:
            astf.fetch_stale_issues("owner/repo", "tok", _CUTOFF)

        path = mock_api.call_args_list[0][0][0]
        self.assertNotIn("state=open", path)
        self.assertIn("labels=test-failure", path)


# ---------------------------------------------------------------------------
# archive_issue tests
# ---------------------------------------------------------------------------


class TestArchiveIssue(unittest.TestCase):
    def test_replaces_test_failure_with_archive_label(self):
        issue = _make_issue(
            42,
            "Old failure",
            _CUTOFF - timedelta(days=1),
            ["test-failure", "ci", "orchestrate"],
        )

        with patch.object(astf, "gh_api") as mock_api:
            astf.archive_issue(issue, "owner/repo", "tok")

        mock_api.assert_called_once()
        body = mock_api.call_args[1]["body"]
        self.assertIn("test-failure-archive", body["labels"])
        self.assertNotIn("test-failure", body["labels"])
        self.assertIn("ci", body["labels"])
        self.assertIn("orchestrate", body["labels"])

    def test_archive_label_added_even_when_only_label(self):
        issue = _make_issue(
            5,
            "Bare label issue",
            _CUTOFF - timedelta(days=1),
            ["test-failure"],
        )

        with patch.object(astf, "gh_api") as mock_api:
            astf.archive_issue(issue, "owner/repo", "tok")

        body = mock_api.call_args[1]["body"]
        self.assertEqual(body["labels"], ["test-failure-archive"])

    def test_uses_patch_method(self):
        issue = _make_issue(7, "Issue", _CUTOFF - timedelta(days=1), ["test-failure"])

        with patch.object(astf, "gh_api") as mock_api:
            astf.archive_issue(issue, "owner/repo", "tok")

        self.assertEqual(mock_api.call_args[1]["method"], "PATCH")

    def test_targets_correct_issue_number_in_path(self):
        issue = _make_issue(99, "Issue", _CUTOFF - timedelta(days=1), ["test-failure"])

        with patch.object(astf, "gh_api") as mock_api:
            astf.archive_issue(issue, "owner/repo", "tok")

        path = mock_api.call_args[0][0]
        self.assertIn("99", path)


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
                "STALE_DAYS": "21",
            },
        )
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()

    def test_exit_0_when_token_missing(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
            result = astf.main()
        self.assertEqual(result, 0)

    def test_exit_0_when_repository_missing(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": ""}):
            result = astf.main()
        self.assertEqual(result, 0)

    def test_stale_days_env_var_respected(self):
        """STALE_DAYS=5 means only issues older than 5 days are archived."""
        captured_cutoff = []

        def fake_fetch(repo, token, cutoff):
            captured_cutoff.append(cutoff)
            return []

        with patch.dict(os.environ, {"STALE_DAYS": "5"}):
            with patch.object(astf, "fetch_stale_issues", side_effect=fake_fetch):
                astf.main()

        self.assertEqual(len(captured_cutoff), 1)
        # cutoff should be approximately now - 5 days
        expected = datetime.now(timezone.utc) - timedelta(days=5)
        diff = abs((captured_cutoff[0] - expected).total_seconds())
        self.assertLess(diff, 5)  # within 5 seconds

    def test_archives_stale_issues(self):
        stale_ts = datetime.now(timezone.utc) - timedelta(days=30)
        stale_issue = _make_issue(10, "Stale", stale_ts, ["test-failure"])

        with patch.object(astf, "fetch_stale_issues", return_value=[stale_issue]):
            with patch.object(astf, "archive_issue") as mock_archive:
                result = astf.main()

        self.assertEqual(result, 0)
        mock_archive.assert_called_once_with(stale_issue, "owner/repo", "test-token")

    def test_exit_0_when_fetch_raises(self):
        with patch.object(astf, "fetch_stale_issues", side_effect=Exception("network error")):
            result = astf.main()
        self.assertEqual(result, 0)

    def test_exit_0_when_archive_raises(self):
        stale_ts = datetime.now(timezone.utc) - timedelta(days=30)
        stale_issue = _make_issue(11, "Stale", stale_ts, ["test-failure"])

        with patch.object(astf, "fetch_stale_issues", return_value=[stale_issue]):
            with patch.object(astf, "archive_issue", side_effect=Exception("API error")):
                result = astf.main()

        self.assertEqual(result, 0)

    def test_no_stale_issues_archives_nothing(self):
        with patch.object(astf, "fetch_stale_issues", return_value=[]):
            with patch.object(astf, "archive_issue") as mock_archive:
                result = astf.main()

        self.assertEqual(result, 0)
        mock_archive.assert_not_called()

    def test_archives_stale_closed_issue(self):
        """A closed, stale, test-failure issue returned by fetch_stale_issues is archived."""
        stale_ts = datetime.now(timezone.utc) - timedelta(days=30)
        closed_issue = _make_issue(12, "Closed stale", stale_ts, ["test-failure"], state="closed")

        with patch.object(astf, "fetch_stale_issues", return_value=[closed_issue]):
            with patch.object(astf, "archive_issue") as mock_archive:
                result = astf.main()

        self.assertEqual(result, 0)
        mock_archive.assert_called_once_with(closed_issue, "owner/repo", "test-token")

    def test_continues_after_single_archive_failure(self):
        """An error on one issue does not prevent processing subsequent issues."""
        stale_ts = datetime.now(timezone.utc) - timedelta(days=30)
        issues = [
            _make_issue(20, "First", stale_ts, ["test-failure"]),
            _make_issue(21, "Second", stale_ts, ["test-failure"]),
        ]
        archive_calls = []

        def fake_archive(issue, repo, token):
            archive_calls.append(issue["number"])
            if issue["number"] == 20:
                raise Exception("transient error")

        with patch.object(astf, "fetch_stale_issues", return_value=issues):
            with patch.object(astf, "archive_issue", side_effect=fake_archive):
                result = astf.main()

        self.assertEqual(result, 0)
        self.assertIn(21, archive_calls)


if __name__ == "__main__":
    unittest.main()
