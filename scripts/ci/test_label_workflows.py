#!/usr/bin/env python3
"""Tests of the label workflows' own wiring: their triggers and steps."""

import os
import unittest

import yaml
from workflow_files import REPO_ROOT

_WORKFLOW_PATH = os.path.join(REPO_ROOT, ".github", "workflows", "administrative-merge-holds.yml")


class TestWorkflowTriggers(unittest.TestCase):
    """The script's verdict only reaches the commit when an event re-runs it.

    Every source the script reads can change without the PR that fired the
    last run being touched, so the trigger list is part of this gate's
    behavior rather than incidental configuration.
    """

    def _trigger_types(self) -> set[str]:
        with open(_WORKFLOW_PATH, encoding="utf-8") as f:
            workflow = yaml.safe_load(f)
        # YAML 1.1 resolves a bare `on` key to the boolean True, which is what
        # PyYAML hands back for a GitHub workflow's trigger block.
        triggers = workflow[True] if True in workflow else workflow["on"]
        return set(triggers["pull_request"]["types"])

    def test_every_event_that_can_change_the_verdict_is_a_trigger(self):
        self.assertEqual(
            self._trigger_types(),
            {"opened", "reopened", "synchronize", "labeled", "unlabeled", "closed"},
        )

    def test_closed_is_a_trigger(self):
        """Closing a blocked PR must re-evaluate the commit it was blocking.

        The failing check run a PR earned is stored on the commit and stays
        the most recent run of this check there after the PR is closed, so
        without this event a sibling stays blocked with no blocking label
        applied anywhere and no automatic way out.
        """
        self.assertIn("closed", self._trigger_types())


class TestMergedJobWiring(unittest.TestCase):
    """The gate shares one job with label enforcement (issue #1241)."""

    _GATE_STEP = "Red to hold merge, not a failure"
    _ENFORCE_STEP = "Enforce mutually exclusive label sets"

    def setUp(self):
        with open(_WORKFLOW_PATH, encoding="utf-8") as f:
            workflow = yaml.safe_load(f)
        (self.job,) = workflow["jobs"].values()
        self.steps = {step["name"]: (i, step) for i, step in enumerate(self.job["steps"])}

    def test_job_name_is_the_required_check_name(self):
        self.assertEqual(self.job["name"], "Administrative merge holds (not failure)")

    def test_enforcement_runs_before_the_gate(self):
        """Enforcement can remove a blocking label, so the gate must see the result."""
        self.assertLess(self.steps[self._ENFORCE_STEP][0], self.steps[self._GATE_STEP][0])

    def test_enforcement_runs_only_on_labeled(self):
        self.assertEqual(
            self.steps[self._ENFORCE_STEP][1]["if"], "github.event.action == 'labeled'"
        )

    def test_gate_runs_only_on_pull_requests(self):
        self.assertEqual(
            self.steps[self._GATE_STEP][1]["if"], "github.event_name == 'pull_request'"
        )

    def test_gate_counts_the_payload_minus_what_enforcement_removed(self):
        """The payload keeps the label that fired the event; only removals narrow it.

        A run-time API read of the PR's labels could lag the `labeled` event
        that started the run and open the gate, so the payload stays the
        source and the gate drops only the labels step 1 reports removing.
        """
        enforce = self.steps[self._ENFORCE_STEP][1]
        gate = self.steps[self._GATE_STEP][1]
        self.assertEqual(
            gate["env"]["PR_LABELS"], "${{ toJson(github.event.pull_request.labels.*.name) }}"
        )
        self.assertEqual(
            gate["env"]["REMOVED_LABELS"], "${{ steps.%s.outputs.removed }}" % enforce["id"]
        )

    def test_no_step_hides_a_failure(self):
        for _, step in self.steps.values():
            self.assertNotIn("continue-on-error", step)


if __name__ == "__main__":
    unittest.main()
