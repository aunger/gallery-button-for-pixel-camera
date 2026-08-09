#!/usr/bin/env python3
"""Unit tests for audit_labels_by_files.py."""

import io
import json
import os
import sys
import unittest
import urllib.error
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import audit_labels_by_files as alf  # noqa: E402


def _pull(number: int, labels: list[str], merged: bool = True) -> dict:
    return {
        "number": number,
        "labels": [{"name": lbl} for lbl in labels],
        "merged_at": "2026-07-01T00:00:00Z" if merged else None,
    }


def _paths_from(mapping: dict[int, list[str] | None]):
    """Build a fetch_paths callable backed by *mapping*."""
    return lambda number: mapping[number]


# ---------------------------------------------------------------------------
# fetch_merged_pulls
# ---------------------------------------------------------------------------


class TestFetchMergedPulls(unittest.TestCase):
    def test_skips_closed_but_unmerged_pull_requests(self):
        page = [_pull(3, []), _pull(2, [], merged=False), _pull(1, [])]
        with patch.object(alf.label_by_title, "gh_api", side_effect=[page, []]):
            result = alf.fetch_merged_pulls("owner/repo", "tok")
        self.assertEqual([p["number"] for p in result], [3, 1])

    def test_paginates_until_empty_page(self):
        page1 = [_pull(i, []) for i in range(100, 0, -1)]
        page2 = [_pull(0, [])]
        with patch.object(alf.label_by_title, "gh_api", side_effect=[page1, page2, []]) as mock_api:
            result = alf.fetch_merged_pulls("owner/repo", "tok")
        self.assertEqual(len(result), 101)
        self.assertEqual(mock_api.call_count, 3)

    def test_limit_truncates_the_pull_request_list(self):
        page = [_pull(i, []) for i in range(10, 0, -1)]
        with patch.object(alf.label_by_title, "gh_api", side_effect=[page, []]) as mock_api:
            result = alf.fetch_merged_pulls("owner/repo", "tok", limit=3)
        self.assertEqual([p["number"] for p in result], [10, 9, 8])
        # Stopping mid-page means the walk never asks for a second page.
        self.assertEqual(mock_api.call_count, 1)

    def test_requests_closed_pull_requests_newest_first(self):
        with patch.object(alf.label_by_title, "gh_api", return_value=[]) as mock_api:
            alf.fetch_merged_pulls("owner/repo", "tok")
        path = mock_api.call_args[0][0]
        self.assertIn("pulls?state=closed", path)
        self.assertIn("direction=desc", path)


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------


class TestBuildReport(unittest.TestCase):
    def test_applied_and_missing_label_goes_to_add(self):
        pulls = [_pull(1, [])]
        report = alf.build_report(pulls, _paths_from({1: [".github/workflows/build.yml"]}))
        self.assertEqual(report.add, {"ci": [1]})
        self.assertEqual(report.to_add, {1: ["ci"]})
        self.assertEqual(report.match, {})

    def test_applied_and_present_label_goes_to_match(self):
        pulls = [_pull(2, ["ci"])]
        report = alf.build_report(pulls, _paths_from({2: [".github/workflows/build.yml"]}))
        self.assertEqual(report.match, {"ci": [2]})
        self.assertEqual(report.add, {})
        self.assertEqual(report.to_add, {})

    def test_forbidden_and_carried_label_goes_to_remove(self):
        pulls = [_pull(3, ["agents"])]
        report = alf.build_report(pulls, _paths_from({3: ["app/src/main/java/Foo.kt"]}))
        self.assertEqual(report.remove, {"agents": [3]})
        self.assertEqual(report.to_remove, {3: ["agents"]})

    def test_forbidden_but_absent_label_is_not_reported(self):
        pulls = [_pull(4, ["ci"])]
        report = alf.build_report(pulls, _paths_from({4: ["app/src/main/java/Foo.kt"]}))
        self.assertEqual(report.remove, {})
        self.assertEqual(report.to_remove, {})

    def test_unanimity_labels_are_never_proposed_for_removal(self):
        # A product-only diff justifies no "ci", but "ci" is add-only: its
        # path map is a heuristic, not a definition.
        pulls = [_pull(5, ["ci", "automated tests"])]
        report = alf.build_report(pulls, _paths_from({5: ["app/src/main/java/Foo.kt"]}))
        self.assertEqual(report.remove, {})

    def test_one_pull_request_can_both_gain_and_lose_a_label(self):
        pulls = [_pull(6, ["agents"])]
        report = alf.build_report(pulls, _paths_from({6: [".github/workflows/build.yml"]}))
        self.assertEqual(report.to_add, {6: ["ci"]})
        self.assertEqual(report.to_remove, {6: ["agents"]})

    def test_removal_uses_the_spelling_github_returned(self):
        pulls = [_pull(7, ["Agents"])]
        report = alf.build_report(pulls, _paths_from({7: ["app/src/main/java/Foo.kt"]}))
        self.assertEqual(report.to_remove, {7: ["Agents"]})

    def test_truncated_file_list_is_skipped_and_counted(self):
        pulls = [_pull(8, ["agents"]), _pull(9, [])]
        report = alf.build_report(pulls, _paths_from({8: None, 9: [".github/workflows/build.yml"]}))
        self.assertEqual(report.skipped, [8])
        self.assertEqual(report.remove, {})
        self.assertEqual(report.add, {"ci": [9]})

    def test_numbers_accumulate_per_label(self):
        pulls = [_pull(1, []), _pull(2, [])]
        report = alf.build_report(
            pulls,
            _paths_from(
                {1: [".github/workflows/build.yml"], 2: [".github/workflows/lint.yml"]},
            ),
        )
        self.assertEqual(sorted(report.add["ci"]), [1, 2])


