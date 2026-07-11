#!/usr/bin/env python3
"""Unit tests for enforce_mutually_exclusive_labels.py."""

import json
import os
import sys
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import enforce_mutually_exclusive_labels as emxl  # noqa: E402


# ---------------------------------------------------------------------------
# gh_api tests
# ---------------------------------------------------------------------------


class TestGhApi(unittest.TestCase):
    """Tests for the gh_api helper, exercised without mocking the function itself."""

    def _make_response(self, body: bytes, status: int = 200) -> MagicMock:
        resp = MagicMock()
        resp.read.return_value = body
        resp.status = status
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_returns_parsed_json_for_non_empty_body(self):
        payload = {"labels": [{"name": "p1"}]}
        response = self._make_response(json.dumps(payload).encode())
        with patch("urllib.request.urlopen", return_value=response):
            result = emxl.gh_api("repos/owner/repo/issues/1", token="tok")
        self.assertEqual(result, payload)

    def test_returns_none_for_empty_body(self):
        """DELETE /labels/:name returns 204 No Content with an empty body."""
        response = self._make_response(b"", status=204)
        with patch("urllib.request.urlopen", return_value=response):
            result = emxl.gh_api(
                "repos/owner/repo/issues/1/labels/p2", token="tok", method="DELETE"
            )
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# find_conflicting_set tests
# ---------------------------------------------------------------------------


class TestFindConflictingSet(unittest.TestCase):
    def test_priority_label_found(self):
        result = emxl.find_conflicting_set("p1")
        self.assertIsNotNone(result)
        self.assertIn("p1", result)
        self.assertIn("p2", result)
        self.assertIn("p3", result)

    def test_priority_label_case_insensitive(self):
        self.assertIsNotNone(emxl.find_conflicting_set("P2"))
        self.assertIsNotNone(emxl.find_conflicting_set("P3"))

    def test_verification_labels_found(self):
        result = emxl.find_conflicting_set("verification needed")
        self.assertIsNotNone(result)
        self.assertIn("verification needed", result)
        self.assertIn("verified", result)

    def test_verified_label_found(self):
        result = emxl.find_conflicting_set("verified")
        self.assertIsNotNone(result)
        self.assertIn("verification needed", result)

    def test_changes_requested_found(self):
        result = emxl.find_conflicting_set("changes requested")
        self.assertIsNotNone(result)
        self.assertIn("changes requested", result)
        self.assertIn("changes done", result)

    def test_changes_done_found(self):
        result = emxl.find_conflicting_set("changes done")
        self.assertIsNotNone(result)
        self.assertIn("changes requested", result)

    def test_orchestrate_found(self):
        result = emxl.find_conflicting_set("orchestrate")
        self.assertIsNotNone(result)
        self.assertIn("orchestrate", result)
        self.assertIn("orchestrating", result)

    def test_orchestrating_found(self):
        result = emxl.find_conflicting_set("orchestrating")
        self.assertIsNotNone(result)
        self.assertIn("orchestrate", result)

    def test_unknown_label_returns_none(self):
        self.assertIsNone(emxl.find_conflicting_set("bug"))
        self.assertIsNone(emxl.find_conflicting_set("ci"))
        self.assertIsNone(emxl.find_conflicting_set("enhancement"))

    def test_empty_string_returns_none(self):
        self.assertIsNone(emxl.find_conflicting_set(""))


# ---------------------------------------------------------------------------
# find_conflicting_prefix tests
# ---------------------------------------------------------------------------


