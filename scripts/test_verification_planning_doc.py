#!/usr/bin/env python3
"""Tests that the Verification Planner doc keeps surfacing out-of-repo reviewer requests.

The Verification Planner is the final gate before merge. Issue #319 made it
responsible for surfacing reviewer-requested changes that are not file edits
(e.g. an issue that must be filed) on a "Before merging" checklist, alongside
the unautomated verification steps it already tracked. These tests guard that
responsibility against silent regression of the prose contract.
"""

import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLANNER_DOC = os.path.join(REPO_ROOT, "agents", "verification_planning.md")
ORCHESTRATION_DOC = os.path.join(REPO_ROOT, "agents", "dev_orchestration.md")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class VerificationPlannerDocTest(unittest.TestCase):
    def setUp(self) -> None:
        self.doc = _read(PLANNER_DOC)
        self.lower = self.doc.lower()

    def test_still_tracks_unautomated_verification_steps(self) -> None:
        # The original responsibility must not be dropped by the expansion.
        self.assertIn("unautomated verification steps", self.lower)

    def test_surfaces_out_of_repo_reviewer_requests(self) -> None:
        self.assertIn("out-of-repo reviewer requests", self.lower)

    def test_calls_out_filing_an_issue_as_an_example(self) -> None:
        # The issue's motivating example: a change that is not a file edit, such
        # as an issue that needs to be filed.
        self.assertRegex(self.lower, r"not\s+(?:a\s+)?file edits?")
        self.assertRegex(self.lower, r"fil(?:e|ing)[^\n]*issue")

    def test_has_a_before_merging_checklist(self) -> None:
        self.assertIn("before merging", self.lower)
        self.assertIn("checklist", self.lower)

    def test_planner_does_not_discharge_the_items_itself(self) -> None:
        # It surfaces the work; it must not file the issues or modify sources.
        self.assertIn("do not modify source files", self.lower)
        self.assertRegex(
            self.lower, r"do not file[^\n]*yourself|surface them so the responsible party acts"
        )


class OrchestrationWiringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.doc = _read(ORCHESTRATION_DOC).lower()

    def test_dispatch_sentence_mentions_out_of_repo_requests(self) -> None:
        # The verification-planner role-assignment sentence must advertise the
        # broadened scope so dispatched planners know to look for it.
        match = re.search(
            r"verification planner:\s*\"(?P<sentence>[^\"]+)\"", self.doc
        )
        self.assertIsNotNone(match, "verification-planner dispatch sentence not found")
        sentence = match.group("sentence")
        self.assertIn("out-of-repo reviewer requests", sentence)
        self.assertIn("before merging checklist", sentence)

    def test_clear_step_references_before_merging_checklist(self) -> None:
        self.assertIn("before merging checklist", self.doc)


if __name__ == "__main__":
    unittest.main()
