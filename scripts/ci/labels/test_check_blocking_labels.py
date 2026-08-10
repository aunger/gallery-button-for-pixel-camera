#!/usr/bin/env python3
"""Unit tests for check_blocking_labels.py."""

import io
import json
import os
import sys
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import check_blocking_labels as cbl  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SHA = "43a3ce7" + "0" * 33
_OTHER_SHA = "b" * 40


def _pr(number: int, labels: list[str], head_sha: str = _SHA) -> dict:
    """Build the subset of a GitHub pull request object this script reads."""
    return {
        "number": number,
        "head": {"sha": head_sha},
        "labels": [{"name": lbl} for lbl in labels],
    }


def _env(
    pr_number: int = 808,
    pr_state: str = "open",
    pr_labels: list[str] | None = None,
    head_sha: str = _SHA,
) -> dict[str, str]:
    return {
        "GITHUB_TOKEN": "tok",
        "GITHUB_REPOSITORY": "aunger/gallery-button-for-pixel-camera",
        "HEAD_SHA": head_sha,
        "PR_NUMBER": str(pr_number),
        "PR_STATE": pr_state,
        "PR_LABELS": json.dumps(pr_labels if pr_labels is not None else []),
    }


def _run_main(env: dict[str, str], pages: list[list[dict]] | Exception) -> tuple[int, str]:
    """Run main() with *env* and a gh_api that serves *pages* (or raises).

    Returns (exit code, combined stdout+stderr).
    """
    out, err = io.StringIO(), io.StringIO()
    side_effect = pages if isinstance(pages, Exception) else list(pages)
    with patch.dict(os.environ, env, clear=True):
        with patch.object(cbl.emxl, "gh_api", side_effect=side_effect):
            with redirect_stdout(out), redirect_stderr(err):
                code = cbl.main()
    return code, out.getvalue() + err.getvalue()


# ---------------------------------------------------------------------------
# blocking_labels_in
# ---------------------------------------------------------------------------


class TestBlockingLabelsIn(unittest.TestCase):
    def test_no_labels_is_not_blocking(self):
        self.assertEqual(cbl.blocking_labels_in([]), [])

    def test_non_blocking_labels_are_ignored(self):
        self.assertEqual(cbl.blocking_labels_in(["ci", "p1", "verified", "orchestrate"]), [])

    def test_each_blocking_label_is_detected(self):
        for label in ("verification needed", "changes requested", "changes done", "orchestrating"):
            with self.subTest(label=label):
                self.assertEqual(cbl.blocking_labels_in(["ci", label]), [label])

    def test_matching_is_case_insensitive(self):
        self.assertEqual(cbl.blocking_labels_in(["Verification Needed"]), ["verification needed"])

    def test_multiple_blocking_labels_are_sorted_and_deduplicated(self):
        found = cbl.blocking_labels_in(["orchestrating", "changes done", "ORCHESTRATING"])
        self.assertEqual(found, ["changes done", "orchestrating"])


# ---------------------------------------------------------------------------
# fetch_open_prs_at_head
# ---------------------------------------------------------------------------


