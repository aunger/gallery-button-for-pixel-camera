#!/usr/bin/env python3
"""Unit tests for label_by_title.py."""

import json
import os
import sys
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import label_by_title as lpt  # noqa: E402


# ---------------------------------------------------------------------------
# gh_api tests
# ---------------------------------------------------------------------------


class TestGhApi(unittest.TestCase):
    def _make_response(self, body: bytes, status: int = 200) -> MagicMock:
        resp = MagicMock()
        resp.read.return_value = body
        resp.status = status
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_returns_parsed_json_for_non_empty_body(self):
        payload = [{"name": "ci"}]
        response = self._make_response(json.dumps(payload).encode())
        with patch("urllib.request.urlopen", return_value=response):
            result = lpt.gh_api("repos/owner/repo/issues/1/labels", token="tok")
        self.assertEqual(result, payload)

    def test_returns_none_for_empty_body(self):
        response = self._make_response(b"", status=204)
        with patch("urllib.request.urlopen", return_value=response):
            result = lpt.gh_api("repos/owner/repo/issues/1/labels", token="tok", method="POST")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# matching_labels tests
# ---------------------------------------------------------------------------


class TestMatchingLabelsCi(unittest.TestCase):
    def test_bare_ci_word_matches(self):
        self.assertIn("ci", lpt.matching_labels("Fix the CI pipeline"))

    def test_ci_prefixed_by_underscore_matches(self):
        self.assertIn("ci", lpt.matching_labels("Move ci_monitor.py into scripts/"))

    def test_ci_within_longer_word_does_not_match(self):
        self.assertNotIn("ci", lpt.matching_labels("Fix the specific config"))

    def test_automat_suffixes_match(self):
        self.assertIn("ci", lpt.matching_labels("Automate version sync"))
        self.assertIn("ci", lpt.matching_labels("Add automated coverage"))
        self.assertIn("ci", lpt.matching_labels("Validate automation-required labels"))

    def test_case_insensitive(self):
        self.assertIn("ci", lpt.matching_labels("fix the ci pipeline"))
        self.assertIn("ci", lpt.matching_labels("Fix The CI Pipeline"))

    def test_workflow_suffixes_match(self):
        self.assertIn("ci", lpt.matching_labels("Add workflow_dispatch trigger"))
        self.assertIn("ci", lpt.matching_labels("Daily workflow to archive stale issues"))

    def test_github_action_matches_with_separator_variants(self):
        self.assertIn("ci", lpt.matching_labels("GitHub Action: remove verified label"))
        self.assertIn("ci", lpt.matching_labels("Enforce label sets via GitHub Actions"))
        self.assertIn("ci", lpt.matching_labels("Fix github_action bug"))

    def test_e2e_suffix_matches_in_ci(self):
        self.assertIn("ci", lpt.matching_labels("Capture E2E test video on failure"))

    def test_e2e_without_leading_boundary_does_not_match_ci(self):
        # "E2E" inside "VisualE2ETest" has no boundary before it (preceded
        # by "l"), so the suffix-only e2e\w* rule doesn't fire.
        self.assertNotIn("ci", lpt.matching_labels("Add VisualE2Efixture helper"))

    def test_e2etest_camel_case_identifier_does_not_match_ci(self):
        # The e2etest identifier rule is testing-only by design; CI-auto-
        # filed test-failure issue titles don't get ci from this alone.
        self.assertNotIn(
            "ci",
            lpt.matching_labels("[GalleryButtonVisualE2ETest] test2a_emptyGalleryNoGreenAfterTap"),
        )

    def test_preflight_variants_match(self):
        self.assertIn("ci", lpt.matching_labels("Preflight: Pixel Launcher isn't responding"))
        self.assertIn("ci", lpt.matching_labels("CI pre-flight: replace fixed sleep"))

    def test_codeql_matches(self):
        self.assertIn("ci", lpt.matching_labels("Add CodeQL workflow for scanning"))

    def test_merge_alone_does_not_match(self):
        # "merge" needs a companion word (gate/block/PR); bare merge/merging
        # is too generic on its own.
        self.assertNotIn("ci", lpt.matching_labels("Merge feature branches together"))

    def test_merge_with_gate_matches(self):
        self.assertIn("ci", lpt.matching_labels("Add to label-based merge gate"))
        self.assertIn("ci", lpt.matching_labels("Extend the merge gating logic"))

    def test_gate_does_not_match_gateway(self):
        # gat(ing|e[ds]?) is deliberately narrower than an open gate\w*, so
        # it doesn't sweep in unrelated words like "gateway".
        self.assertNotIn("ci", lpt.matching_labels("Merge feature via the new gateway service"))

    def test_merge_with_block_matches_either_order(self):
        self.assertIn("ci", lpt.matching_labels("GitHub Action: block merge when label present"))
        self.assertIn("ci", lpt.matching_labels("'Merging is blocked' after checks pass"))

    def test_merge_with_unblock_matches(self):
        # (un)?block\w* -- "unblock merges" has no boundary before "block"
        # for a plain block\w* rule, so the "un" prefix must be explicit.
        self.assertIn("ci", lpt.matching_labels("Report empty results to unblock merges"))

    def test_merge_with_pr_matches_either_order(self):
        self.assertIn("ci", lpt.matching_labels("(re PR #349) Disable CodeQL setup before merging"))

    def test_merge_without_any_companion_does_not_match(self):
        self.assertNotIn(
            "ci",
            lpt.matching_labels(
                "File tracking issues for before-merging requirements instead of consulting"
            ),
        )


