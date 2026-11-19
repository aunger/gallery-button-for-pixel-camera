#!/usr/bin/env python3
"""Unit tests for github_rest.py."""

import io
import json
import os
import sys
import unittest
import urllib.error
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import github_rest


def _http_error(code: int) -> urllib.error.HTTPError:
    """Build an HTTPError carrying the given status code for retry tests."""
    return urllib.error.HTTPError(url=None, code=code, msg="err", hdrs=None, fp=None)


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
            result = github_rest.gh_api("repos/owner/repo/issues/1", token="tok")
        self.assertEqual(result, payload)

    def test_returns_none_for_empty_body(self):
        """DELETE /labels/:name returns 204 No Content with an empty body."""
        response = self._make_response(b"", status=204)
        with patch("urllib.request.urlopen", return_value=response):
            result = github_rest.gh_api(
                "repos/owner/repo/issues/1/labels/p2", token="tok", method="DELETE"
            )
        self.assertIsNone(result)

    # ------------------------------------------------------------------
    # Transient-error retry tests (issue #749)
    # ------------------------------------------------------------------

    def test_retries_transient_503_then_succeeds(self):
        """A 503 blip is retried and the following success is returned."""
        payload = {"labels": [{"name": "p1"}]}
        response = self._make_response(json.dumps(payload).encode())
        with patch.object(github_rest.time, "sleep") as mock_sleep:
            with patch(
                "urllib.request.urlopen",
                side_effect=[_http_error(503), response],
            ) as mock_urlopen:
                result = github_rest.gh_api("repos/owner/repo/issues/1", token="tok")
        self.assertEqual(result, payload)
        self.assertEqual(mock_urlopen.call_count, 2)
        mock_sleep.assert_called_once()

    def test_retries_429_then_succeeds(self):
        """A 429 rate-limit response is transient and is retried."""
        response = self._make_response(b"")
        with patch.object(github_rest.time, "sleep"):
            with patch(
                "urllib.request.urlopen",
                side_effect=[_http_error(429), response],
            ) as mock_urlopen:
                result = github_rest.gh_api(
                    "repos/owner/repo/issues/1/labels/p2", token="tok", method="DELETE"
                )
        self.assertIsNone(result)
        self.assertEqual(mock_urlopen.call_count, 2)

    def test_retries_url_error_then_succeeds(self):
        """A network-level URLError (e.g. connection reset) is retried."""
        payload = {"ok": True}
        response = self._make_response(json.dumps(payload).encode())
        with patch.object(github_rest.time, "sleep"):
            with patch(
                "urllib.request.urlopen",
                side_effect=[urllib.error.URLError("connection reset"), response],
            ) as mock_urlopen:
                result = github_rest.gh_api("repos/owner/repo/issues/1", token="tok")
        self.assertEqual(result, payload)
        self.assertEqual(mock_urlopen.call_count, 2)

    def test_retries_exhaust_and_raise_last_transient(self):
        """After max_attempts transient failures, the last error propagates."""
        with patch.object(github_rest.time, "sleep") as mock_sleep:
            with patch(
                "urllib.request.urlopen",
                side_effect=[_http_error(503), _http_error(502), _http_error(500)],
            ) as mock_urlopen:
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    github_rest.gh_api("repos/owner/repo/issues/1", token="tok", max_attempts=3)
        self.assertEqual(ctx.exception.code, 500)
        self.assertEqual(mock_urlopen.call_count, 3)
        # One sleep between each pair of attempts: three attempts => two sleeps.
        self.assertEqual(mock_sleep.call_count, 2)

    def test_does_not_retry_404(self):
        """A 404 is benign/real, not transient, so it is raised without retry."""
        with patch.object(github_rest.time, "sleep") as mock_sleep:
            with patch(
                "urllib.request.urlopen",
                side_effect=[_http_error(404), self._make_response(b"{}")],
            ) as mock_urlopen:
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    github_rest.gh_api(
                        "repos/owner/repo/issues/1/labels/p2", token="tok", method="DELETE"
                    )
        self.assertEqual(ctx.exception.code, 404)
        self.assertEqual(mock_urlopen.call_count, 1)
        mock_sleep.assert_not_called()

    def test_does_not_retry_422(self):
        """A 422 validation error is real, not transient, so it is not retried."""
        with patch.object(github_rest.time, "sleep") as mock_sleep:
            with patch("urllib.request.urlopen", side_effect=_http_error(422)) as mock_urlopen:
                with self.assertRaises(urllib.error.HTTPError):
                    github_rest.gh_api("repos/owner/repo/issues/1", token="tok")
        self.assertEqual(mock_urlopen.call_count, 1)
        mock_sleep.assert_not_called()

    def test_backoff_grows_exponentially(self):
        """Each retry waits longer than the last, following the backoff policy."""
        with patch.object(github_rest.time, "sleep") as mock_sleep:
            with patch(
                "urllib.request.urlopen",
                side_effect=[
                    _http_error(503),
                    _http_error(503),
                    _http_error(503),
                    _http_error(503),
                ],
            ):
                with self.assertRaises(urllib.error.HTTPError):
                    github_rest.gh_api("repos/owner/repo/issues/1", token="tok", max_attempts=4)
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        b0 = github_rest.INITIAL_BACKOFF_SECONDS
        m = github_rest.BACKOFF_MULTIPLIER
        self.assertEqual(delays, [b0, b0 * m, b0 * m * m])


