#!/usr/bin/env python3
"""Unit tests for label_by_files.py."""

import os
import sys
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import label_by_files as lbf  # noqa: E402


# ---------------------------------------------------------------------------
# labels_for_path tests
# ---------------------------------------------------------------------------


class TestLabelsForPath(unittest.TestCase):
    def test_gradle_test_source_sets_give_automated_tests(self):
        for path in (
            "app/src/test/java/com/gb4pc/Foo.kt",
            "app/src/androidTest/java/com/gb4pc/Foo.kt",
            "app/src/sharedTest/java/com/gb4pc/Foo.kt",
            "e2e-mock-gallery/src/test/java/com/gb4pc/Foo.kt",
        ):
            self.assertEqual(lbf.labels_for_path(path), frozenset({"automated tests"}), msg=path)

    def test_test_script_basename_gives_automated_tests(self):
        self.assertEqual(
            lbf.labels_for_path("scripts/ci/labels/test_label_by_files.py"),
            frozenset({"automated tests"}),
        )

    def test_workflow_yaml_gives_ci(self):
        self.assertEqual(lbf.labels_for_path(".github/workflows/build.yml"), frozenset({"ci"}))

    def test_unclassified_paths_give_empty_set(self):
        for path in (
            "app/src/main/java/com/gb4pc/Foo.kt",
            "README.md",
            "agents/pr_participation.md",
            ".github/release.yml",
            ".github/allowed-test-failures.txt",
        ):
            self.assertEqual(lbf.labels_for_path(path), frozenset(), msg=path)

    def test_file_literally_named_test_is_not_itself_a_test(self):
        # segments[:-1] restricts the directory-segment check to
        # directories, so a *file* named "test" doesn't trigger the rule.
        self.assertEqual(lbf.labels_for_path("app/src/main/test"), frozenset())


# ---------------------------------------------------------------------------
# matching_labels tests
# ---------------------------------------------------------------------------


class TestMatchingLabels(unittest.TestCase):
    def test_empty_list_gives_no_labels(self):
        self.assertEqual(lbf.matching_labels([]), [])

    def test_all_test_paths_give_automated_tests(self):
        self.assertEqual(
            lbf.matching_labels(
                [
                    "app/src/test/java/com/gb4pc/FooTest.kt",
                    "app/src/androidTest/java/com/gb4pc/BarTest.kt",
                ]
            ),
            ["automated tests"],
        )

    def test_all_workflow_paths_give_ci(self):
        self.assertEqual(
            lbf.matching_labels([".github/workflows/build.yml", ".github/workflows/lint.yml"]),
            ["ci"],
        )

    def test_mixing_test_and_workflow_files_gives_no_labels(self):
        # Unanimity: a test path justifies only "automated tests" and a
        # workflow path justifies only "ci", so their intersection is empty.
        self.assertEqual(
            lbf.matching_labels(
                [
                    "app/src/test/java/com/gb4pc/FooTest.kt",
                    ".github/workflows/build.yml",
                ]
            ),
            [],
        )

    def test_test_files_plus_one_product_file_gives_no_labels(self):
        self.assertEqual(
            lbf.matching_labels(
                [
                    "app/src/test/java/com/gb4pc/FooTest.kt",
                    "app/src/androidTest/java/com/gb4pc/BarTest.kt",
                    "app/src/main/java/com/gb4pc/Foo.kt",
                ]
            ),
            [],
        )

    def test_pr_735_regression_fixture(self):
        # #735: touches only app/src/androidTest, sharedTest, and test
        # sources. This is one of the two misses issue #775 cited.
        paths = [
            "app/src/androidTest/java/com/gb4pc/e2e/visual/ColorMatch.kt",
            "app/src/sharedTest/java/com/gb4pc/e2e/visual/PixelMask.kt",
            "app/src/test/java/com/gb4pc/e2e/visual/PixelMaskTest.kt",
        ]
        self.assertEqual(lbf.matching_labels(paths), ["automated tests"])

    def test_pr_665_regression_fixture(self):
        # #665: a pure .github/workflows/*.yml version bump. The other miss
        # issue #775 cited.
        paths = [".github/workflows/strip-session-bylines.yml"]
        self.assertEqual(lbf.matching_labels(paths), ["ci"])


# ---------------------------------------------------------------------------
# fetch_changed_files tests
# ---------------------------------------------------------------------------


