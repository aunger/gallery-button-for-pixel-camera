#!/usr/bin/env python3
"""Unit tests for propagate_issue_labels.py."""

import json
import os
import sys
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import propagate_issue_labels as pil  # noqa: E402


def _closing_issues_payload(issues: list[tuple[int, str, list[str]]]) -> dict:
    """Build a GraphQL response for the given (number, repo, labels) issues."""
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "closingIssuesReferences": {
                        "nodes": [
                            {
                                "number": number,
                                "repository": {"nameWithOwner": repo},
                                "labels": {"nodes": [{"name": lbl} for lbl in labels]},
                            }
                            for number, repo, labels in issues
                        ]
                    }
                }
            }
        }
    }


# ---------------------------------------------------------------------------
# graphql_query tests
# ---------------------------------------------------------------------------


class TestGraphqlQuery(unittest.TestCase):
    def _make_response(self, body: bytes) -> MagicMock:
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_returns_data_on_success(self):
        payload = {"data": {"repository": {"pullRequest": None}}}
        response = self._make_response(json.dumps(payload).encode())
        with patch("urllib.request.urlopen", return_value=response):
            result = pil.graphql_query("query {}", {}, "tok")
        self.assertEqual(result, payload["data"])

    def test_raises_on_graphql_errors(self):
        payload = {"errors": [{"message": "Could not resolve to a PullRequest"}]}
        response = self._make_response(json.dumps(payload).encode())
        with patch("urllib.request.urlopen", return_value=response):
            with self.assertRaises(RuntimeError):
                pil.graphql_query("query {}", {}, "tok")

    def test_sends_bearer_auth_header(self):
        payload = {"data": {}}
        response = self._make_response(json.dumps(payload).encode())
        captured = {}

        def fake_urlopen(req):
            captured["request"] = req
            return response

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            pil.graphql_query("query {}", {"a": 1}, "sekret")

        self.assertEqual(captured["request"].get_header("Authorization"), "Bearer sekret")
        self.assertEqual(captured["request"].get_full_url(), "https://api.github.com/graphql")


# ---------------------------------------------------------------------------
# fetch_closing_issue_labels tests
# ---------------------------------------------------------------------------


class TestFetchClosingIssueLabels(unittest.TestCase):
    def test_returns_labels_from_same_repo_issues(self):
        payload = _closing_issues_payload(
            [(1, "owner/repo", ["p1", "bug"]), (2, "owner/repo", ["ci"])]
        )
        with patch.object(pil, "graphql_query", return_value=payload["data"]):
            result = pil.fetch_closing_issue_labels("owner", "repo", 42, "tok")
        self.assertEqual(result, ["p1", "bug", "ci"])

    def test_skips_cross_repo_issues(self):
        payload = _closing_issues_payload(
            [(1, "owner/repo", ["p1"]), (2, "other-owner/other-repo", ["p2"])]
        )
        with patch.object(pil, "graphql_query", return_value=payload["data"]):
            result = pil.fetch_closing_issue_labels("owner", "repo", 42, "tok")
        self.assertEqual(result, ["p1"])

    def test_returns_empty_when_no_pull_request(self):
        data = {"repository": {"pullRequest": None}}
        with patch.object(pil, "graphql_query", return_value=data):
            result = pil.fetch_closing_issue_labels("owner", "repo", 42, "tok")
        self.assertEqual(result, [])

    def test_returns_empty_when_no_closing_issues(self):
        data = {"repository": {"pullRequest": {"closingIssuesReferences": {"nodes": []}}}}
        with patch.object(pil, "graphql_query", return_value=data):
            result = pil.fetch_closing_issue_labels("owner", "repo", 42, "tok")
        self.assertEqual(result, [])

    def test_returns_empty_when_closing_issue_has_no_labels(self):
        payload = _closing_issues_payload([(1, "owner/repo", [])])
        with patch.object(pil, "graphql_query", return_value=payload["data"]):
            result = pil.fetch_closing_issue_labels("owner", "repo", 42, "tok")
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# labels_to_propagate tests
# ---------------------------------------------------------------------------