class TestFetchOpenPrsAtHead(unittest.TestCase):
    def test_keeps_only_prs_whose_head_is_the_commit(self):
        page = [_pr(808, []), _pr(900, [], head_sha=_OTHER_SHA), _pr(832, [])]
        with patch.object(cbl.emxl, "gh_api", side_effect=[page]) as gh_api:
            found = cbl.fetch_open_prs_at_head("o/r", "tok", _SHA)
        self.assertEqual([pr["number"] for pr in found], [808, 832])
        self.assertIn("state=open", gh_api.call_args[0][0])

    def test_paginates_until_a_short_page(self):
        full_page = [_pr(n, [], head_sha=_OTHER_SHA) for n in range(100)]
        second_page = [_pr(832, [])]
        with patch.object(cbl.emxl, "gh_api", side_effect=[full_page, second_page]) as gh_api:
            found = cbl.fetch_open_prs_at_head("o/r", "tok", _SHA)
        self.assertEqual([pr["number"] for pr in found], [832])
        self.assertEqual(gh_api.call_count, 2)
        self.assertIn("page=2", gh_api.call_args[0][0])

    def test_stops_on_an_empty_page(self):
        full_page = [_pr(n, [], head_sha=_OTHER_SHA) for n in range(100)]
        with patch.object(cbl.emxl, "gh_api", side_effect=[full_page, []]) as gh_api:
            found = cbl.fetch_open_prs_at_head("o/r", "tok", _SHA)
        self.assertEqual(found, [])
        self.assertEqual(gh_api.call_count, 2)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain(unittest.TestCase):
    def test_passes_when_no_pr_at_the_commit_is_blocked(self):
        code, output = _run_main(_env(pr_labels=["ci"]), [[_pr(808, ["ci"])]])
        self.assertEqual(code, 0)
        self.assertIn("OK: No blocking labels are present.", output)

    def test_fails_when_the_triggering_pr_is_blocked(self):
        code, output = _run_main(
            _env(pr_labels=["verification needed"]),
            [[_pr(808, ["verification needed"])]],
        )
        self.assertEqual(code, 1)
        self.assertIn("ERROR: This PR has a blocking label: verification needed", output)

    def test_fails_when_a_sibling_pr_at_the_same_commit_is_blocked(self):
        """Issue #833: a clean test PR must not answer the gate for a blocked sibling.

        PR #832 was opened from #808's head, so both check runs land on the
        same commit. Evaluating #832's labels alone reported success and
        unblocked #808 while it still carried `verification needed`.
        """
        code, output = _run_main(
            _env(pr_number=832, pr_labels=[]),
            [[_pr(808, ["verification needed"]), _pr(832, [])]],
        )
        self.assertEqual(code, 1)
        self.assertIn(
            "ERROR: PR #808, which shares this head commit, has a blocking label:"
            " verification needed",
            output,
        )

    def test_a_blocked_pr_at_a_different_commit_does_not_block(self):
        code, output = _run_main(
            _env(pr_labels=[]),
            [[_pr(808, []), _pr(900, ["orchestrating"], head_sha=_OTHER_SHA)]],
        )
        self.assertEqual(code, 0)
        self.assertIn("OK: No blocking labels are present.", output)

    def test_reports_every_blocked_pr_at_the_commit(self):
        code, output = _run_main(
            _env(pr_number=832, pr_labels=["changes done"]),
            [[_pr(808, ["orchestrating"]), _pr(832, ["changes done"])]],
        )
        self.assertEqual(code, 1)
        self.assertIn("ERROR: This PR has a blocking label: changes done", output)
        self.assertIn(
            "ERROR: PR #808, which shares this head commit, has a blocking label: orchestrating",
            output,
        )

    def test_a_closed_triggering_pr_does_not_block_its_open_sibling(self):
        """A closed PR cannot merge, so labeling it must not block a sibling.

        Its labels arrive in the event payload (`labeled` fires on closed PRs
        too) but it is absent from the open-PR listing.
        """
        code, output = _run_main(
            _env(pr_number=832, pr_state="closed", pr_labels=["orchestrating"]),
            [[_pr(808, [])]],
        )
        self.assertEqual(code, 0)
        self.assertIn("OK: No blocking labels are present.", output)

    def test_payload_labels_block_even_if_the_listing_is_stale(self):
        """The verdict is never weaker about the triggering PR than the payload."""
        code, output = _run_main(_env(pr_labels=["changes requested"]), [[]])
        self.assertEqual(code, 1)
        self.assertIn("ERROR: This PR has a blocking label: changes requested", output)

    def test_listing_labels_block_even_if_the_payload_is_stale(self):
        """A label applied after the event fired still blocks the same PR."""
        code, output = _run_main(_env(pr_labels=[]), [[_pr(808, ["orchestrating"])]])
        self.assertEqual(code, 1)
        self.assertIn("ERROR: This PR has a blocking label: orchestrating", output)

    def test_lists_the_prs_at_the_commit(self):
        _, output = _run_main(
            _env(pr_number=832, pr_labels=[]),
            [[_pr(808, []), _pr(832, [])]],
        )
        self.assertIn(f"Open pull request(s) whose head is {_SHA}: #808, #832.", output)

    def test_fails_closed_when_the_api_call_fails(self):
        error = urllib.error.HTTPError(url=None, code=500, msg="boom", hdrs=None, fp=None)
        code, output = _run_main(_env(pr_labels=[]), error)
        self.assertEqual(code, 1)
        self.assertIn("Error listing open pull requests", output)
        self.assertIn("treated as blocked", output)

    def test_fails_when_a_required_variable_is_missing(self):
        for name in (
            "GITHUB_TOKEN",
            "GITHUB_REPOSITORY",
            "HEAD_SHA",
            "PR_NUMBER",
            "PR_STATE",
            "PR_LABELS",
        ):
            with self.subTest(missing=name):
                env = _env()
                del env[name]
                code, output = _run_main(env, [])
                self.assertEqual(code, 1)
                self.assertIn(name, output)

    def test_fails_when_pr_number_is_not_an_integer(self):
        env = _env()
        env["PR_NUMBER"] = "not-a-number"
        code, output = _run_main(env, [])
        self.assertEqual(code, 1)
        self.assertIn("PR_NUMBER is not a valid integer", output)

    def test_fails_when_pr_labels_is_not_json(self):
        env = _env()
        env["PR_LABELS"] = "verification needed"
        code, output = _run_main(env, [])
        self.assertEqual(code, 1)
        self.assertIn("PR_LABELS is not a JSON array", output)


if __name__ == "__main__":
    unittest.main()
