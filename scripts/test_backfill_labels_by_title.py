#!/usr/bin/env python3
"""Unit tests for backfill_labels_by_title.py."""

import json
import os
import sys
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import backfill_labels_by_title as blt  # noqa: E402


def _issue(number: int, title: str, labels: list[str]) -> dict:
    return {"number": number, "title": title, "labels": [{"name": lbl} for lbl in labels]}


# ---------------------------------------------------------------------------
# TRACKED_LABELS
# ---------------------------------------------------------------------------


class TestTrackedLabels(unittest.TestCase):
    def test_includes_product(self):
        self.assertIn("product", blt.TRACKED_LABELS)

    def test_includes_every_rule_label(self):
        self.assertIn("ci", blt.TRACKED_LABELS)
        self.assertIn("agents", blt.TRACKED_LABELS)
        self.assertIn("testing", blt.TRACKED_LABELS)

    def test_has_no_extra_labels(self):
        self.assertEqual(blt.TRACKED_LABELS, frozenset({"product", "ci", "agents", "testing"}))


# ---------------------------------------------------------------------------
# fetch_all_issues
# ---------------------------------------------------------------------------


class TestFetchAllIssues(unittest.TestCase):
    def test_paginates_until_empty_page(self):
        # A full page (100 items) is followed by a short page (1 item) and
        # then the terminating empty page, mirroring the pagination
        # convention in archive_stale_test_failures.py: stop only on an
        # empty batch, not merely a short one.
        page1 = [_issue(i, f"title {i}", []) for i in range(100)]
        page2 = [_issue(100, "title 100", [])]
        with patch.object(blt.label_by_title, "gh_api", side_effect=[page1, page2, []]) as mock_api:
            result = blt.fetch_all_issues("owner/repo", "tok")
        self.assertEqual(len(result), 101)
        self.assertEqual(mock_api.call_count, 3)

    def test_stops_on_empty_batch(self):
        with patch.object(blt.label_by_title, "gh_api", side_effect=[[], []]) as mock_api:
            result = blt.fetch_all_issues("owner/repo", "tok")
        self.assertEqual(result, [])
        mock_api.assert_called_once()

    def test_uses_state_all(self):
        with patch.object(blt.label_by_title, "gh_api", return_value=[]) as mock_api:
            blt.fetch_all_issues("owner/repo", "tok")
        self.assertIn("state=all", mock_api.call_args[0][0])


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------


class TestBuildReport(unittest.TestCase):
    def test_matched_and_missing_label_goes_to_add(self):
        items = [_issue(1, "Fix the CI pipeline", [])]
        add, match, miss, to_apply = blt.build_report(items)
        self.assertEqual(add["ci"], [1])
        self.assertEqual(to_apply[1], ["ci"])

    def test_matched_and_present_label_goes_to_match(self):
        items = [_issue(2, "Fix the CI pipeline", ["ci"])]
        add, match, miss, to_apply = blt.build_report(items)
        self.assertEqual(match["ci"], [2])
        self.assertNotIn(2, to_apply)

    def test_tracked_label_present_but_unmatched_goes_to_miss(self):
        items = [_issue(3, "Bump the version number", ["product"])]
        add, match, miss, to_apply = blt.build_report(items)
        self.assertEqual(miss["product"], [3])
        self.assertNotIn(3, to_apply)

    def test_untracked_label_present_but_unmatched_is_ignored(self):
        items = [_issue(4, "Bump the version number", ["bug"])]
        add, match, miss, to_apply = blt.build_report(items)
        self.assertEqual(add, {})
        self.assertEqual(match, {})
        self.assertEqual(miss, {})

    def test_ci_present_but_title_no_longer_matches_goes_to_miss(self):
        items = [_issue(5, "Bump the version number", ["ci"])]
        add, match, miss, to_apply = blt.build_report(items)
        self.assertEqual(miss["ci"], [5])

    def test_multiple_items_accumulate_per_label(self):
        items = [
            _issue(1, "Fix the CI pipeline", []),
            _issue(2, "Automate the release", []),
        ]
        add, match, miss, to_apply = blt.build_report(items)
        self.assertEqual(sorted(add["ci"]), [1, 2])

    def test_multi_label_title_produces_multiple_to_apply_entries(self):
        items = [_issue(1, "Add CiWatcher agent and fix Reviewer CI-polling loop", [])]
        add, match, miss, to_apply = blt.build_report(items)
        self.assertIn("ci", to_apply[1])
        self.assertIn("agents", to_apply[1])


# ---------------------------------------------------------------------------
# apply_labels
# ---------------------------------------------------------------------------