class TestLabelsToPropagate(unittest.TestCase):
    def test_propagates_non_conflicting_labels(self):
        to_add, skipped, excluded = pil.labels_to_propagate([], ["p1", "bug"])
        self.assertEqual(to_add, ["p1", "bug"])
        self.assertEqual(skipped, [])
        self.assertEqual(excluded, [])

    def test_skips_labels_already_on_pr(self):
        to_add, skipped, excluded = pil.labels_to_propagate(["bug"], ["bug", "p1"])
        self.assertEqual(to_add, ["p1"])
        self.assertEqual(skipped, [])
        self.assertEqual(excluded, [])

    def test_already_present_check_is_case_insensitive(self):
        to_add, skipped, excluded = pil.labels_to_propagate(["Bug"], ["bug", "p1"])
        self.assertEqual(to_add, ["p1"])
        self.assertEqual(skipped, [])
        self.assertEqual(excluded, [])

    def test_deduplicates_labels_from_multiple_issues(self):
        to_add, skipped, excluded = pil.labels_to_propagate([], ["p1", "p1", "bug"])
        self.assertEqual(to_add, ["p1", "bug"])
        self.assertEqual(skipped, [])
        self.assertEqual(excluded, [])

    def test_skips_label_that_would_evict_existing_pr_label_fixed_set(self):
        # PR already carries p2; propagating p1 would let the mutual-exclusion
        # workflow remove p2, so p1 must be skipped instead.
        to_add, skipped, excluded = pil.labels_to_propagate(["p2"], ["p1"])
        self.assertEqual(to_add, [])
        self.assertEqual(skipped, ["p1"])
        self.assertEqual(excluded, [])

    def test_skips_label_that_would_evict_existing_pr_label_prefix_group(self):
        to_add, skipped, excluded = pil.labels_to_propagate(["c-a-opus"], ["c-a-haiku"])
        self.assertEqual(to_add, [])
        self.assertEqual(skipped, ["c-a-haiku"])
        self.assertEqual(excluded, [])

    def test_non_conflicting_prefix_label_still_propagates(self):
        to_add, skipped, excluded = pil.labels_to_propagate(["c-a-opus"], ["c-r-haiku"])
        self.assertEqual(to_add, ["c-r-haiku"])
        self.assertEqual(skipped, [])
        self.assertEqual(excluded, [])

    def test_first_accepted_candidate_blocks_a_later_conflicting_one(self):
        # No real PR label yet, but p1 is accepted first (from one linked
        # issue) and then blocks p2 (from another linked issue) within the
        # same run.
        to_add, skipped, excluded = pil.labels_to_propagate([], ["p1", "p2"])
        self.assertEqual(to_add, ["p1"])
        self.assertEqual(skipped, ["p2"])
        self.assertEqual(excluded, [])

    def test_labels_outside_any_exclusive_group_never_conflict(self):
        # "agents" was one of the examples here until issue #785 made it
        # file-determined and therefore never propagated at all; "bug" keeps
        # this case about mutual exclusion alone.
        to_add, skipped, excluded = pil.labels_to_propagate(["automated tests"], ["ci", "bug"])
        self.assertEqual(to_add, ["ci", "bug"])
        self.assertEqual(skipped, [])
        self.assertEqual(excluded, [])

    def test_empty_issue_labels_is_a_no_op(self):
        to_add, skipped, excluded = pil.labels_to_propagate(["p1"], [])
        self.assertEqual(to_add, [])
        self.assertEqual(skipped, [])
        self.assertEqual(excluded, [])

    # -- PROCESS_STATE_LABELS exclusion (issue #621 review, PR #713) --------

    def test_excludes_orchestrating_even_with_no_pr_side_conflict(self):
        # Reproduces the live bug: the PR carries none of {orchestrate,
        # orchestrating}, so the old mutual-exclusion-only guard would have
        # let this through and re-blocked "No blocking labels".
        to_add, skipped, excluded = pil.labels_to_propagate([], ["orchestrating"])
        self.assertEqual(to_add, [])
        self.assertEqual(skipped, [])
        self.assertEqual(excluded, ["orchestrating"])

    def test_excludes_every_process_state_label(self):
        for label in sorted(pil.PROCESS_STATE_LABELS):
            with self.subTest(label=label):
                to_add, skipped, excluded = pil.labels_to_propagate([], [label])
                self.assertEqual(to_add, [])
                self.assertEqual(skipped, [])
                self.assertEqual(excluded, [label])

    def test_process_state_exclusion_is_case_insensitive(self):
        to_add, skipped, excluded = pil.labels_to_propagate([], ["Orchestrating"])
        self.assertEqual(to_add, [])
        self.assertEqual(excluded, ["Orchestrating"])

    def test_process_state_labels_excluded_alongside_ordinary_labels(self):
        to_add, skipped, excluded = pil.labels_to_propagate([], ["p1", "orchestrating", "ci"])
        self.assertEqual(to_add, ["p1", "ci"])
        self.assertEqual(skipped, [])
        self.assertEqual(excluded, ["orchestrating"])

    def test_process_state_label_deduplicated_across_issues(self):
        to_add, skipped, excluded = pil.labels_to_propagate([], ["orchestrating", "orchestrating"])
        self.assertEqual(excluded, ["orchestrating"])

    # -- FILE_DETERMINED_LABELS exclusion (issue #785) -----------------------

    def test_excludes_agents_from_propagation(self):
        to_add, skipped, excluded = pil.labels_to_propagate([], ["agents", "p1"])
        self.assertEqual(to_add, ["p1"])
        self.assertEqual(skipped, [])
        self.assertEqual(excluded, ["agents"])

    def test_excludes_agents_even_when_the_pr_already_carries_it(self):
        # Ordered ahead of the already-present check, matching how
        # PROCESS_STATE_LABELS is handled today.
        to_add, skipped, excluded = pil.labels_to_propagate(["agents"], ["agents"])
        self.assertEqual(to_add, [])
        self.assertEqual(excluded, ["agents"])

    def test_file_determined_exclusion_is_case_insensitive(self):
        to_add, skipped, excluded = pil.labels_to_propagate([], ["Agents"])
        self.assertEqual(to_add, [])
        self.assertEqual(excluded, ["Agents"])

    def test_file_determined_and_process_state_sets_are_disjoint(self):
        # propagate_to_pr partitions `excluded` by these two sets for
        # logging, which is only total if nothing belongs to both.
        self.assertEqual(pil.FILE_DETERMINED_LABELS & pil.PROCESS_STATE_LABELS, frozenset())

    def test_process_state_exclusion_does_not_consume_a_skip_slot(self):
        # A process-state label and a genuinely conflicting label in the same
        # run are reported in their own distinct lists, not conflated.
        to_add, skipped, excluded = pil.labels_to_propagate(["p2"], ["orchestrating", "p1"])
        self.assertEqual(to_add, [])
        self.assertEqual(skipped, ["p1"])
        self.assertEqual(excluded, ["orchestrating"])


