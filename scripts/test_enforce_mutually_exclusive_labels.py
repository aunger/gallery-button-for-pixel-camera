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
        result = emxl.find_conflicting_set("needs verification")
        self.assertIsNotNone(result)
        self.assertIn("needs verification", result)
        self.assertIn("verified", result)

    def test_verified_label_found(self):
        result = emxl.find_conflicting_set("verified")
        self.assertIsNotNone(result)
        self.assertIn("needs verification", result)

    def test_change_requested_found(self):
        result = emxl.find_conflicting_set("change requested")
        self.assertIsNotNone(result)
        self.assertIn("change requested", result)
        self.assertIn("change done", result)

    def test_change_done_found(self):
        result = emxl.find_conflicting_set("change done")
        self.assertIsNotNone(result)
        self.assertIn("change requested", result)

    def test_for_ai_to_do_found(self):
        result = emxl.find_conflicting_set("for ai to do")
        self.assertIsNotNone(result)
        self.assertIn("for ai to do", result)
        self.assertIn("orchestrating", result)

    def test_orchestrating_found(self):
        result = emxl.find_conflicting_set("orchestrating")
        self.assertIsNotNone(result)
        self.assertIn("for ai to do", result)

    def test_unknown_label_returns_none(self):
        self.assertIsNone(emxl.find_conflicting_set("bug"))
        self.assertIsNone(emxl.find_conflicting_set("ci"))
        self.assertIsNone(emxl.find_conflicting_set("enhancement"))

    def test_empty_string_returns_none(self):
        self.assertIsNone(emxl.find_conflicting_set(""))


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
        verification_set = frozenset({"needs verification", "verified"})
        result = emxl.labels_to_remove(
            "verified",
            ["needs verification", "ci", "p1", "bug"],
            verification_set,
        )
        self.assertEqual(result, ["needs verification"])


# ---------------------------------------------------------------------------
# remove_labels tests
# ---------------------------------------------------------------------------


class TestRemoveLabels(unittest.TestCase):
    def test_calls_delete_for_each_label(self):
        with patch.object(emxl, "gh_api") as mock_api:
            emxl.remove_labels(42, ["p2", "p3"], "owner/repo", "tok")

        self.assertEqual(mock_api.call_count, 2)
        paths = [c[0][0] for c in mock_api.call_args_list]
        self.assertTrue(any("p2" in p for p in paths))
        self.assertTrue(any("p3" in p for p in paths))

    def test_uses_delete_method(self):
        with patch.object(emxl, "gh_api") as mock_api:
            emxl.remove_labels(1, ["p2"], "owner/repo", "tok")

        self.assertEqual(mock_api.call_args[1]["method"], "DELETE")

    def test_no_calls_when_list_empty(self):
        with patch.object(emxl, "gh_api") as mock_api:
            emxl.remove_labels(1, [], "owner/repo", "tok")

        mock_api.assert_not_called()

    def test_handles_404_gracefully(self):
        error = urllib.error.HTTPError(url=None, code=404, msg="Not Found", hdrs=None, fp=None)
        with patch.object(emxl, "gh_api", side_effect=error):
            # Should not raise.
            emxl.remove_labels(1, ["p2"], "owner/repo", "tok")

    def test_handles_other_http_error_gracefully(self):
        error = urllib.error.HTTPError(
            url=None, code=422, msg="Unprocessable Entity", hdrs=None, fp=None
        )
        with patch.object(emxl, "gh_api", side_effect=error):
            emxl.remove_labels(1, ["p2"], "owner/repo", "tok")

    def test_handles_url_error_gracefully(self):
        error = urllib.error.URLError("network error")
        with patch.object(emxl, "gh_api", side_effect=error):
            emxl.remove_labels(1, ["p2"], "owner/repo", "tok")

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
            emxl.remove_labels(1, ["p2", "p3"], "owner/repo", "tok")

        self.assertTrue(any("p3" in p for p in call_paths))

    def test_url_encodes_label_with_spaces(self):
        call_paths = []

        def fake_api(path, token, method="GET", body=None):
            call_paths.append(path)

        with patch.object(emxl, "gh_api", side_effect=fake_api):
            emxl.remove_labels(5, ["needs verification"], "owner/repo", "tok")

        self.assertTrue(any("needs%20verification" in p for p in call_paths))


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

    def test_exit_0_when_issue_number_non_integer(self):
        with patch.dict(os.environ, {"ISSUE_NUMBER": "abc"}):
            result = emxl.main()
        self.assertEqual(result, 0)

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
                    self._make_issue_response(["needs verification", "ci"]),
                    None,  # DELETE needs verification
                ],
            ) as mock_api:
                result = emxl.main()

        self.assertEqual(result, 0)
        delete_call = mock_api.call_args_list[1]
        self.assertIn("needs%20verification", delete_call[0][0])

    def test_removes_for_change_set(self):
        with patch.dict(os.environ, {"ADDED_LABEL": "change done", "ISSUE_NUMBER": "7"}):
            with patch.object(
                emxl,
                "gh_api",
                side_effect=[
                    self._make_issue_response(["change requested", "bug"]),
                    None,
                ],
            ) as mock_api:
                result = emxl.main()

        self.assertEqual(result, 0)
        delete_call = mock_api.call_args_list[1]
        self.assertIn("change%20requested", delete_call[0][0])

    def test_removes_for_ai_workflow_set(self):
        with patch.dict(os.environ, {"ADDED_LABEL": "orchestrating", "ISSUE_NUMBER": "3"}):
            with patch.object(
                emxl,
                "gh_api",
                side_effect=[
                    self._make_issue_response(["for ai to do", "ci"]),
                    None,
                ],
            ) as mock_api:
                result = emxl.main()

        self.assertEqual(result, 0)
        delete_call = mock_api.call_args_list[1]
        self.assertIn("for%20ai%20to%20do", delete_call[0][0])

    def test_exit_0_when_fetch_raises(self):
        with patch.object(emxl, "gh_api", side_effect=Exception("network error")):
            result = emxl.main()
        self.assertEqual(result, 0)

    def test_exit_0_when_fetch_returns_non_dict(self):
        # gh_api returns None for an empty body; main() must not crash on it.
        with patch.object(emxl, "gh_api") as mock_api:
            mock_api.return_value = None
            result = emxl.main()
        self.assertEqual(result, 0)
        # Only the GET was attempted; no DELETE follows a bad response.
        self.assertEqual(mock_api.call_count, 1)


if __name__ == "__main__":
    unittest.main()