class TestFindConflictingPrefix(unittest.TestCase):
    def test_c_a_label_returns_prefix(self):
        self.assertEqual(emxl.find_conflicting_prefix("c-a-sonnet"), "c-a-")

    def test_c_a_opus_returns_prefix(self):
        self.assertEqual(emxl.find_conflicting_prefix("c-a-opus"), "c-a-")

    def test_c_r_label_returns_prefix(self):
        self.assertEqual(emxl.find_conflicting_prefix("c-r-haiku"), "c-r-")

    def test_c_r_opus_returns_prefix(self):
        self.assertEqual(emxl.find_conflicting_prefix("c-r-opus"), "c-r-")

    def test_case_insensitive_c_a(self):
        self.assertEqual(emxl.find_conflicting_prefix("C-A-Opus"), "c-a-")

    def test_case_insensitive_c_r(self):
        self.assertEqual(emxl.find_conflicting_prefix("C-R-Haiku"), "c-r-")

    def test_test_failure_label_returns_prefix(self):
        self.assertEqual(emxl.find_conflicting_prefix("test-failure"), "test-failure")

    def test_test_failure_archive_label_returns_prefix(self):
        self.assertEqual(emxl.find_conflicting_prefix("test-failure-archive"), "test-failure")

    def test_case_insensitive_test_failure(self):
        self.assertEqual(emxl.find_conflicting_prefix("Test-Failure-Archive"), "test-failure")

    def test_unrelated_label_returns_none(self):
        self.assertIsNone(emxl.find_conflicting_prefix("ci"))
        self.assertIsNone(emxl.find_conflicting_prefix("p1"))
        self.assertIsNone(emxl.find_conflicting_prefix("bug"))
        self.assertIsNone(emxl.find_conflicting_prefix("testing"))

    def test_empty_string_returns_none(self):
        self.assertIsNone(emxl.find_conflicting_prefix(""))

    def test_partial_prefix_not_matched(self):
        # "c-a" without trailing dash should not match "c-a-"
        self.assertIsNone(emxl.find_conflicting_prefix("c-a"))
        self.assertIsNone(emxl.find_conflicting_prefix("c-r"))


# ---------------------------------------------------------------------------
# labels_to_remove_by_prefix tests
# ---------------------------------------------------------------------------


class TestLabelsByPrefix(unittest.TestCase):
    def test_removes_conflicting_c_a_label(self):
        result = emxl.labels_to_remove_by_prefix("c-a-opus", ["c-a-sonnet", "ci"], "c-a-")
        self.assertEqual(result, ["c-a-sonnet"])

    def test_does_not_remove_added_label_itself(self):
        result = emxl.labels_to_remove_by_prefix("c-a-opus", ["c-a-opus", "c-a-sonnet"], "c-a-")
        self.assertNotIn("c-a-opus", result)
        self.assertIn("c-a-sonnet", result)

    def test_removes_conflicting_c_r_label(self):
        result = emxl.labels_to_remove_by_prefix("c-r-haiku", ["c-r-opus", "bug"], "c-r-")
        self.assertEqual(result, ["c-r-opus"])

    def test_returns_empty_when_no_prefix_conflicts(self):
        result = emxl.labels_to_remove_by_prefix("c-a-sonnet", ["ci", "p1", "c-r-haiku"], "c-a-")
        self.assertEqual(result, [])

    def test_returns_empty_when_current_labels_empty(self):
        result = emxl.labels_to_remove_by_prefix("c-a-sonnet", [], "c-a-")
        self.assertEqual(result, [])

    def test_case_insensitive_added_label(self):
        """C-A-Opus should be treated as c-a-opus and not remove itself."""
        result = emxl.labels_to_remove_by_prefix("C-A-Opus", ["c-a-opus", "c-a-sonnet"], "c-a-")
        self.assertNotIn("c-a-opus", result)
        self.assertIn("c-a-sonnet", result)

    def test_case_insensitive_current_label(self):
        """Current label C-A-Sonnet should be matched and returned."""
        result = emxl.labels_to_remove_by_prefix("c-a-opus", ["C-A-Sonnet", "ci"], "c-a-")
        self.assertEqual(result, ["C-A-Sonnet"])

    def test_does_not_remove_labels_from_other_prefix(self):
        """c-r-* labels must not be removed when enforcing c-a-*."""
        result = emxl.labels_to_remove_by_prefix(
            "c-a-sonnet", ["c-a-haiku", "c-r-opus", "ci"], "c-a-"
        )
        self.assertIn("c-a-haiku", result)
        self.assertNotIn("c-r-opus", result)

    def test_removes_test_failure_when_archive_added(self):
        result = emxl.labels_to_remove_by_prefix(
            "test-failure-archive", ["test-failure", "testing"], "test-failure"
        )
        self.assertEqual(result, ["test-failure"])

    def test_removes_test_failure_archive_when_test_failure_added(self):
        result = emxl.labels_to_remove_by_prefix(
            "test-failure", ["test-failure-archive", "testing"], "test-failure"
        )
        self.assertEqual(result, ["test-failure-archive"])

    def test_test_failure_prefix_does_not_touch_testing_label(self):
        """The 'testing' label shares no real prefix with 'test-failure' and must survive."""
        result = emxl.labels_to_remove_by_prefix(
            "test-failure", ["testing", "ci"], "test-failure"
        )
        self.assertEqual(result, [])
        self.assertNotIn("ci", result)