# ---------------------------------------------------------------------------
# main() tests
# ---------------------------------------------------------------------------


class TestMain(unittest.TestCase):
    def _env(self, **overrides):
        env = {
            "GITHUB_TOKEN": "tok",
            "GITHUB_REPOSITORY": "owner/repo",
            "PR_NUMBER": "42",
        }
        env.update(overrides)
        return env

    def test_missing_token_returns_1(self):
        with patch.dict(os.environ, self._env(GITHUB_TOKEN=""), clear=True):
            self.assertEqual(pil.main(), 1)

    def test_missing_repo_returns_1(self):
        with patch.dict(os.environ, self._env(GITHUB_REPOSITORY=""), clear=True):
            self.assertEqual(pil.main(), 1)

    def test_missing_pr_number_returns_1(self):
        with patch.dict(os.environ, self._env(PR_NUMBER=""), clear=True):
            self.assertEqual(pil.main(), 1)

    def test_malformed_repo_returns_1(self):
        with patch.dict(os.environ, self._env(GITHUB_REPOSITORY="not-a-slug"), clear=True):
            self.assertEqual(pil.main(), 1)

    def test_non_integer_pr_number_returns_1(self):
        with patch.dict(os.environ, self._env(PR_NUMBER="abc"), clear=True):
            self.assertEqual(pil.main(), 1)

    def test_delegates_to_propagate_to_pr_with_parsed_args(self):
        with patch.dict(os.environ, self._env(), clear=True):
            with patch.object(pil, "propagate_to_pr", return_value=True) as mock_propagate:
                result = pil.main()

        self.assertEqual(result, 0)
        mock_propagate.assert_called_once_with("owner", "repo", "owner/repo", 42, "tok")

    def test_returns_1_when_propagate_to_pr_fails(self):
        with patch.dict(os.environ, self._env(), clear=True):
            with patch.object(pil, "propagate_to_pr", return_value=False):
                self.assertEqual(pil.main(), 1)


# ---------------------------------------------------------------------------
# propagate_to_pr tests
# ---------------------------------------------------------------------------


