#!/usr/bin/env python3
"""Unit tests for wake_held_issues.py."""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import wake_held_issues as whi  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 8, 10, 5, 0, 0, tzinfo=timezone.utc)


def _make_issue(number: int, labels: list[str], state: str = "closed", is_pr: bool = False) -> dict:
    issue = {
        "number": number,
        "labels": [{"name": lbl} for lbl in labels],
        "state": state,
    }
    if is_pr:
        issue["pull_request"] = {"url": "https://api.github.com/..."}
    return issue


def _labeled_event(label: str, when: datetime) -> dict:
    return {
        "event": "labeled",
        "label": {"name": label},
        "created_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ---------------------------------------------------------------------------
# fetch_issues_with_label tests
# ---------------------------------------------------------------------------


class TestFetchIssuesWithLabel(unittest.TestCase):
    def _paged(self, pages: list[list[dict]]):
        call_count = [0]

        def side_effect(path, token, method="GET", body=None):
            idx = call_count[0]
            call_count[0] += 1
            return pages[idx] if idx < len(pages) else []

        return side_effect

    def test_returns_matching_issues(self):
        issue = _make_issue(1, ["hold 30 days"])
        with patch.object(whi.emxl, "gh_api", side_effect=self._paged([[issue], []])):
            result = whi.fetch_issues_with_label("owner/repo", "tok", "hold 30 days")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["number"], 1)

    def test_excludes_pull_requests(self):
        issue = _make_issue(1, ["hold 30 days"])
        pr = _make_issue(2, ["hold 30 days"], is_pr=True)
        with patch.object(whi.emxl, "gh_api", side_effect=self._paged([[issue, pr], []])):
            result = whi.fetch_issues_with_label("owner/repo", "tok", "hold 30 days")
        self.assertEqual([i["number"] for i in result], [1])

    def test_paginates(self):
        page1 = [_make_issue(i, ["hold 30 days"]) for i in range(1, 4)]
        page2 = [_make_issue(i, ["hold 30 days"]) for i in range(4, 6)]
        with patch.object(whi.emxl, "gh_api", side_effect=self._paged([page1, page2, []])):
            result = whi.fetch_issues_with_label("owner/repo", "tok", "hold 30 days")
        self.assertEqual(len(result), 5)

    def test_includes_state_all_and_encoded_label(self):
        calls = []

        def side_effect(path, token, method="GET", body=None):
            calls.append(path)
            return []

        with patch.object(whi.emxl, "gh_api", side_effect=side_effect):
            whi.fetch_issues_with_label("owner/repo", "tok", "hold 30 days")

        self.assertIn("state=all", calls[0])
        self.assertIn("hold%2030%20days", calls[0])


# ---------------------------------------------------------------------------
# find_label_applied_at tests
# ---------------------------------------------------------------------------