# ---------------------------------------------------------------------------
# _is_transient_error tests
# ---------------------------------------------------------------------------


class TestIsTransientError(unittest.TestCase):
    def test_5xx_is_transient(self):
        for code in (500, 502, 503, 504, 599):
            self.assertTrue(github_rest._is_transient_error(_http_error(code)), code)

    def test_429_is_transient(self):
        self.assertTrue(github_rest._is_transient_error(_http_error(429)))

    def test_url_error_is_transient(self):
        self.assertTrue(github_rest._is_transient_error(urllib.error.URLError("boom")))

    def test_404_is_not_transient(self):
        self.assertFalse(github_rest._is_transient_error(_http_error(404)))

    def test_422_is_not_transient(self):
        self.assertFalse(github_rest._is_transient_error(_http_error(422)))

    def test_other_4xx_not_transient(self):
        for code in (400, 401, 403):
            self.assertFalse(github_rest._is_transient_error(_http_error(code)), code)

    def test_non_url_exception_not_transient(self):
        self.assertFalse(github_rest._is_transient_error(ValueError("nope")))


# ---------------------------------------------------------------------------
# fetch_open_pull_requests tests
# ---------------------------------------------------------------------------


def _pull(number: int) -> dict:
    """A pull request object with enough shape for a caller to project from."""
    return {"number": number, "head": {"sha": f"{number:040d}"}, "labels": []}


class TestFetchOpenPullRequests(unittest.TestCase):
    """Tests for the listing helper shared by check_blocking_labels and
    reconcile_issue_labels.

    Both callers inherit the pagination contract asserted here, so it is
    asserted once against the helper rather than once per caller.
    """

    def test_returns_the_raw_objects_so_callers_can_project_them(self):
        page = [_pull(808), _pull(832)]
        with patch.object(github_rest, "gh_api", side_effect=[page]):
            found = github_rest.fetch_open_pull_requests("o/r", "tok")
        self.assertEqual(found, page)

    def test_requests_open_pull_requests_a_full_page_at_a_time(self):
        with patch.object(github_rest, "gh_api", side_effect=[[_pull(808)]]) as gh_api:
            github_rest.fetch_open_pull_requests("o/r", "tok")
        path = gh_api.call_args[0][0]
        self.assertEqual(
            path, f"repos/o/r/pulls?state=open&per_page={github_rest.PULLS_PER_PAGE}&page=1"
        )
        self.assertEqual(gh_api.call_args.kwargs["token"], "tok")

    def test_a_short_first_page_costs_one_call(self):
        with patch.object(github_rest, "gh_api", side_effect=[[_pull(808)]]) as gh_api:
            found = github_rest.fetch_open_pull_requests("o/r", "tok")
        self.assertEqual([pr["number"] for pr in found], [808])
        self.assertEqual(gh_api.call_count, 1)

    def test_paginates_until_a_short_page(self):
        full_page = [_pull(n) for n in range(github_rest.PULLS_PER_PAGE)]
        with patch.object(github_rest, "gh_api", side_effect=[full_page, [_pull(808)]]) as gh_api:
            found = github_rest.fetch_open_pull_requests("o/r", "tok")
        self.assertEqual(len(found), github_rest.PULLS_PER_PAGE + 1)
        self.assertEqual(gh_api.call_count, 2)
        self.assertIn("page=2", gh_api.call_args[0][0])

    def test_paginates_until_an_empty_page_when_the_last_page_is_exactly_full(self):
        full_page = [_pull(n) for n in range(github_rest.PULLS_PER_PAGE)]
        with patch.object(github_rest, "gh_api", side_effect=[full_page, []]) as gh_api:
            found = github_rest.fetch_open_pull_requests("o/r", "tok")
        self.assertEqual(len(found), github_rest.PULLS_PER_PAGE)
        self.assertEqual(gh_api.call_count, 2)

    def test_no_open_pull_requests_yields_an_empty_list(self):
        with patch.object(github_rest, "gh_api", side_effect=[[]]) as gh_api:
            self.assertEqual(github_rest.fetch_open_pull_requests("o/r", "tok"), [])
        self.assertEqual(gh_api.call_count, 1)

    def test_an_api_error_propagates_to_the_caller(self):
        with patch.object(github_rest, "gh_api", side_effect=_http_error(403)):
            with self.assertRaises(urllib.error.HTTPError):
                github_rest.fetch_open_pull_requests("o/r", "tok")