class TestPropagateToPr(unittest.TestCase):
    def test_graphql_failure_returns_false(self):
        with patch.object(pil, "fetch_closing_issue_labels", side_effect=RuntimeError("boom")):
            result = pil.propagate_to_pr("owner", "repo", "owner/repo", 42, "tok")
        self.assertFalse(result)

    def test_no_closing_issues_returns_true_and_does_not_touch_labels(self):
        with patch.object(pil, "fetch_closing_issue_labels", return_value=[]):
            with patch.object(pil.emxl, "gh_api") as mock_api:
                result = pil.propagate_to_pr("owner", "repo", "owner/repo", 42, "tok")
        self.assertTrue(result)
        mock_api.assert_not_called()

    def test_pr_fetch_failure_returns_false(self):
        with patch.object(pil, "fetch_closing_issue_labels", return_value=["p1"]):
            with patch.object(
                pil.emxl,
                "gh_api",
                side_effect=urllib.error.HTTPError(
                    url=None, code=404, msg="Not Found", hdrs=None, fp=None
                ),
            ):
                result = pil.propagate_to_pr("owner", "repo", "owner/repo", 42, "tok")
        self.assertFalse(result)

    def test_applies_eligible_labels(self):
        with patch.object(pil, "fetch_closing_issue_labels", return_value=["p1", "bug"]):
            with patch.object(
                pil.emxl, "gh_api", return_value={"labels": [{"name": "bug"}]}
            ) as mock_api:
                result = pil.propagate_to_pr("owner", "repo", "owner/repo", 42, "tok")

        self.assertTrue(result)
        post_calls = [c for c in mock_api.call_args_list if c.kwargs.get("method") == "POST"]
        self.assertEqual(len(post_calls), 1)
        self.assertEqual(post_calls[0].args[0], "repos/owner/repo/issues/42/labels")
        self.assertEqual(post_calls[0].kwargs["body"], {"labels": ["p1"]})

    def test_skip_only_case_returns_true_without_posting(self):
        # PR already carries p2; the only candidate (p1) conflicts and is
        # skipped, so nothing should be POSTed.
        with patch.object(pil, "fetch_closing_issue_labels", return_value=["p1"]):
            with patch.object(
                pil.emxl, "gh_api", return_value={"labels": [{"name": "p2"}]}
            ) as mock_api:
                result = pil.propagate_to_pr("owner", "repo", "owner/repo", 42, "tok")

        self.assertTrue(result)
        post_calls = [c for c in mock_api.call_args_list if c.kwargs.get("method") == "POST"]
        self.assertEqual(post_calls, [])

    def test_apply_failure_returns_false(self):
        def fake_api(path, token, method="GET", body=None):
            if method == "POST":
                raise urllib.error.HTTPError(
                    url=None, code=422, msg="Unprocessable Entity", hdrs=None, fp=None
                )
            return {"labels": []}

        with patch.object(pil, "fetch_closing_issue_labels", return_value=["p1"]):
            with patch.object(pil.emxl, "gh_api", side_effect=fake_api):
                result = pil.propagate_to_pr("owner", "repo", "owner/repo", 42, "tok")
        self.assertFalse(result)

    def test_does_not_reapply_orchestrating_the_orchestrator_just_removed(self):
        # Reproduces the live PR #713 bug: the issue still carries
        # "orchestrating" (active orchestration), but the PR just had it
        # removed alone to clear the merge gate. A routine synchronize event
        # must not put it back.
        with patch.object(pil, "fetch_closing_issue_labels", return_value=["orchestrating", "p1"]):
            with patch.object(
                pil.emxl, "gh_api", return_value={"labels": [{"name": "ci"}]}
            ) as mock_api:
                result = pil.propagate_to_pr("owner", "repo", "owner/repo", 713, "tok")

        self.assertTrue(result)
        post_calls = [c for c in mock_api.call_args_list if c.kwargs.get("method") == "POST"]
        self.assertEqual(len(post_calls), 1)
        self.assertEqual(post_calls[0].kwargs["body"], {"labels": ["p1"]})

    def test_does_not_propagate_agents_from_a_linked_issue(self):
        # Issue #775 itself carries `agents`; a PR fixing it by touching only
        # scripts/ci/labels/ must not inherit the label, on open or on any
        # later body edit.
        with patch.object(pil, "fetch_closing_issue_labels", return_value=["agents", "p1"]):
            with patch.object(pil.emxl, "gh_api", return_value={"labels": []}) as mock_api:
                result = pil.propagate_to_pr("owner", "repo", "owner/repo", 42, "tok")

        self.assertTrue(result)
        post_calls = [c for c in mock_api.call_args_list if c.kwargs.get("method") == "POST"]
        self.assertEqual(len(post_calls), 1)
        self.assertEqual(post_calls[0].kwargs["body"], {"labels": ["p1"]})


if __name__ == "__main__":
    unittest.main()