# ---------------------------------------------------------------------------
# labels_to_remove tests
# ---------------------------------------------------------------------------


class TestLabelsToRemove(unittest.TestCase):
    def setUp(self):
        self.priority_set = frozenset({"p1", "p2", "p3"})

    def test_returns_conflicting_label(self):
        result = emxl.labels_to_remove("p1", ["p2", "ci"], self.priority_set)
        self.assertEqual(result, ["p2"])

    def test_does_not_return_added_label_itself(self):
        result = emxl.labels_to_remove("p1", ["p1", "p2"], self.priority_set)
        self.assertNotIn("p1", result)
        self.assertIn("p2", result)

    def test_returns_multiple_conflicts(self):
        result = emxl.labels_to_remove("p1", ["p2", "p3", "ci"], self.priority_set)
        self.assertIn("p2", result)
        self.assertIn("p3", result)
        self.assertNotIn("ci", result)

    def test_returns_empty_when_no_conflicts(self):
        result = emxl.labels_to_remove("p1", ["ci", "bug"], self.priority_set)
        self.assertEqual(result, [])

    def test_returns_empty_when_current_labels_empty(self):
        result = emxl.labels_to_remove("p1", [], self.priority_set)
        self.assertEqual(result, [])

    def test_case_insensitive_matching_added_label(self):
        """Added label P1 should still exclude p1 from removal (same label)."""
        result = emxl.labels_to_remove("P1", ["p2", "p1"], self.priority_set)
        self.assertIn("p2", result)
        self.assertNotIn("p1", result)

    def test_case_insensitive_matching_current_label(self):
        """Current label P2 should be treated as p2 and returned."""
        result = emxl.labels_to_remove("p1", ["P2", "ci"], self.priority_set)
        self.assertEqual(result, ["P2"])

    def test_non_set_labels_are_not_returned(self):
        verification_set = frozenset({"verification needed", "verified"})
        result = emxl.labels_to_remove(
            "verified",
            ["verification needed", "ci", "p1", "bug"],
            verification_set,
        )
        self.assertEqual(result, ["verification needed"])


# ---------------------------------------------------------------------------
# remove_labels tests
# ---------------------------------------------------------------------------