# ---------------------------------------------------------------------------
# apply_changes
# ---------------------------------------------------------------------------


class TestApplyChanges(unittest.TestCase):
    def test_posts_adds_and_deletes_removals(self):
        with patch.object(alf.label_by_title, "gh_api") as mock_add:
            with patch.object(alf.emxl, "gh_api") as mock_remove:
                result = alf.apply_changes({1: ["ci"]}, {2: ["agents"]}, "owner/repo", "tok")
        self.assertTrue(result)
        self.assertEqual(mock_add.call_args[0][0], "repos/owner/repo/issues/1/labels")
        self.assertEqual(mock_add.call_args[1]["method"], "POST")
        self.assertEqual(mock_remove.call_args[0][0], "repos/owner/repo/issues/2/labels/agents")
        self.assertEqual(mock_remove.call_args[1]["method"], "DELETE")

    def test_removal_log_line_says_unjustified_not_conflicting(self):
        # Nothing conflicts here either: the removal reason is that no
        # changed path justifies the label, so the log line must say so
        # instead of defaulting to enforce_mutually_exclusive_labels's
        # "conflicting" wording, which is the only line printed for this
        # call site and would otherwise be the sole, wrong explanation on
        # the Actions log.
        with patch.object(alf.label_by_title, "gh_api"):
            with patch.object(alf.emxl, "gh_api"):
                out = io.StringIO()
                with redirect_stdout(out):
                    alf.apply_changes({}, {2: ["agents"]}, "owner/repo", "tok")
        self.assertIn("Removed unjustified label 'agents' from #2.", out.getvalue())

    def test_no_calls_when_nothing_to_do(self):
        with patch.object(alf.label_by_title, "gh_api") as mock_add:
            with patch.object(alf.emxl, "gh_api") as mock_remove:
                result = alf.apply_changes({}, {}, "owner/repo", "tok")
        self.assertTrue(result)
        mock_add.assert_not_called()
        mock_remove.assert_not_called()

    def test_continues_past_a_failure_but_reports_it(self):
        def fake_add(path, token, method="GET", body=None, **kwargs):
            if "/1/" in path:
                raise urllib.error.HTTPError(
                    url=None, code=422, msg="Unprocessable", hdrs=None, fp=None
                )

        with patch.object(alf.label_by_title, "gh_api", side_effect=fake_add) as mock_add:
            with patch.object(alf.emxl, "gh_api") as mock_remove:
                result = alf.apply_changes(
                    {1: ["ci"], 2: ["ci"]}, {3: ["agents"]}, "owner/repo", "tok"
                )
        self.assertFalse(result)
        self.assertEqual(mock_add.call_count, 2)
        mock_remove.assert_called_once()


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------


