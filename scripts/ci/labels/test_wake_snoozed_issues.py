#!/usr/bin/env python3
"""Unit tests for wake_snoozed_issues.py."""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import wake_snoozed_issues as wsi  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 8, 10, 5, 0, 0, tzinfo=timezone.utc)

# The 30-day rung's label, as main() hands it to find_label_applied_at().
_RUNG_30 = "snooze 30 days"


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
        issue = _make_issue(1, ["snooze 30 days"])
        with patch.object(wsi.emxl, "gh_api", side_effect=self._paged([[issue], []])):
            result = wsi.fetch_issues_with_label("owner/repo", "tok", "snooze 30 days")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["number"], 1)

    def test_excludes_pull_requests(self):
        issue = _make_issue(1, ["snooze 30 days"])
        pr = _make_issue(2, ["snooze 30 days"], is_pr=True)
        with patch.object(wsi.emxl, "gh_api", side_effect=self._paged([[issue, pr], []])):
            result = wsi.fetch_issues_with_label("owner/repo", "tok", "snooze 30 days")
        self.assertEqual([i["number"] for i in result], [1])

    def test_paginates(self):
        page1 = [_make_issue(i, ["snooze 30 days"]) for i in range(1, 4)]
        page2 = [_make_issue(i, ["snooze 30 days"]) for i in range(4, 6)]
        with patch.object(wsi.emxl, "gh_api", side_effect=self._paged([page1, page2, []])):
            result = wsi.fetch_issues_with_label("owner/repo", "tok", "snooze 30 days")
        self.assertEqual(len(result), 5)

    def test_includes_state_all_and_encoded_label(self):
        calls = []

        def side_effect(path, token, method="GET", body=None):
            calls.append(path)
            return []

        with patch.object(wsi.emxl, "gh_api", side_effect=side_effect):
            wsi.fetch_issues_with_label("owner/repo", "tok", "snooze 30 days")

        self.assertIn("state=all", calls[0])
        self.assertIn("snooze%2030%20days", calls[0])


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
        with patch.object(wsi.emxl, "gh_api", side_effect=self._paged([[], []])):
            result = wsi.find_label_applied_at(1, _RUNG_30, "owner/repo", "tok")
        self.assertIsNone(result)

    def test_returns_timestamp_of_matching_event(self):
        when = _NOW - timedelta(days=31)
        events = [
            {"event": "commented", "created_at": when.strftime("%Y-%m-%dT%H:%M:%SZ")},
            _labeled_event("snooze 30 days", when),
        ]
        with patch.object(wsi.emxl, "gh_api", side_effect=self._paged([events, []])):
            result = wsi.find_label_applied_at(1, _RUNG_30, "owner/repo", "tok")
        self.assertEqual(result, when)

    def test_ignores_events_for_other_labels(self):
        when = _NOW - timedelta(days=31)
        events = [_labeled_event("orchestrate", when)]
        with patch.object(wsi.emxl, "gh_api", side_effect=self._paged([events, []])):
            result = wsi.find_label_applied_at(1, _RUNG_30, "owner/repo", "tok")
        self.assertIsNone(result)

    def test_case_insensitive_label_match(self):
        when = _NOW - timedelta(days=31)
        events = [_labeled_event("Snooze 30 Days", when)]
        with patch.object(wsi.emxl, "gh_api", side_effect=self._paged([events, []])):
            result = wsi.find_label_applied_at(1, _RUNG_30, "owner/repo", "tok")
        self.assertEqual(result, when)

    def test_ignores_the_legacy_spelling_of_the_rung(self):
        """A hold 30 days label is not the 30-day rung any more, so an event
        naming it dates nothing. An issue whose snooze rests only on such an
        event is left snoozed rather than woken off an event for a label it no
        longer carries."""
        when = _NOW - timedelta(days=31)
        events = [_labeled_event("hold 30 days", when)]
        with patch.object(wsi.emxl, "gh_api", side_effect=self._paged([events, []])):
            result = wsi.find_label_applied_at(1, _RUNG_30, "owner/repo", "tok")
        self.assertIsNone(result)

    def test_ignores_events_for_another_rung(self):
        """Only the rung's own label counts; a different rung's is as
        irrelevant as any other label."""
        when = _NOW - timedelta(days=31)
        events = [_labeled_event("snooze 90 days", when)]
        with patch.object(wsi.emxl, "gh_api", side_effect=self._paged([events, []])):
            result = wsi.find_label_applied_at(1, _RUNG_30, "owner/repo", "tok")
        self.assertIsNone(result)

    def test_returns_most_recent_of_several_matching_events(self):
        older = _NOW - timedelta(days=100)
        newer = _NOW - timedelta(days=31)
        events = [
            _labeled_event("snooze 30 days", older),
            _labeled_event("snooze 30 days", newer),
        ]
        with patch.object(wsi.emxl, "gh_api", side_effect=self._paged([events, []])):
            result = wsi.find_label_applied_at(1, _RUNG_30, "owner/repo", "tok")
        self.assertEqual(result, newer)

    def test_paginates_across_events(self):
        when = _NOW - timedelta(days=31)
        page1 = [{"event": "commented", "created_at": when.strftime("%Y-%m-%dT%H:%M:%SZ")}] * 100
        page2 = [_labeled_event("snooze 30 days", when)]
        with patch.object(wsi.emxl, "gh_api", side_effect=self._paged([page1, page2, []])):
            result = wsi.find_label_applied_at(1, _RUNG_30, "owner/repo", "tok")
        self.assertEqual(result, when)


