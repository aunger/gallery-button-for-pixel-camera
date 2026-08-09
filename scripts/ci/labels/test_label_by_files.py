#!/usr/bin/env python3
"""Unit tests for label_by_files.py."""

import io
import os
import sys
import unittest
import urllib.error
from contextlib import redirect_stdout
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
            lbf.labels_for_path("scripts/lint/test_check_md040.py"),
            frozenset({"automated tests"}),
        )

    def test_workflow_yaml_gives_ci(self):
        self.assertEqual(lbf.labels_for_path(".github/workflows/build.yml"), frozenset({"ci"}))

    def test_ci_script_gives_ci(self):
        for path in (
            "scripts/ci/labels/label_by_title.py",
            "scripts/ci/test-support/dismiss_anr.sh",
        ):
            self.assertEqual(lbf.labels_for_path(path), frozenset({"ci"}), msg=path)

    def test_ci_test_script_gives_both_ci_and_automated_tests(self):
        # Both rules fire on one path: the basename is a test script and the
        # path is CI automation.
        self.assertEqual(
            lbf.labels_for_path("scripts/ci/labels/test_label_by_title.py"),
            frozenset({"automated tests", "ci"}),
        )

    def test_lint_and_ci_monitor_scripts_are_not_ci(self):
        # Pins the deliberate exclusions from CI_PATH_PREFIXES: lint.sh is a
        # local pre-commit tool as much as a CI step, and ci_monitor is the
        # Orchestrator's tool rather than repo CI.
        for path in ("scripts/lint/lint.sh", "scripts/ci_monitor/ci_monitor.py"):
            self.assertEqual(lbf.labels_for_path(path), frozenset(), msg=path)

    def test_unclassified_paths_give_empty_set(self):
        for path in (
            "app/src/main/java/com/gb4pc/Foo.kt",
            "README.md",
            ".github/release.yml",
            ".github/allowed-test-failures.txt",
        ):
            self.assertEqual(lbf.labels_for_path(path), frozenset(), msg=path)

    def test_file_literally_named_test_is_not_itself_a_test(self):
        # segments[:-1] restricts the directory-segment check to
        # directories, so a *file* named "test" doesn't trigger the rule.
        self.assertEqual(lbf.labels_for_path("app/src/main/test"), frozenset())

    def test_agents_path_set_gives_agents(self):
        # The repository owner's four globs from issue #775:
        # **/AGENTS.md, **/CLAUDE.md, **/agents/**/*, .claude/**/*.
        for path in (
            "AGENTS.md",
            "CLAUDE.md",
            "agents/code_edit.md",
            "agents/pr_participation.md",
            "scripts/agents/update_gh_labels.sh",
            ".claude/rules/prose-style.md",
            ".claude/settings.json",
            "scripts/ci_monitor/CLAUDE.md",
        ):
            self.assertEqual(lbf.labels_for_path(path), frozenset({"agents"}), msg=path)

    def test_agents_test_script_gives_agents_and_automated_tests(self):
        self.assertEqual(
            lbf.labels_for_path("scripts/agents/test_update_gh_labels.sh"),
            frozenset({"agents", "automated tests"}),
        )

    def test_workflow_claude_md_gives_agents_and_ci(self):
        # Decision 4 from the repository owner's comment on issue #775.
        self.assertEqual(
            lbf.labels_for_path(".github/workflows/CLAUDE.md"),
            frozenset({"agents", "ci"}),
        )

    def test_agents_matching_is_case_sensitive_and_directory_only(self):
        # A *file* named "agents" is not the directory the glob names, and
        # a lowercase docs/agents.md is a different document, not a stale
        # spelling of AGENTS.md.
        for path in ("app/src/main/agents", "docs/agents.md"):
            self.assertEqual(lbf.labels_for_path(path), frozenset(), msg=path)


# ---------------------------------------------------------------------------
# EXHAUSTIVE_LABELS / FILE_DETERMINED_LABELS identity
# ---------------------------------------------------------------------------