class TestApplyLabels(unittest.TestCase):
    def test_posts_once_per_issue(self):
        to_apply = {1: ["ci"], 2: ["agents", "testing"]}
        with patch.object(blt.label_by_title, "gh_api") as mock_api:
            blt.apply_labels(to_apply, "owner/repo", "tok")
        self.assertEqual(mock_api.call_count, 2)
        paths = [c[0][0] for c in mock_api.call_args_list]
        self.assertIn("repos/owner/repo/issues/1/labels", paths)
        self.assertIn("repos/owner/repo/issues/2/labels", paths)

    def test_no_calls_when_empty(self):
        with patch.object(blt.label_by_title, "gh_api") as mock_api:
            blt.apply_labels({}, "owner/repo", "tok")
        mock_api.assert_not_called()

    def test_continues_after_http_error(self):
        error = urllib.error.HTTPError(url=None, code=422, msg="Unprocessable", hdrs=None, fp=None)
        calls = []

        def fake_api(path, token, method="GET", body=None):
            calls.append(path)
            if "/1/" in path:
                raise error

        with patch.object(blt.label_by_title, "gh_api", side_effect=fake_api):
            blt.apply_labels({1: ["ci"], 2: ["ci"]}, "owner/repo", "tok")
        self.assertEqual(len(calls), 2)

    def test_continues_after_url_error(self):
        error = urllib.error.URLError("network error")
        with patch.object(blt.label_by_title, "gh_api", side_effect=error):
            blt.apply_labels({1: ["ci"]}, "owner/repo", "tok")  # should not raise


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------


class TestFormatReport(unittest.TestCase):
    def test_output_is_valid_json(self):
        text = blt.format_report({"ci": [2, 1]}, {}, {"product": [3]})
        parsed = json.loads(text)
        self.assertEqual(parsed, {"add": {"ci": [1, 2]}, "match": {}, "miss": {"product": [3]}})

    def test_numbers_are_sorted(self):
        text = blt.format_report({"ci": [3, 1, 2]}, {}, {})
        parsed = json.loads(text)
        self.assertEqual(parsed["add"]["ci"], [1, 2, 3])

    def test_all_three_keys_always_present(self):
        text = blt.format_report({}, {}, {})
        parsed = json.loads(text)
        self.assertEqual(set(parsed.keys()), {"add", "match", "miss"})

    def test_newline_after_each_category_key(self):
        text = blt.format_report({"ci": [1]}, {}, {})
        self.assertIn('"add":\n{', text)

    def test_newline_after_each_number_list(self):
        text = blt.format_report({"ci": [1]}, {}, {})
        self.assertIn("[1]\n", text)

    def test_no_spaces_in_output(self):
        text = blt.format_report({"ci": [1, 2]}, {"agents": [3]}, {"product": [4]})
        self.assertNotIn(" ", text)

    def test_numbers_are_json_numbers_not_strings(self):
        text = blt.format_report({"ci": [1]}, {}, {})
        self.assertIn("[1]", text)
        self.assertNotIn('["1"]', text)


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------


class TestParseArgs(unittest.TestCase):
    def test_dry_run_flag(self):
        args = blt.parse_args(["-n"])
        self.assertTrue(args.dry_run)
        self.assertFalse(args.force)

    def test_force_flag(self):
        args = blt.parse_args(["-f"])
        self.assertTrue(args.force)
        self.assertFalse(args.dry_run)

    def test_long_flags(self):
        self.assertTrue(blt.parse_args(["--dry-run"]).dry_run)
        self.assertTrue(blt.parse_args(["--force"]).force)

    def test_neither_flag_is_an_error(self):
        with self.assertRaises(SystemExit):
            blt.parse_args([])

    def test_both_flags_is_an_error(self):
        with self.assertRaises(SystemExit):
            blt.parse_args(["-n", "-f"])


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain(unittest.TestCase):
    def setUp(self):
        self._env_patch = patch.dict(
            os.environ, {"GITHUB_TOKEN": "test-token", "GITHUB_REPOSITORY": "owner/repo"}
        )
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()

    def test_exit_1_when_token_missing(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
            result = blt.main(["-n"])
        self.assertEqual(result, 1)

    def test_exit_1_when_repository_missing(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": ""}):
            result = blt.main(["-n"])
        self.assertEqual(result, 1)

    def test_exit_1_when_fetch_fails(self):
        with patch.object(blt, "fetch_all_issues", side_effect=Exception("boom")):
            result = blt.main(["-n"])
        self.assertEqual(result, 1)

    def test_dry_run_does_not_apply(self):
        items = [_issue(1, "Fix the CI pipeline", [])]
        with patch.object(blt, "fetch_all_issues", return_value=items):
            with patch.object(blt, "apply_labels") as mock_apply:
                result = blt.main(["-n"])
        self.assertEqual(result, 0)
        mock_apply.assert_not_called()

    def test_force_applies(self):
        items = [_issue(1, "Fix the CI pipeline", [])]
        with patch.object(blt, "fetch_all_issues", return_value=items):
            with patch.object(blt, "apply_labels") as mock_apply:
                result = blt.main(["-f"])
        self.assertEqual(result, 0)
        mock_apply.assert_called_once()
        to_apply_arg = mock_apply.call_args[0][0]
        self.assertEqual(to_apply_arg[1], ["ci"])

    def test_prints_report(self):
        items = [_issue(1, "Fix the CI pipeline", [])]
        with patch.object(blt, "fetch_all_issues", return_value=items):
            with patch("builtins.print") as mock_print:
                blt.main(["-n"])
        # The report is the last of possibly several print() calls (apply_labels
        # would have printed one line per issue had -f been passed).
        printed = mock_print.call_args_list[-1].args[0]
        parsed = json.loads(printed)
        self.assertIn("add", parsed)


if __name__ == "__main__":
    unittest.main()