# ---------------------------------------------------------------------------
# wake_issue tests
# ---------------------------------------------------------------------------


class TestWakeIssue(unittest.TestCase):
    """wake_issue() re-fetches live, then reopens (label-free PATCH) and
    removes only the specific labels it targets (via emxl.remove_labels(),
    patched separately from the raw gh_api calls it wraps)."""

    def test_reopens_via_a_labels_free_patch(self):
        issue = _make_issue(7, ["snooze 30 days", "bug"])
        with patch.object(wsi.emxl, "gh_api", side_effect=[issue, None, None]) as mock_api:
            with patch.object(wsi.emxl, "remove_labels", return_value=True):
                result = wsi.wake_issue(7, "snooze 30 days", "owner/repo", "tok")

        self.assertTrue(result)
        patch_call = mock_api.call_args_list[1]
        self.assertEqual(patch_call[1]["method"], "PATCH")
        self.assertEqual(patch_call[1]["body"], {"state": "open"})

    def test_targets_only_the_snooze_label_when_nothing_else_conflicts(self):
        issue = _make_issue(7, ["snooze 30 days", "p1", "ci"])
        with patch.object(wsi.emxl, "gh_api", side_effect=[issue, None, None]):
            with patch.object(wsi.emxl, "remove_labels", return_value=True) as mock_remove:
                wsi.wake_issue(7, "snooze 30 days", "owner/repo", "tok")

        mock_remove.assert_called_once_with(
            7, ["snooze 30 days"], "owner/repo", "tok", reason="expired-snooze"
        )

    def test_leaves_the_legacy_spelling_alone(self):
        """A hold 30 days label is no longer part of the 30-day rung, so it is not
        the wake's to strip: it is an ordinary label like any other."""
        issue = _make_issue(7, ["snooze 30 days", "hold 30 days", "ci"])
        with patch.object(wsi.emxl, "gh_api", side_effect=[issue, None, None]):
            with patch.object(wsi.emxl, "remove_labels", return_value=True) as mock_remove:
                result = wsi.wake_issue(7, "snooze 30 days", "owner/repo", "tok")

        self.assertTrue(result)
        self.assertEqual(mock_remove.call_args[0][1], ["snooze 30 days"])

    def test_leaves_another_rung_alone(self):
        """Only the woken rung is stripped. A different rung is somebody's
        live snooze, not this wake's business."""
        issue = _make_issue(7, ["snooze 30 days", "snooze 180 days"])
        with patch.object(wsi.emxl, "gh_api", side_effect=[issue, None, None]):
            with patch.object(wsi.emxl, "remove_labels", return_value=True) as mock_remove:
                wsi.wake_issue(7, "snooze 30 days", "owner/repo", "tok")

        self.assertEqual(mock_remove.call_args[0][1], ["snooze 30 days"])

    def test_targets_process_state_labels_alongside_the_snooze_label(self):
        issue = _make_issue(7, ["snooze 90 days", "orchestrate", "changes requested", "ci"])
        with patch.object(wsi.emxl, "gh_api", side_effect=[issue, None, None]):
            with patch.object(wsi.emxl, "remove_labels", return_value=True) as mock_remove:
                wsi.wake_issue(7, "snooze 90 days", "owner/repo", "tok")

        targeted = mock_remove.call_args[0][1]
        self.assertCountEqual(targeted, ["snooze 90 days", "orchestrate", "changes requested"])
        self.assertNotIn("ci", targeted)

    def test_posts_a_comment(self):
        issue = _make_issue(7, ["snooze 30 days"])
        with patch.object(wsi.emxl, "gh_api", side_effect=[issue, None, None]) as mock_api:
            with patch.object(wsi.emxl, "remove_labels", return_value=True):
                wsi.wake_issue(7, "snooze 30 days", "owner/repo", "tok")

        comment_call = mock_api.call_args_list[2]
        self.assertIn("comments", comment_call[0][0])
        self.assertEqual(comment_call[1]["method"], "POST")
        self.assertIn("snooze 30 days", comment_call[1]["body"]["body"])

    def test_comment_notes_other_stripped_labels(self):
        issue = _make_issue(7, ["snooze 30 days", "orchestrating"])
        with patch.object(wsi.emxl, "gh_api", side_effect=[issue, None, None]) as mock_api:
            with patch.object(wsi.emxl, "remove_labels", return_value=True):
                wsi.wake_issue(7, "snooze 30 days", "owner/repo", "tok")

        comment_body = mock_api.call_args_list[2][1]["body"]["body"]
        self.assertIn("orchestrating", comment_body)

    def test_case_insensitive_snooze_label_still_matches(self):
        issue = _make_issue(7, ["Snooze 30 Days"])
        with patch.object(wsi.emxl, "gh_api", side_effect=[issue, None, None]):
            with patch.object(wsi.emxl, "remove_labels", return_value=True) as mock_remove:
                result = wsi.wake_issue(7, "snooze 30 days", "owner/repo", "tok")

        self.assertTrue(result)
        mock_remove.assert_called_once()

    def test_raises_on_non_dict_get_response(self):
        with patch.object(wsi.emxl, "gh_api", return_value=None):
            with self.assertRaises(RuntimeError):
                wsi.wake_issue(7, "snooze 30 days", "owner/repo", "tok")

    # ------------------------------------------------------------------
    # Concurrency guards (PR #837 review)
    # ------------------------------------------------------------------

    def test_returns_false_and_touches_nothing_when_snooze_label_already_gone(self):
        """A live re-fetch showing the snooze label already replaced (e.g. an
        escalation from snooze 30 days to snooze 90 days that landed between
        main()'s list call and this call) is left alone entirely: no reopen,
        no label removal, no comment."""
        issue = _make_issue(7, ["snooze 90 days", "ci"])
        with patch.object(wsi.emxl, "gh_api", side_effect=[issue]) as mock_api:
            with patch.object(wsi.emxl, "remove_labels") as mock_remove:
                result = wsi.wake_issue(7, "snooze 30 days", "owner/repo", "tok")

        self.assertFalse(result)
        self.assertEqual(mock_api.call_count, 1)  # only the re-fetch GET
        mock_remove.assert_not_called()

    def test_reopen_and_strip_are_not_undone_by_a_failed_comment(self):
        issue = _make_issue(7, ["snooze 30 days"])

        def gh_api_side_effect(path, token, method="GET", body=None):
            if method == "POST":
                raise Exception("comment API down")
            return issue if method == "GET" else None

        with patch.object(wsi.emxl, "gh_api", side_effect=gh_api_side_effect):
            with patch.object(wsi.emxl, "remove_labels", return_value=True) as mock_remove:
                result = wsi.wake_issue(7, "snooze 30 days", "owner/repo", "tok")

        # The reopen and label removal both already happened; a failed
        # notification comment does not retroactively fail the wake.
        self.assertTrue(result)
        mock_remove.assert_called_once()


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
            result = wsi.main()
        self.assertEqual(result, 0)

    def test_exit_0_when_repository_missing(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": ""}):
            result = wsi.main()
        self.assertEqual(result, 0)

    def test_wakes_issue_past_its_snooze(self):
        """applied_at 31 days before real "now" clears the 30-day snooze."""
        issue = _make_issue(1, ["snooze 30 days"])
        applied_at = datetime.now(timezone.utc) - timedelta(days=31)

        def fetch_side_effect(repo, token, label):
            return [issue] if label == "snooze 30 days" else []

        with patch.object(wsi, "fetch_issues_with_label", side_effect=fetch_side_effect):
            with patch.object(wsi, "find_label_applied_at", return_value=applied_at):
                with patch.object(wsi, "wake_issue") as mock_wake:
                    result = wsi.main()

        self.assertEqual(result, 0)
        mock_wake.assert_called_once_with(1, "snooze 30 days", "owner/repo", "test-token")

    def test_ignores_an_issue_labeled_with_the_legacy_spelling(self):
        """A hold 30 days label is not a rung any more, so it is not listed, not
        dated, and never wakes anything.

        find_label_applied_at is patched so that "not dated" is asserted rather
        than inferred from the wake that did not happen. Leaving it real would
        also let a regression to the legacy spelling reach the network: main()
        swallows anything the history read raises, so the live request's error
        would be logged and the test would pass regardless.
        """
        issue = _make_issue(1, ["hold 30 days"])
        queried = []

        def fetch_side_effect(repo, token, label):
            queried.append(label)
            return [issue] if label == "hold 30 days" else []

        with patch.object(wsi, "fetch_issues_with_label", side_effect=fetch_side_effect):
            with patch.object(wsi, "find_label_applied_at", return_value=None) as mock_find:
                with patch.object(wsi, "wake_issue") as mock_wake:
                    result = wsi.main()

        self.assertEqual(result, 0)
        self.assertNotIn("hold 30 days", queried)  # not listed
        mock_find.assert_not_called()  # not dated
        mock_wake.assert_not_called()  # never woken

    def test_each_rung_waits_out_its_own_day_count(self):
        """Four days in, a 3-day snooze has elapsed and a 7-day one has not."""
        applied_at = datetime.now(timezone.utc) - timedelta(days=4)

        def fetch_side_effect(repo, token, label):
            if label == "snooze 3 days":
                return [_make_issue(1, [label])]
            if label == "snooze 7 days":
                return [_make_issue(2, [label])]
            return []

        with patch.object(wsi, "fetch_issues_with_label", side_effect=fetch_side_effect):
            with patch.object(wsi, "find_label_applied_at", return_value=applied_at):
                with patch.object(wsi, "wake_issue") as mock_wake:
                    result = wsi.main()

        self.assertEqual(result, 0)
        mock_wake.assert_called_once_with(1, "snooze 3 days", "owner/repo", "test-token")

    def test_queries_every_rung(self):
        queried = []

        def fetch_side_effect(repo, token, label):
            queried.append(label)
            return []

        with patch.object(wsi, "fetch_issues_with_label", side_effect=fetch_side_effect):
            result = wsi.main()

        self.assertEqual(result, 0)
        self.assertEqual(
            queried,
            [wsi.emxl.snooze_label_for_days(days) for days in wsi.emxl.SNOOZE_LADDER_DAYS],
        )

    def test_reads_label_history_for_the_rung(self):
        issue = _make_issue(1, ["snooze 30 days"])

        def fetch_side_effect(repo, token, label):
            return [issue] if label == "snooze 30 days" else []

        with patch.object(wsi, "fetch_issues_with_label", side_effect=fetch_side_effect):
            with patch.object(wsi, "find_label_applied_at", return_value=None) as mock_find:
                result = wsi.main()

        self.assertEqual(result, 0)
        mock_find.assert_called_once_with(1, _RUNG_30, "owner/repo", "test-token")

    def test_fetch_error_for_one_rung_does_not_skip_the_others(self):
        issue = _make_issue(1, ["snooze 90 days"])
        applied_at = datetime.now(timezone.utc) - timedelta(days=91)

        def fetch_side_effect(repo, token, label):
            if label == "snooze 30 days":
                raise Exception("network error")
            return [issue] if label == "snooze 90 days" else []

        with patch.object(wsi, "fetch_issues_with_label", side_effect=fetch_side_effect):
            with patch.object(wsi, "find_label_applied_at", return_value=applied_at):
                with patch.object(wsi, "wake_issue") as mock_wake:
                    result = wsi.main()

        self.assertEqual(result, 0)
        mock_wake.assert_called_once_with(1, "snooze 90 days", "owner/repo", "test-token")

    def test_leaves_issue_within_snooze_period(self):
        """applied_at 5 days before real "now" has not yet cleared the 30-day snooze."""
        issue = _make_issue(1, ["snooze 30 days"])
        applied_at = datetime.now(timezone.utc) - timedelta(days=5)

        def fetch_side_effect(repo, token, label):
            return [issue] if label == "snooze 30 days" else []

        with patch.object(wsi, "fetch_issues_with_label", side_effect=fetch_side_effect):
            with patch.object(wsi, "find_label_applied_at", return_value=applied_at):
                with patch.object(wsi, "wake_issue") as mock_wake:
                    result = wsi.main()

        self.assertEqual(result, 0)
        mock_wake.assert_not_called()

    def test_skips_when_applied_at_unknown(self):
        issue = _make_issue(1, ["snooze 30 days"])

        def fetch_side_effect(repo, token, label):
            return [issue] if label == "snooze 30 days" else []

        with patch.object(wsi, "fetch_issues_with_label", side_effect=fetch_side_effect):
            with patch.object(wsi, "find_label_applied_at", return_value=None):
                with patch.object(wsi, "wake_issue") as mock_wake:
                    result = wsi.main()

        self.assertEqual(result, 0)
        mock_wake.assert_not_called()

    def test_continues_after_fetch_error_for_one_label(self):
        def fetch_side_effect(repo, token, label):
            if label == "snooze 30 days":
                raise Exception("network error")
            return []

        with patch.object(wsi, "fetch_issues_with_label", side_effect=fetch_side_effect):
            result = wsi.main()

        self.assertEqual(result, 0)

    def test_continues_after_wake_error_for_one_issue(self):
        issue = _make_issue(1, ["snooze 30 days"])
        applied_at = _NOW - timedelta(days=31)

        def fetch_side_effect(repo, token, label):
            return [issue] if label == "snooze 30 days" else []

        with patch.object(wsi, "fetch_issues_with_label", side_effect=fetch_side_effect):
            with patch.object(wsi, "find_label_applied_at", return_value=applied_at):
                with patch.object(wsi, "wake_issue", side_effect=Exception("boom")):
                    result = wsi.main()

        self.assertEqual(result, 0)

    def test_no_issues_found_wakes_nothing(self):
        with patch.object(wsi, "fetch_issues_with_label", return_value=[]):
            with patch.object(wsi, "wake_issue") as mock_wake:
                result = wsi.main()

        self.assertEqual(result, 0)
        mock_wake.assert_not_called()

    def test_does_not_count_a_no_op_wake_as_woken(self):
        """wake_issue() returning False (snooze label already gone by the time it
        re-fetched, e.g. an escalation) must not inflate the woken count."""
        issue = _make_issue(1, ["snooze 30 days"])
        applied_at = datetime.now(timezone.utc) - timedelta(days=31)

        def fetch_side_effect(repo, token, label):
            return [issue] if label == "snooze 30 days" else []

        out = io.StringIO()
        with patch.object(wsi, "fetch_issues_with_label", side_effect=fetch_side_effect):
            with patch.object(wsi, "find_label_applied_at", return_value=applied_at):
                with patch.object(wsi, "wake_issue", return_value=False):
                    with redirect_stdout(out):
                        result = wsi.main()

        self.assertEqual(result, 0)
        self.assertIn("Woke 0 issue(s)", out.getvalue())


if __name__ == "__main__":
    unittest.main()