# ---------------------------------------------------------------------------
# remove_labels tests
# ---------------------------------------------------------------------------


class TestRemoveLabels(unittest.TestCase):
    def test_calls_delete_for_each_label(self):
        with patch.object(github_rest, "gh_api") as mock_api:
            result = github_rest.remove_labels(42, ["p2", "p3"], "owner/repo", "tok")

        self.assertEqual(mock_api.call_count, 2)
        paths = [c[0][0] for c in mock_api.call_args_list]
        self.assertTrue(any("p2" in p for p in paths))
        self.assertTrue(any("p3" in p for p in paths))
        self.assertTrue(result)

    def test_uses_delete_method(self):
        with patch.object(github_rest, "gh_api") as mock_api:
            github_rest.remove_labels(1, ["p2"], "owner/repo", "tok")

        self.assertEqual(mock_api.call_args[1]["method"], "DELETE")

    def test_no_calls_when_list_empty(self):
        with patch.object(github_rest, "gh_api") as mock_api:
            result = github_rest.remove_labels(1, [], "owner/repo", "tok")

        mock_api.assert_not_called()
        self.assertTrue(result)

    def test_handles_404_gracefully(self):
        error = urllib.error.HTTPError(url=None, code=404, msg="Not Found", hdrs=None, fp=None)
        with patch.object(github_rest, "gh_api", side_effect=error):
            # Should not raise, and a 404 (already removed) does not count as a failure.
            result = github_rest.remove_labels(1, ["p2"], "owner/repo", "tok")

        self.assertTrue(result)

    def test_continues_after_http_error_but_reports_failure(self):
        error = urllib.error.HTTPError(
            url=None, code=422, msg="Unprocessable Entity", hdrs=None, fp=None
        )
        with patch.object(github_rest, "gh_api", side_effect=error):
            result = github_rest.remove_labels(1, ["p2"], "owner/repo", "tok")

        self.assertFalse(result)

    def test_continues_after_url_error_but_reports_failure(self):
        error = urllib.error.URLError("network error")
        with patch.object(github_rest, "gh_api", side_effect=error):
            result = github_rest.remove_labels(1, ["p2"], "owner/repo", "tok")

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

        with patch.object(github_rest, "gh_api", side_effect=fake_api):
            result = github_rest.remove_labels(1, ["p2", "p3"], "owner/repo", "tok")

        self.assertTrue(any("p3" in p for p in call_paths))
        self.assertFalse(result)

    def test_one_failure_among_several_still_reports_failure(self):
        def fake_api(path, token, method="GET", body=None):
            if "p2" in path:
                raise urllib.error.HTTPError(
                    url=None, code=500, msg="Server Error", hdrs=None, fp=None
                )

        with patch.object(github_rest, "gh_api", side_effect=fake_api):
            result = github_rest.remove_labels(1, ["p1", "p2", "p3"], "owner/repo", "tok")

        self.assertFalse(result)

    def test_url_encodes_label_with_spaces(self):
        call_paths = []

        def fake_api(path, token, method="GET", body=None):
            call_paths.append(path)

        with patch.object(github_rest, "gh_api", side_effect=fake_api):
            result = github_rest.remove_labels(5, ["verification needed"], "owner/repo", "tok")

        self.assertTrue(any("verification%20needed" in p for p in call_paths))
        self.assertTrue(result)

    def test_default_reason_says_conflicting(self):
        with patch.object(github_rest, "gh_api"):
            out = io.StringIO()
            with redirect_stdout(out):
                github_rest.remove_labels(1, ["p2"], "owner/repo", "tok")
        self.assertIn("Removed conflicting label 'p2' from #1.", out.getvalue())

    def test_custom_reason_replaces_conflicting_in_the_log_line(self):
        # scripts/ci/labels/label_by_files.py and
        # scripts/ci/labels/audit_labels_by_files.py reuse this function for a
        # removal where nothing conflicts, so they pass reason="unjustified"
        # to keep the log line honest.
        with patch.object(github_rest, "gh_api"):
            out = io.StringIO()
            with redirect_stdout(out):
                github_rest.remove_labels(1, ["agents"], "owner/repo", "tok", reason="unjustified")
        self.assertIn("Removed unjustified label 'agents' from #1.", out.getvalue())
        self.assertNotIn("conflicting", out.getvalue())


if __name__ == "__main__":
    unittest.main()