class TestExhaustiveLabelsIdentity(unittest.TestCase):
    def test_exhaustive_labels_is_the_same_object_as_file_determined_labels(self):
        # label_by_title.FILE_DETERMINED_LABELS is the single canonical
        # definition (label_by_title.py suppresses these from PR titles,
        # propagate_issue_labels.py excludes them from propagation, and this
        # module both adds and removes them from a PR's own changed files).
        # An `is` check, not just `==`, is the point: it fails the moment
        # someone reintroduces a separately written literal that happens to
        # equal today's value, which is exactly the silent-drift risk a
        # structural "exactly one writer owns this label" claim cannot rest
        # on three independently maintained copies to avoid.
        self.assertIs(lbf.EXHAUSTIVE_LABELS, lbf.label_by_title.FILE_DETERMINED_LABELS)


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

    def test_pr_782_regression_fixture(self):
        # #782: the "a script under scripts/ci/ plus its test_* sibling"
        # shape that motivates mapping scripts/ci/ to "ci". Without that
        # mapping label_by_files.py itself is unclassified, which empties the
        # intersection and the PR gets no label at all.
        paths = [
            ".github/workflows/label-by-files.yml",
            "scripts/ci/labels/label_by_files.py",
            "scripts/ci/labels/test_label_by_files.py",
        ]
        self.assertEqual(lbf.matching_labels(paths), ["ci"])

    # -- the existential (if-and-only-if) half ------------------------------

    def test_one_agents_path_among_unrelated_files_still_gives_agents(self):
        # "regardless of what else the PR changes": unanimity would fail
        # here, but `agents` is existential.
        self.assertEqual(
            lbf.matching_labels(["agents/code_edit.md", "app/src/main/java/Foo.kt"]),
            ["agents"],
        )

    def test_agents_path_does_not_drag_a_unanimous_label_along(self):
        # The workflow YAML justifies "ci" but agents/x.md does not, so
        # unanimity still fails for "ci" while "agents" fires on its own.
        self.assertEqual(
            lbf.matching_labels(["agents/x.md", ".github/workflows/build.yml"]),
            ["agents"],
        )

    def test_one_path_can_produce_both_shapes(self):
        self.assertEqual(lbf.matching_labels([".github/workflows/CLAUDE.md"]), ["agents", "ci"])

    def test_pr_779_regression_fixture(self):
        # #779: a reorg that moved scripts around, touching .claude/ files
        # and a dozen workflow YAMLs. The existential half applies `agents`
        # even though most changed paths justify only `ci`.
        paths = [
            ".claude/environment.md",
            ".claude/hooks/session-start.sh",
            ".github/workflows/CLAUDE.md",
            ".github/workflows/build.yml",
            ".github/workflows/lint.yml",
        ]
        self.assertIn("agents", lbf.matching_labels(paths))


# ---------------------------------------------------------------------------
# labels_to_remove tests
# ---------------------------------------------------------------------------


class TestLabelsToRemove(unittest.TestCase):
    def test_empty_list_removes_nothing(self):
        # An empty changed-file list is missing evidence, not evidence of
        # absence.
        self.assertEqual(lbf.labels_to_remove([]), [])

    def test_product_only_list_removes_agents(self):
        self.assertEqual(lbf.labels_to_remove(["app/src/main/java/com/gb4pc/Foo.kt"]), ["agents"])

    def test_any_agents_path_keeps_agents(self):
        self.assertEqual(
            lbf.labels_to_remove(["app/src/main/java/Foo.kt", ".claude/settings.json"]),
            [],
        )

    def test_all_test_list_removes_agents(self):
        # The label is justified nowhere here. Whether the PR actually
        # carries it is the caller's concern, not this function's.
        self.assertEqual(
            lbf.labels_to_remove(["app/src/test/java/com/gb4pc/FooTest.kt"]),
            ["agents"],
        )

    def test_unanimous_labels_are_never_removed(self):
        # A product-only diff justifies neither "ci" nor "automated tests",
        # but their path maps are heuristics rather than definitions, so
        # absence of evidence must not become a removal.
        self.assertNotIn("ci", lbf.labels_to_remove(["app/src/main/java/Foo.kt"]))
        self.assertNotIn("automated tests", lbf.labels_to_remove(["app/src/main/java/Foo.kt"]))

    def test_pr_753_regression_fixture(self):
        # #753: a ci_monitor change carrying `agents` that touches no path in
        # the owner's agents set, so the label goes and nothing is added.
        paths = [
            "scripts/ci_monitor/README.md",
            "scripts/ci_monitor/ci_monitor.py",
            "scripts/test_ci_monitor.py",
        ]
        self.assertEqual(lbf.labels_to_remove(paths), ["agents"])
        self.assertEqual(lbf.matching_labels(paths), [])


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