class TestMatchingLabelsAgents(unittest.TestCase):
    def test_agent_word_matches(self):
        self.assertIn("agents", lpt.matching_labels("Add agent guidance for code reviews"))

    def test_agent_suffix_matches(self):
        self.assertIn("agents", lpt.matching_labels("Tighten guidance for agents"))

    def test_agent_prefixed_by_underscore_matches(self):
        self.assertIn("agents", lpt.matching_labels("Rename the_agent module"))

    def test_dev_orchestration_matches(self):
        self.assertIn("agents", lpt.matching_labels("Update dev_orchestration.md for clarity"))

    def test_rule_and_rules_match(self):
        self.assertIn("agents", lpt.matching_labels("Add orchestrator timestamp reporting rule"))
        self.assertIn("agents", lpt.matching_labels("Rework scope creep rules"))

    def test_ruled_does_not_match(self):
        self.assertNotIn("agents", lpt.matching_labels("The court ruled in our favor"))

    def test_attribution_and_byline_match(self):
        self.assertIn("agents", lpt.matching_labels("Claude attribution/byline settings"))
        self.assertIn("agents", lpt.matching_labels("Clear commit attribution in settings.json"))

    def test_verif_suffixes_match(self):
        self.assertIn("agents", lpt.matching_labels("Add PR verification agent instructions"))
        self.assertIn("agents", lpt.matching_labels("Test PR: verify remove-verified-on-push"))

    def test_author_exact_word_matches(self):
        self.assertIn(
            "agents", lpt.matching_labels("Empower Author to file issues for review requests")
        )

    def test_authors_does_not_match(self):
        self.assertNotIn("agents", lpt.matching_labels("List all the authors of this project"))

    def test_review_requires_suffix(self):
        self.assertIn("agents", lpt.matching_labels("Clarify Reviewer account sharing"))
        self.assertNotIn("agents", lpt.matching_labels("Leave a review"))

    def test_orchestrat_suffixes_match(self):
        self.assertIn("agents", lpt.matching_labels("Orchestrator directions: CI review loop"))
        self.assertIn("agents", lpt.matching_labels("Build an /orchestrate Claude Code plugin"))

    def test_ci_monitor_matches_with_separator_variants(self):
        self.assertIn("agents", lpt.matching_labels("CI Monitor: handle two lagging endpoints"))
        self.assertIn("agents", lpt.matching_labels("Speed up ci_monitor's poll interval"))
        self.assertIn("agents", lpt.matching_labels("Refactor CIMonitor internals"))

    def test_subagent_concatenated_matches(self):
        self.assertIn("agents", lpt.matching_labels("Make subagent delegation dependable"))
        self.assertIn(
            "agents", lpt.matching_labels("Replace CiWatcher subagents with a Monitor loop")
        )

    def test_subagent_with_single_separator_matches(self):
        self.assertIn("agents", lpt.matching_labels("Fix the sub-agent handoff"))
        self.assertIn("agents", lpt.matching_labels("Fix the sub agent handoff"))
        self.assertIn("agents", lpt.matching_labels("Fix the sub_agent handoff"))