class TestFetchChangedFiles(unittest.TestCase):
    def _page(self, n: int, start: int = 0) -> list[dict]:
        return [{"filename": f"file{i}.py"} for i in range(start, start + n)]

    def test_stops_on_short_page(self):
        page1 = self._page(100)
        page2 = self._page(1, start=100)
        with patch.object(lbf.label_by_title, "gh_api", side_effect=[page1, page2]) as mock_api:
            result = lbf.fetch_changed_files("owner/repo", 42, "tok")
        self.assertEqual(len(result), 101)
        self.assertEqual(mock_api.call_count, 2)

    def test_stops_on_empty_first_page(self):
        with patch.object(lbf.label_by_title, "gh_api", return_value=[]) as mock_api:
            result = lbf.fetch_changed_files("owner/repo", 42, "tok")
        self.assertEqual(result, [])
        mock_api.assert_called_once()

    def test_single_short_page_returns_its_paths(self):
        with patch.object(lbf.label_by_title, "gh_api", return_value=[{"filename": "a.py"}]):
            result = lbf.fetch_changed_files("owner/repo", 42, "tok")
        self.assertEqual(result, ["a.py"])

    def test_hitting_max_pages_without_a_short_page_returns_none(self):
        full_page = self._page(100)
        with patch.object(lbf.label_by_title, "gh_api", return_value=full_page) as mock_api:
            result = lbf.fetch_changed_files("owner/repo", 42, "tok")
        self.assertIsNone(result)
        self.assertEqual(mock_api.call_count, lbf.MAX_PAGES)

    def test_uses_pull_files_endpoint(self):
        with patch.object(lbf.label_by_title, "gh_api", return_value=[]) as mock_api:
            lbf.fetch_changed_files("owner/repo", 42, "tok")
        self.assertIn("pulls/42/files", mock_api.call_args[0][0])


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
                "PR_NUMBER": "42",
            },
        )
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()

    def test_exit_1_when_token_missing(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
            result = lbf.main()
        self.assertEqual(result, 1)

    def test_exit_1_when_repository_missing(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": ""}):
            result = lbf.main()
        self.assertEqual(result, 1)

    def test_exit_1_when_pr_number_missing(self):
        with patch.dict(os.environ, {"PR_NUMBER": ""}):
            result = lbf.main()
        self.assertEqual(result, 1)

    def test_exit_1_when_pr_number_non_integer(self):
        with patch.dict(os.environ, {"PR_NUMBER": "abc"}):
            result = lbf.main()
        self.assertEqual(result, 1)

    def test_no_api_call_when_no_files_match(self):
        with patch.object(lbf, "fetch_changed_files", return_value=["app/src/main/java/Foo.kt"]):
            with patch.object(lbf.label_by_title, "gh_api") as mock_api:
                result = lbf.main()
        self.assertEqual(result, 0)
        mock_api.assert_not_called()

    def test_no_api_call_when_file_list_possibly_truncated(self):
        with patch.object(lbf, "fetch_changed_files", return_value=None):
            with patch.object(lbf.label_by_title, "gh_api") as mock_api:
                result = lbf.main()
        self.assertEqual(result, 0)
        mock_api.assert_not_called()

    def test_posts_matching_label(self):
        with patch.object(lbf, "fetch_changed_files", return_value=[".github/workflows/build.yml"]):
            with patch.object(lbf.label_by_title, "gh_api") as mock_api:
                result = lbf.main()
        self.assertEqual(result, 0)
        mock_api.assert_called_once()
        args, kwargs = mock_api.call_args
        self.assertEqual(args[0], "repos/owner/repo/issues/42/labels")
        self.assertEqual(kwargs["method"], "POST")
        self.assertEqual(kwargs["body"], {"labels": ["ci"]})

    def test_exit_1_on_fetch_http_error(self):
        error = urllib.error.HTTPError(url=None, code=404, msg="Not Found", hdrs=None, fp=None)
        with patch.object(lbf, "fetch_changed_files", side_effect=error):
            result = lbf.main()
        self.assertEqual(result, 1)

    def test_exit_1_on_fetch_url_error(self):
        error = urllib.error.URLError("network error")
        with patch.object(lbf, "fetch_changed_files", side_effect=error):
            result = lbf.main()
        self.assertEqual(result, 1)

    def test_exit_1_on_apply_http_error(self):
        error = urllib.error.HTTPError(url=None, code=422, msg="Unprocessable", hdrs=None, fp=None)
        with patch.object(lbf, "fetch_changed_files", return_value=[".github/workflows/build.yml"]):
            with patch.object(lbf.label_by_title, "gh_api", side_effect=error):
                result = lbf.main()
        self.assertEqual(result, 1)

    def test_exit_1_on_apply_url_error(self):
        error = urllib.error.URLError("network error")
        with patch.object(lbf, "fetch_changed_files", return_value=[".github/workflows/build.yml"]):
            with patch.object(lbf.label_by_title, "gh_api", side_effect=error):
                result = lbf.main()
        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