class TestFindLabelAppliedAt(unittest.TestCase):
    def _paged(self, pages: list[list[dict]]):
        call_count = [0]

        def side_effect(path, token, method="GET", body=None):
            idx = call_count[0]
            call_count[0] += 1
            return pages[idx] if idx < len(pages) else []

        return side_effect

    def test_returns_none_when_no_matching_event(self):
        with patch.object(whi.emxl, "gh_api", side_effect=self._paged([[], []])):
            result = whi.find_label_applied_at(1, "hold 30 days", "owner/repo", "tok")
        self.assertIsNone(result)

    def test_returns_timestamp_of_matching_event(self):
        when = _NOW - timedelta(days=31)
        events = [
            {"event": "commented", "created_at": when.strftime("%Y-%m-%dT%H:%M:%SZ")},
            _labeled_event("hold 30 days", when),
        ]
        with patch.object(whi.emxl, "gh_api", side_effect=self._paged([events, []])):
            result = whi.find_label_applied_at(1, "hold 30 days", "owner/repo", "tok")
        self.assertEqual(result, when)

    def test_ignores_events_for_other_labels(self):
        when = _NOW - timedelta(days=31)
        events = [_labeled_event("orchestrate", when)]
        with patch.object(whi.emxl, "gh_api", side_effect=self._paged([events, []])):
            result = whi.find_label_applied_at(1, "hold 30 days", "owner/repo", "tok")
        self.assertIsNone(result)

    def test_case_insensitive_label_match(self):
        when = _NOW - timedelta(days=31)
        events = [_labeled_event("Hold 30 Days", when)]
        with patch.object(whi.emxl, "gh_api", side_effect=self._paged([events, []])):
            result = whi.find_label_applied_at(1, "hold 30 days", "owner/repo", "tok")
        self.assertEqual(result, when)

    def test_returns_most_recent_of_several_matching_events(self):
        older = _NOW - timedelta(days=100)
        newer = _NOW - timedelta(days=31)
        events = [
            _labeled_event("hold 30 days", older),
            _labeled_event("hold 30 days", newer),
        ]
        with patch.object(whi.emxl, "gh_api", side_effect=self._paged([events, []])):
            result = whi.find_label_applied_at(1, "hold 30 days", "owner/repo", "tok")
        self.assertEqual(result, newer)

    def test_paginates_across_events(self):
        when = _NOW - timedelta(days=31)
        page1 = [{"event": "commented", "created_at": when.strftime("%Y-%m-%dT%H:%M:%SZ")}] * 100
        page2 = [_labeled_event("hold 30 days", when)]
        with patch.object(whi.emxl, "gh_api", side_effect=self._paged([page1, page2, []])):
            result = whi.find_label_applied_at(1, "hold 30 days", "owner/repo", "tok")
        self.assertEqual(result, when)


# ---------------------------------------------------------------------------
# wake_issue tests
# ---------------------------------------------------------------------------


class TestWakeIssue(unittest.TestCase):
    def test_strips_hold_label_and_reopens(self):
        issue = _make_issue(7, ["hold 30 days", "bug"])
        with patch.object(whi.emxl, "gh_api") as mock_api:
            whi.wake_issue(issue, "hold 30 days", "owner/repo", "tok")

        patch_call = mock_api.call_args_list[0]
        self.assertEqual(patch_call[1]["method"], "PATCH")
        self.assertEqual(patch_call[1]["body"]["state"], "open")
        self.assertEqual(patch_call[1]["body"]["labels"], ["bug"])

    def test_strips_process_state_labels(self):
        issue = _make_issue(7, ["hold 90 days", "orchestrate", "changes requested", "ci"])
        with patch.object(whi.emxl, "gh_api") as mock_api:
            whi.wake_issue(issue, "hold 90 days", "owner/repo", "tok")

        patch_call = mock_api.call_args_list[0]
        self.assertEqual(patch_call[1]["body"]["labels"], ["ci"])

    def test_posts_a_comment(self):
        issue = _make_issue(7, ["hold 30 days"])
        with patch.object(whi.emxl, "gh_api") as mock_api:
            whi.wake_issue(issue, "hold 30 days", "owner/repo", "tok")

        comment_call = mock_api.call_args_list[1]
        self.assertIn("comments", comment_call[0][0])
        self.assertEqual(comment_call[1]["method"], "POST")
        self.assertIn("hold 30 days", comment_call[1]["body"]["body"])

    def test_comment_notes_other_stripped_labels(self):
        issue = _make_issue(7, ["hold 30 days", "orchestrating"])
        with patch.object(whi.emxl, "gh_api") as mock_api:
            whi.wake_issue(issue, "hold 30 days", "owner/repo", "tok")

        comment_body = mock_api.call_args_list[1][1]["body"]["body"]
        self.assertIn("orchestrating", comment_body)

    def test_no_other_labels_left_intact(self):
        issue = _make_issue(7, ["hold 30 days", "p1", "ci"])
        with patch.object(whi.emxl, "gh_api") as mock_api:
            whi.wake_issue(issue, "hold 30 days", "owner/repo", "tok")

        patch_call = mock_api.call_args_list[0]
        self.assertCountEqual(patch_call[1]["body"]["labels"], ["p1", "ci"])