class TestRemoveLabels(unittest.TestCase):
    def test_calls_delete_for_each_label(self):
        with patch.object(emxl, "gh_api") as mock_api:
            result = emxl.remove_labels(42, ["p2", "p3"], "owner/repo", "tok")

        self.assertEqual(mock_api.call_count, 2)
        paths = [c[0][0] for c in mock_api.call_args_list]
        self.assertTrue(any("p2" in p for p in paths))
        self.assertTrue(any("p3" in p for p in paths))
        self.assertTrue(result)

    def test_uses_delete_method(self):
        with patch.object(emxl, "gh_api") as mock_api:
            emxl.remove_labels(1, ["p2"], "owner/repo", "tok")

        self.assertEqual(mock_api.call_args[1]["method"], "DELETE")

    def test_no_calls_when_list_empty(self):
        with patch.object(emxl, "gh_api") as mock_api:
            result = emxl.remove_labels(1, [], "owner/repo", "tok")

        mock_api.assert_not_called()
        self.assertTrue(result)

    def test_handles_404_gracefully(self):
        error = urllib.error.HTTPError(url=None, code=404, msg="Not Found", hdrs=None, fp=None)
        with patch.object(emxl, "gh_api", side_effect=error):
            # Should not raise, and a 404 (already removed) does not count as a failure.
            result = emxl.remove_labels(1, ["p2"], "owner/repo", "tok")

        self.assertTrue(result)

    def test_continues_after_http_error_but_reports_failure(self):
        error = urllib.error.HTTPError(
            url=None, code=422, msg="Unprocessable Entity", hdrs=None, fp=None
        )
        with patch.object(emxl, "gh_api", side_effect=error):
            result = emxl.remove_labels(1, ["p2"], "owner/repo", "tok")

        self.assertFalse(result)

    def test_continues_after_url_error_but_reports_failure(self):
        error = urllib.error.URLError("network error")
        with patch.object(emxl, "gh_api", side_effect=error):
            result = emxl.remove_labels(1, ["p2"], "owner/repo", "tok")

        self.assertFalse(result)

    def test_continues_after_one_failure(self):
        """An error on the first label does not prevent the second from being removed."""
        call_paths = []

        def fake_api(path, token, method="GET", body=None):
            call_paths.append(path)
            if "p2" in path:
                raise urllib.error.HTTPError(
                    url=None, code=500, msg="Server Error", hdrs=None, fp=None
                )

        with patch.object(emxl, "gh_api", side_effect=fake_api):
            result = emxl.remove_labels(1, ["p2", "p3"], "owner/repo", "tok")

        self.assertTrue(any("p3" in p for p in call_paths))
        self.assertFalse(result)

    def test_one_failure_among_several_still_reports_failure(self):
        def fake_api(path, token, method="GET", body=None):
            if "p2" in path:
                raise urllib.error.HTTPError(
                    url=None, code=500, msg="Server Error", hdrs=None, fp=None
                )

        with patch.object(emxl, "gh_api", side_effect=fake_api):
            result = emxl.remove_labels(1, ["p1", "p2", "p3"], "owner/repo", "tok")

        self.assertFalse(result)

    def test_url_encodes_label_with_spaces(self):
        call_paths = []

        def fake_api(path, token, method="GET", body=None):
            call_paths.append(path)

        with patch.object(emxl, "gh_api", side_effect=fake_api):
            result = emxl.remove_labels(5, ["verification needed"], "owner/repo", "tok")

        self.assertTrue(any("verification%20needed" in p for p in call_paths))
        self.assertTrue(result)


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
                "ADDED_LABEL": "p1",
            },
        )
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()

    def _make_issue_response(self, labels: list[str]) -> dict:
        return {"labels": [{"name": lbl} for lbl in labels]}

    def test_exit_0_when_token_missing(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
            result = emxl.main()
        self.assertEqual(result, 0)

    def test_exit_0_when_repository_missing(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": ""}):
            result = emxl.main()
        self.assertEqual(result, 0)

    def test_exit_0_when_issue_number_missing(self):
        with patch.dict(os.environ, {"ISSUE_NUMBER": ""}):
            result = emxl.main()
        self.assertEqual(result, 0)

    def test_exit_0_when_added_label_missing(self):
        with patch.dict(os.environ, {"ADDED_LABEL": ""}):
            result = emxl.main()
        self.assertEqual(result, 0)

    def test_exit_1_when_issue_number_non_integer(self):
        with patch.dict(os.environ, {"ISSUE_NUMBER": "abc"}):
            result = emxl.main()
        self.assertEqual(result, 1)

    def test_exit_0_when_label_not_in_any_set(self):
        with patch.dict(os.environ, {"ADDED_LABEL": "bug"}):
            with patch.object(emxl, "gh_api") as mock_api:
                result = emxl.main()
        self.assertEqual(result, 0)
        mock_api.assert_not_called()

    def test_removes_conflicting_priority_label(self):
        with patch.dict(os.environ, {"ADDED_LABEL": "p1"}):
            with patch.object(
                emxl,
                "gh_api",
                side_effect=[
                    self._make_issue_response(["p2", "ci"]),
                    None,  # DELETE p2
                ],
            ) as mock_api:
                result = emxl.main()

        self.assertEqual(result, 0)
        # The second call should be a DELETE for p2
        delete_call = mock_api.call_args_list[1]
        self.assertIn("p2", delete_call[0][0])
        self.assertEqual(delete_call[1]["method"], "DELETE")

    def test_does_not_remove_when_no_conflicts(self):
        with patch.dict(os.environ, {"ADDED_LABEL": "p1"}):
            with patch.object(
                emxl,
                "gh_api",
                side_effect=[self._make_issue_response(["ci", "bug"])],
            ) as mock_api:
                result = emxl.main()

        self.assertEqual(result, 0)
        # Only the GET call to fetch the issue should be made
        self.assertEqual(mock_api.call_count, 1)

    def test_removes_for_verification_set(self):
        with patch.dict(os.environ, {"ADDED_LABEL": "verified", "ISSUE_NUMBER": "10"}):
            with patch.object(
                emxl,
                "gh_api",
                side_effect=[
                    self._make_issue_response(["verification needed", "ci"]),
                    None,  # DELETE verification needed
                ],
            ) as mock_api:
                result = emxl.main()

        self.assertEqual(result, 0)
        delete_call = mock_api.call_args_list[1]
        self.assertIn("verification%20needed", delete_call[0][0])

    def test_removes_for_change_set(self):
        with patch.dict(os.environ, {"ADDED_LABEL": "changes done", "ISSUE_NUMBER": "7"}):
            with patch.object(
                emxl,
                "gh_api",
                side_effect=[
                    self._make_issue_response(["changes requested", "bug"]),
                    None,
                ],
            ) as mock_api:
                result = emxl.main()

        self.assertEqual(result, 0)
        delete_call = mock_api.call_args_list[1]
        self.assertIn("changes%20requested", delete_call[0][0])

    def test_removes_orchestrate_workflow_set(self):
        with patch.dict(os.environ, {"ADDED_LABEL": "orchestrating", "ISSUE_NUMBER": "3"}):
            with patch.object(
                emxl,
                "gh_api",
                side_effect=[
                    self._make_issue_response(["orchestrate", "ci"]),
                    None,
                ],
            ) as mock_api:
                result = emxl.main()

        self.assertEqual(result, 0)
        delete_call = mock_api.call_args_list[1]
        self.assertIn("orchestrate", delete_call[0][0])

    def test_exit_1_when_fetch_raises(self):
        with patch.object(emxl, "gh_api", side_effect=Exception("network error")):
            result = emxl.main()
        self.assertEqual(result, 1)

    def test_exit_1_when_fetch_returns_non_dict(self):
        # gh_api returns None for an empty body; main() must not crash on it.
        with patch.object(emxl, "gh_api") as mock_api:
            mock_api.return_value = None
            result = emxl.main()
        self.assertEqual(result, 1)
        # Only the GET was attempted; no DELETE follows a bad response.
        self.assertEqual(mock_api.call_count, 1)

    def test_exit_1_when_remove_labels_fails(self):
        error = urllib.error.HTTPError(url=None, code=500, msg="Server Error", hdrs=None, fp=None)
        with patch.dict(os.environ, {"ADDED_LABEL": "p1"}):
            with patch.object(
                emxl,
                "gh_api",
                side_effect=[
                    self._make_issue_response(["p2", "ci"]),
                    error,  # DELETE p2 fails
                ],
            ):
                result = emxl.main()

        self.assertEqual(result, 1)

    def test_exit_0_when_removal_404_is_benign(self):
        error = urllib.error.HTTPError(url=None, code=404, msg="Not Found", hdrs=None, fp=None)
        with patch.dict(os.environ, {"ADDED_LABEL": "p1"}):
            with patch.object(
                emxl,
                "gh_api",
                side_effect=[
                    self._make_issue_response(["p2", "ci"]),
                    error,  # DELETE p2 races with another process removing it first
                ],
            ):
                result = emxl.main()

        self.assertEqual(result, 0)

    # ------------------------------------------------------------------
    # Prefix group tests
    # ------------------------------------------------------------------

    def test_c_a_opus_removes_c_a_sonnet(self):
        """Adding c-a-opus when c-a-sonnet is present removes c-a-sonnet."""
        with patch.dict(os.environ, {"ADDED_LABEL": "c-a-opus"}):
            with patch.object(
                emxl,
                "gh_api",
                side_effect=[
                    self._make_issue_response(["c-a-sonnet", "ci"]),
                    None,  # DELETE c-a-sonnet
                ],
            ) as mock_api:
                result = emxl.main()

        self.assertEqual(result, 0)
        delete_call = mock_api.call_args_list[1]
        self.assertIn("c-a-sonnet", delete_call[0][0])
        self.assertEqual(delete_call[1]["method"], "DELETE")

    def test_c_r_haiku_removes_c_r_opus(self):
        """Adding c-r-haiku when c-r-opus is present removes c-r-opus."""
        with patch.dict(os.environ, {"ADDED_LABEL": "c-r-haiku"}):
            with patch.object(
                emxl,
                "gh_api",
                side_effect=[
                    self._make_issue_response(["c-r-opus", "bug"]),
                    None,  # DELETE c-r-opus
                ],
            ) as mock_api:
                result = emxl.main()

        self.assertEqual(result, 0)
        delete_call = mock_api.call_args_list[1]
        self.assertIn("c-r-opus", delete_call[0][0])
        self.assertEqual(delete_call[1]["method"], "DELETE")

    def test_c_a_sonnet_no_conflict_does_nothing(self):
        """Adding c-a-sonnet when no other c-a-* label is present does nothing."""
        with patch.dict(os.environ, {"ADDED_LABEL": "c-a-sonnet"}):
            with patch.object(
                emxl,
                "gh_api",
                side_effect=[self._make_issue_response(["ci", "c-r-haiku"])],
            ) as mock_api:
                result = emxl.main()

        self.assertEqual(result, 0)
        # Only the GET should have been called.
        self.assertEqual(mock_api.call_count, 1)

    def test_unrelated_label_unaffected_by_prefix_groups(self):
        """Adding 'ci' should not trigger any prefix-group enforcement."""
        with patch.dict(os.environ, {"ADDED_LABEL": "ci"}):
            with patch.object(emxl, "gh_api") as mock_api:
                result = emxl.main()

        self.assertEqual(result, 0)
        mock_api.assert_not_called()

    def test_c_a_prefix_case_insensitive(self):
        """C-A-Opus is treated as c-a-opus and removes c-a-sonnet."""
        with patch.dict(os.environ, {"ADDED_LABEL": "C-A-Opus"}):
            with patch.object(
                emxl,
                "gh_api",
                side_effect=[
                    self._make_issue_response(["c-a-sonnet", "ci"]),
                    None,  # DELETE c-a-sonnet
                ],
            ) as mock_api:
                result = emxl.main()

        self.assertEqual(result, 0)
        delete_call = mock_api.call_args_list[1]
        self.assertIn("c-a-sonnet", delete_call[0][0])

    def test_c_r_prefix_does_not_remove_c_a_labels(self):
        """Enforcing c-r-* must not touch c-a-* labels."""
        with patch.dict(os.environ, {"ADDED_LABEL": "c-r-sonnet"}):
            with patch.object(
                emxl,
                "gh_api",
                side_effect=[
                    self._make_issue_response(["c-r-haiku", "c-a-opus", "ci"]),
                    None,  # DELETE c-r-haiku only
                ],
            ) as mock_api:
                result = emxl.main()

        self.assertEqual(result, 0)
        # Exactly two calls: one GET, one DELETE (for c-r-haiku only)
        self.assertEqual(mock_api.call_count, 2)
        delete_call = mock_api.call_args_list[1]
        self.assertIn("c-r-haiku", delete_call[0][0])
        # c-a-opus must not appear in any DELETE call
        delete_paths = [c[0][0] for c in mock_api.call_args_list[1:]]
        self.assertFalse(any("c-a-opus" in p for p in delete_paths))

    def test_test_failure_archive_removes_test_failure(self):
        """Adding test-failure-archive when test-failure is present removes test-failure."""
        with patch.dict(os.environ, {"ADDED_LABEL": "test-failure-archive"}):
            with patch.object(
                emxl,
                "gh_api",
                side_effect=[
                    self._make_issue_response(["test-failure", "testing"]),
                    None,  # DELETE test-failure
                ],
            ) as mock_api:
                result = emxl.main()

        self.assertEqual(result, 0)
        delete_call = mock_api.call_args_list[1]
        self.assertIn("test-failure", delete_call[0][0])
        self.assertEqual(delete_call[1]["method"], "DELETE")

    def test_test_failure_removes_test_failure_archive(self):
        """Adding test-failure when test-failure-archive is present removes test-failure-archive."""
        with patch.dict(os.environ, {"ADDED_LABEL": "test-failure"}):
            with patch.object(
                emxl,
                "gh_api",
                side_effect=[
                    self._make_issue_response(["test-failure-archive", "testing"]),
                    None,  # DELETE test-failure-archive
                ],
            ) as mock_api:
                result = emxl.main()

        self.assertEqual(result, 0)
        delete_call = mock_api.call_args_list[1]
        self.assertIn("test-failure-archive", delete_call[0][0])
        self.assertEqual(delete_call[1]["method"], "DELETE")

    def test_test_failure_prefix_does_not_remove_testing_label(self):
        """Enforcing the test-failure group must not touch the unrelated 'testing' label."""
        with patch.dict(os.environ, {"ADDED_LABEL": "test-failure"}):
            with patch.object(
                emxl,
                "gh_api",
                side_effect=[self._make_issue_response(["testing", "ci"])],
            ) as mock_api:
                result = emxl.main()

        self.assertEqual(result, 0)
        # Only the GET call to fetch the issue should be made; no conflicts found.
        self.assertEqual(mock_api.call_count, 1)


if __name__ == "__main__":
    unittest.main()