def _delete_paths(mock_api) -> list[str]:
    """Return the API paths *mock_api* was asked to DELETE."""
    return [
        call.args[0] for call in mock_api.call_args_list if call.kwargs.get("method") == "DELETE"
    ]


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
        # The removal path reads the PR's current labels, then deletes,
        # through enforce_mutually_exclusive_labels.gh_api. Default it to a
        # PR carrying no labels so no test reaches the network; the tests
        # that care about a carried label override it.
        self._emxl_patch = patch.object(lbf.emxl, "gh_api", return_value={"labels": []})
        self.mock_emxl_api = self._emxl_patch.start()

    def tearDown(self):
        self._emxl_patch.stop()
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

    def test_no_post_and_no_delete_when_no_files_match_and_label_absent(self):
        with patch.object(lbf, "fetch_changed_files", return_value=["app/src/main/java/Foo.kt"]):
            with patch.object(lbf.label_by_title, "gh_api") as mock_api:
                result = lbf.main()
        self.assertEqual(result, 0)
        mock_api.assert_not_called()
        # The rule forbids `agents` here, but the PR does not carry it, so
        # firing a DELETE would only 404.
        self.assertEqual(_delete_paths(self.mock_emxl_api), [])

    def test_deletes_a_carried_label_no_changed_file_justifies(self):
        self.mock_emxl_api.return_value = {"labels": [{"name": "agents"}, {"name": "p1"}]}
        with patch.object(lbf, "fetch_changed_files", return_value=["app/src/main/java/Foo.kt"]):
            with patch.object(lbf.label_by_title, "gh_api") as mock_api:
                result = lbf.main()
        self.assertEqual(result, 0)
        mock_api.assert_not_called()
        self.assertEqual(
            _delete_paths(self.mock_emxl_api),
            ["repos/owner/repo/issues/42/labels/agents"],
        )

    def test_no_verdict_when_file_list_possibly_truncated(self):
        with patch.object(lbf, "fetch_changed_files", return_value=None):
            with patch.object(lbf.label_by_title, "gh_api") as mock_api:
                result = lbf.main()
        self.assertEqual(result, 0)
        mock_api.assert_not_called()
        self.mock_emxl_api.assert_not_called()

    def test_no_current_label_fetch_when_diff_justifies_agents(self):
        # labels_to_remove is [] when a changed path justifies "agents", so
        # remove_unjustified_labels--and its GET of the PR's current
        # labels--never runs at all. This is the path deciding whether an
        # agents-touching PR pays an extra GET on every synchronize.
        with patch.object(lbf, "fetch_changed_files", return_value=["agents/code_edit.md"]):
            with patch.object(lbf.label_by_title, "gh_api") as mock_api:
                result = lbf.main()
        self.assertEqual(result, 0)
        self.assertEqual(mock_api.call_args[1]["body"], {"labels": ["agents"]})
        self.mock_emxl_api.assert_not_called()

    def test_removal_log_line_says_unjustified_not_conflicting(self):
        # enforce_mutually_exclusive_labels.remove_labels defaults its log
        # line to "conflicting", which would be false here: nothing conflicts,
        # the diff simply justifies nothing. reason="unjustified" keeps the
        # log line honest instead of contradicting the "no changed file
        # justifies them" line printed immediately before it.
        self.mock_emxl_api.return_value = {"labels": [{"name": "agents"}]}
        with patch.object(lbf, "fetch_changed_files", return_value=["app/src/main/java/Foo.kt"]):
            with patch.object(lbf.label_by_title, "gh_api"):
                out = io.StringIO()
                with redirect_stdout(out):
                    lbf.main()
        self.assertIn("Removed unjustified label 'agents'", out.getvalue())

    def test_no_verdict_when_file_list_is_empty(self):
        # Removing a label on the strength of an empty file list is exactly
        # the case where the evidence does not exist.
        with patch.object(lbf, "fetch_changed_files", return_value=[]):
            with patch.object(lbf.label_by_title, "gh_api") as mock_api:
                result = lbf.main()
        self.assertEqual(result, 0)
        mock_api.assert_not_called()
        self.mock_emxl_api.assert_not_called()

    def test_exit_1_when_removal_fails_with_a_non_404(self):
        def fake_api(path, token, method="GET", body=None, **kwargs):
            if method == "DELETE":
                raise urllib.error.HTTPError(
                    url=None, code=500, msg="Server Error", hdrs=None, fp=None
                )
            return {"labels": [{"name": "agents"}]}

        self.mock_emxl_api.side_effect = fake_api
        with patch.object(lbf, "fetch_changed_files", return_value=["app/src/main/java/Foo.kt"]):
            with patch.object(lbf.label_by_title, "gh_api"):
                result = lbf.main()
        self.assertEqual(result, 1)

    def test_exit_0_when_removal_404s(self):
        # A 404 means the label was already gone (e.g. a race with a human
        # removing it), which is the desired end state, not a failure.
        def fake_api(path, token, method="GET", body=None, **kwargs):
            if method == "DELETE":
                raise urllib.error.HTTPError(
                    url=None, code=404, msg="Not Found", hdrs=None, fp=None
                )
            return {"labels": [{"name": "agents"}]}

        self.mock_emxl_api.side_effect = fake_api
        with patch.object(lbf, "fetch_changed_files", return_value=["app/src/main/java/Foo.kt"]):
            with patch.object(lbf.label_by_title, "gh_api"):
                result = lbf.main()
        self.assertEqual(result, 0)

    def test_exit_1_when_the_current_label_fetch_fails(self):
        self.mock_emxl_api.side_effect = urllib.error.URLError("network error")
        with patch.object(lbf, "fetch_changed_files", return_value=["app/src/main/java/Foo.kt"]):
            with patch.object(lbf.label_by_title, "gh_api"):
                result = lbf.main()
        self.assertEqual(result, 1)

    def test_posts_and_deletes_are_both_attempted_in_one_run(self):
        # A PR whose diff justifies `ci` but forbids `agents`, carrying both.
        self.mock_emxl_api.return_value = {"labels": [{"name": "agents"}]}
        with patch.object(lbf, "fetch_changed_files", return_value=[".github/workflows/build.yml"]):
            with patch.object(lbf.label_by_title, "gh_api") as mock_api:
                result = lbf.main()
        self.assertEqual(result, 0)
        self.assertEqual(mock_api.call_args[1]["body"], {"labels": ["ci"]})
        self.assertEqual(
            _delete_paths(self.mock_emxl_api),
            ["repos/owner/repo/issues/42/labels/agents"],
        )

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