# ---------------------------------------------------------------------------
# main() tests
# ---------------------------------------------------------------------------


class TestMain(unittest.TestCase):
    def setUp(self):
        self._env_patch = patch.dict(
            os.environ,
            {"GITHUB_TOKEN": "test-token", "GITHUB_REPOSITORY": "owner/repo"},
        )
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()

    def test_exit_0_when_token_missing(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
            result = whi.main()
        self.assertEqual(result, 0)

    def test_exit_0_when_repository_missing(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": ""}):
            result = whi.main()
        self.assertEqual(result, 0)

    def test_wakes_issue_past_its_hold(self):
        """applied_at 31 days before real "now" clears the 30-day hold."""
        issue = _make_issue(1, ["hold 30 days"])
        applied_at = datetime.now(timezone.utc) - timedelta(days=31)

        def fetch_side_effect(repo, token, label):
            return [issue] if label == "hold 30 days" else []

        with patch.object(whi, "fetch_issues_with_label", side_effect=fetch_side_effect):
            with patch.object(whi, "find_label_applied_at", return_value=applied_at):
                with patch.object(whi, "wake_issue") as mock_wake:
                    result = whi.main()

        self.assertEqual(result, 0)
        mock_wake.assert_called_once_with(issue, "hold 30 days", "owner/repo", "test-token")

    def test_leaves_issue_within_hold_period(self):
        """applied_at 5 days before real "now" has not yet cleared the 30-day hold."""
        issue = _make_issue(1, ["hold 30 days"])
        applied_at = datetime.now(timezone.utc) - timedelta(days=5)

        def fetch_side_effect(repo, token, label):
            return [issue] if label == "hold 30 days" else []

        with patch.object(whi, "fetch_issues_with_label", side_effect=fetch_side_effect):
            with patch.object(whi, "find_label_applied_at", return_value=applied_at):
                with patch.object(whi, "wake_issue") as mock_wake:
                    result = whi.main()

        self.assertEqual(result, 0)
        mock_wake.assert_not_called()

    def test_skips_when_applied_at_unknown(self):
        issue = _make_issue(1, ["hold 30 days"])

        def fetch_side_effect(repo, token, label):
            return [issue] if label == "hold 30 days" else []

        with patch.object(whi, "fetch_issues_with_label", side_effect=fetch_side_effect):
            with patch.object(whi, "find_label_applied_at", return_value=None):
                with patch.object(whi, "wake_issue") as mock_wake:
                    result = whi.main()

        self.assertEqual(result, 0)
        mock_wake.assert_not_called()

    def test_continues_after_fetch_error_for_one_label(self):
        def fetch_side_effect(repo, token, label):
            if label == "hold 30 days":
                raise Exception("network error")
            return []

        with patch.object(whi, "fetch_issues_with_label", side_effect=fetch_side_effect):
            result = whi.main()

        self.assertEqual(result, 0)

    def test_continues_after_wake_error_for_one_issue(self):
        issue = _make_issue(1, ["hold 30 days"])
        applied_at = _NOW - timedelta(days=31)

        def fetch_side_effect(repo, token, label):
            return [issue] if label == "hold 30 days" else []

        with patch.object(whi, "fetch_issues_with_label", side_effect=fetch_side_effect):
            with patch.object(whi, "find_label_applied_at", return_value=applied_at):
                with patch.object(whi, "wake_issue", side_effect=Exception("boom")):
                    result = whi.main()

        self.assertEqual(result, 0)

    def test_no_issues_found_wakes_nothing(self):
        with patch.object(whi, "fetch_issues_with_label", return_value=[]):
            with patch.object(whi, "wake_issue") as mock_wake:
                result = whi.main()

        self.assertEqual(result, 0)
        mock_wake.assert_not_called()


if __name__ == "__main__":
    unittest.main()