class TestFormatReport(unittest.TestCase):
    def test_output_is_valid_json_with_all_three_categories(self):
        report = alf.Report({"ci": [2, 1]}, {}, {"agents": [3]}, {}, {}, [])
        parsed = json.loads(alf.format_report(report))
        self.assertEqual(parsed, {"add": {"ci": [1, 2]}, "match": {}, "remove": {"agents": [3]}})


# ---------------------------------------------------------------------------
# main()
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

    def test_requires_a_mode_flag(self):
        with self.assertRaises(SystemExit):
            alf.main([])

    def test_exit_1_when_token_missing(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
            self.assertEqual(alf.main(["-n"]), 1)

    def test_exit_1_when_repository_missing(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": ""}):
            self.assertEqual(alf.main(["-n"]), 1)

    def test_exit_1_when_limit_is_not_positive(self):
        self.assertEqual(alf.main(["-n", "--limit", "0"]), 1)

    def test_dry_run_writes_nothing(self):
        with patch.object(alf, "fetch_merged_pulls", return_value=[_pull(1, ["agents"])]):
            with patch.object(
                alf.label_by_files, "fetch_changed_files", return_value=["app/src/main/Foo.kt"]
            ):
                with patch.object(alf, "apply_changes") as mock_apply:
                    result = alf.main(["-n", "-q"])
        self.assertEqual(result, 0)
        mock_apply.assert_not_called()

    def test_force_posts_and_deletes(self):
        pulls = [_pull(1, []), _pull(2, ["agents"])]
        paths = {1: [".github/workflows/build.yml"], 2: ["app/src/main/Foo.kt"]}
        with patch.object(alf, "fetch_merged_pulls", return_value=pulls):
            with patch.object(
                alf.label_by_files,
                "fetch_changed_files",
                side_effect=lambda repo, number, token: paths[number],
            ):
                with patch.object(alf.label_by_title, "gh_api") as mock_add:
                    with patch.object(alf.emxl, "gh_api") as mock_remove:
                        result = alf.main(["-f", "-q"])
        self.assertEqual(result, 0)
        self.assertEqual(mock_add.call_args[1]["body"], {"labels": ["ci"]})
        self.assertEqual(mock_remove.call_args[0][0], "repos/owner/repo/issues/2/labels/agents")

    def test_force_returns_1_when_a_write_fails(self):
        with patch.object(alf, "fetch_merged_pulls", return_value=[_pull(1, [])]):
            with patch.object(
                alf.label_by_files,
                "fetch_changed_files",
                return_value=[".github/workflows/build.yml"],
            ):
                with patch.object(
                    alf.label_by_title,
                    "gh_api",
                    side_effect=urllib.error.URLError("network error"),
                ):
                    result = alf.main(["-f", "-q"])
        self.assertEqual(result, 1)

    def test_per_pull_request_fetch_failure_is_skipped_not_fatal(self):
        pulls = [_pull(1, ["agents"]), _pull(2, [])]

        def fake_fetch(repo, number, token):
            if number == 1:
                raise urllib.error.HTTPError(
                    url=None, code=404, msg="Not Found", hdrs=None, fp=None
                )
            return [".github/workflows/build.yml"]

        with patch.object(alf, "fetch_merged_pulls", return_value=pulls):
            with patch.object(alf.label_by_files, "fetch_changed_files", side_effect=fake_fetch):
                with patch.object(alf, "apply_changes", return_value=True) as mock_apply:
                    result = alf.main(["-f", "-q"])
        self.assertEqual(result, 0)
        # #1 yielded no verdict at all, so nothing is written for it.
        to_add, to_remove = mock_apply.call_args[0][0], mock_apply.call_args[0][1]
        self.assertEqual(to_add, {2: ["ci"]})
        self.assertEqual(to_remove, {})

    def test_exit_1_when_the_pull_request_list_cannot_be_fetched(self):
        with patch.object(alf, "fetch_merged_pulls", side_effect=RuntimeError("boom")):
            self.assertEqual(alf.main(["-n", "-q"]), 1)

    def test_limit_is_passed_through_to_the_fetch(self):
        with patch.object(alf, "fetch_merged_pulls", return_value=[]) as mock_fetch:
            alf.main(["-n", "-q", "--limit", "5"])
        self.assertEqual(mock_fetch.call_args[0][2], 5)


if __name__ == "__main__":
    unittest.main()