class TestMatchingLabelsTesting(unittest.TestCase):
    def test_e2e_matches(self):
        self.assertIn("testing", lpt.matching_labels("Add E2E instrumented test"))

    def test_e2e_within_camel_case_identifier_does_not_match_the_plain_e2e_rule(self):
        # No boundary surrounds "E2E" inside "VisualE2Efixture", and it's
        # not followed by "Test", so neither e2e rule fires.
        self.assertEqual(lpt.matching_labels("Add VisualE2Efixture helper"), [])

    def test_e2etest_camel_case_identifier_matches_testing(self):
        # The dedicated e2etest identifier rule (both sides open) catches
        # what the plain \be2e\b rule can't reach inside CamelCase names.
        self.assertIn("testing", lpt.matching_labels("Add GalleryButtonVisualE2ETest"))

    def test_preflight_matches_testing(self):
        self.assertIn(
            "testing", lpt.matching_labels("CI pre-flight: MockCameraActivity ready signal")
        )

    def test_camel_case_test_suffix_matches_case_sensitively(self):
        self.assertIn("testing", lpt.matching_labels("Add Gaussian-blur noise to ShapeMatcherTest"))
        self.assertIn(
            "testing", lpt.matching_labels("Six instrumented (androidTest) classes are skipped")
        )

    def test_lowercase_camel_test_identifier_does_not_match(self):
        # "shapematchertest" (all lowercase, no separators) has no leading
        # boundary before its final "test" for the ordinary test\w* rule
        # (preceded by "r"), so only a case-insensitive version of the new
        # \w*Test(\b|_) rule could catch it--and it must not.
        self.assertEqual(lpt.matching_labels("shapematchertest"), [])

    def test_common_english_words_ending_in_test_do_not_match(self):
        # Each of these ends in the literal substring "test" with no
        # boundary before it, so only a case-insensitive version of the new
        # \w*Test(\b|_) rule could wrongly catch them. If this test starts
        # failing, the case-sensitivity of that rule has regressed.
        result = lpt.matching_labels("Set the latest, fastest, greatest contest and protest")
        self.assertEqual(result, [])

    def test_unit_exact_word_matches(self):
        self.assertIn("testing", lpt.matching_labels("Skip Python and shell unit tests"))

    def test_units_does_not_match_unit_rule(self):
        # "units" alone (no "test") should not match the strict \bunit\b rule.
        self.assertNotIn("testing", lpt.matching_labels("Convert distance units to metric"))

    def test_test_suffixes_match(self):
        self.assertIn("testing", lpt.matching_labels("Add noisy tests for coverage"))
        self.assertIn("testing", lpt.matching_labels("Add :testgallery module"))

    def test_test_prefixed_by_underscore_matches(self):
        self.assertIn(
            "testing", lpt.matching_labels("Speed up ci_monitor's poll interval unit_tests")
        )


class TestMatchingLabelsCombined(unittest.TestCase):
    def test_multiple_labels_from_one_title(self):
        result = lpt.matching_labels("Add CiWatcher agent and fix Reviewer CI-polling loop")
        self.assertIn("ci", result)
        self.assertIn("agents", result)
        self.assertNotIn("testing", result)

    def test_no_match_returns_empty_list(self):
        self.assertEqual(lpt.matching_labels("Set overlay default position to y=75%"), [])


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
                "ISSUE_TITLE": "Fix the CI pipeline",
            },
        )
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()

    def test_exit_0_when_token_missing(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
            result = lpt.main()
        self.assertEqual(result, 0)

    def test_exit_0_when_repository_missing(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": ""}):
            result = lpt.main()
        self.assertEqual(result, 0)

    def test_exit_0_when_issue_number_missing(self):
        with patch.dict(os.environ, {"ISSUE_NUMBER": ""}):
            result = lpt.main()
        self.assertEqual(result, 0)

    def test_exit_0_when_issue_number_non_integer(self):
        with patch.dict(os.environ, {"ISSUE_NUMBER": "abc"}):
            result = lpt.main()
        self.assertEqual(result, 0)

    def test_no_api_call_when_title_matches_nothing(self):
        with patch.dict(os.environ, {"ISSUE_TITLE": "Set overlay default position"}):
            with patch.object(lpt, "gh_api") as mock_api:
                result = lpt.main()
        self.assertEqual(result, 0)
        mock_api.assert_not_called()

    def test_posts_matching_labels(self):
        with patch.object(lpt, "gh_api") as mock_api:
            result = lpt.main()
        self.assertEqual(result, 0)
        mock_api.assert_called_once()
        args, kwargs = mock_api.call_args
        self.assertEqual(args[0], "repos/owner/repo/issues/42/labels")
        self.assertEqual(kwargs["method"], "POST")
        self.assertEqual(kwargs["body"], {"labels": ["ci"]})

    def test_posts_multiple_matching_labels(self):
        with patch.dict(
            os.environ, {"ISSUE_TITLE": "Add CiWatcher agent and fix Reviewer CI-polling loop"}
        ):
            with patch.object(lpt, "gh_api") as mock_api:
                result = lpt.main()
        self.assertEqual(result, 0)
        body = mock_api.call_args[1]["body"]
        self.assertIn("ci", body["labels"])
        self.assertIn("agents", body["labels"])

    def test_handles_http_error_gracefully(self):
        error = urllib.error.HTTPError(url=None, code=422, msg="Unprocessable", hdrs=None, fp=None)
        with patch.object(lpt, "gh_api", side_effect=error):
            result = lpt.main()
        self.assertEqual(result, 0)

    def test_handles_url_error_gracefully(self):
        error = urllib.error.URLError("network error")
        with patch.object(lpt, "gh_api", side_effect=error):
            result = lpt.main()
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
