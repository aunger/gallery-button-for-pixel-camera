#!/usr/bin/env python3
"""Tests of administrative-merge-holds.yml's own wiring: its triggers and steps.

The other label workflows' steps and tables are tested in test_label_tables.py;
nothing tests their triggers.
"""

import os
import unittest

from workflow_files import REPO_ROOT, load_workflow

_WORKFLOW_PATH = os.path.join(REPO_ROOT, ".github", "workflows", "administrative-merge-holds.yml")


class TestWorkflowTriggers(unittest.TestCase):
    """The gate's verdict only reaches the commit when an event re-runs it.

    Every source the gate reads can change without the PR that fired the
    last run being touched, so the trigger list is part of this gate's
    behavior rather than incidental configuration.
    """

    def _triggers(self) -> dict:
        workflow = load_workflow(_WORKFLOW_PATH)
        # YAML 1.1 resolves a bare `on` key to the boolean True, which is what
        # PyYAML hands back for a GitHub workflow's trigger block.
        return workflow[True] if True in workflow else workflow["on"]

    def _trigger_types(self) -> set[str]:
        return set(self._triggers()["pull_request"]["types"])

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

    def test_issue_labels_are_enforced_too(self):
        """Step 1 keeps issues' exclusive sets too, the snooze ladder among them."""
        self.assertEqual(self._triggers()["issues"]["types"], ["labeled"])


class TestMergedJobWiring(unittest.TestCase):
    """The gate shares one job with label enforcement (issue #1241)."""

    _GATE_STEP = "Red to hold merge, not a failure"
    _ENFORCE_STEP = "Enforce mutually exclusive label sets"

    def setUp(self):
        workflow = load_workflow(_WORKFLOW_PATH)
        (self.job,) = workflow["jobs"].values()
        self.steps = {step["name"]: (i, step) for i, step in enumerate(self.job["steps"])}

    def test_job_name_is_the_required_check_name(self):
        self.assertEqual(self.job["name"], "Administrative merge holds (not failure)")

    def test_each_step_uses_its_vcs_automation_action(self):
        for name, action in (
            (self._ENFORCE_STEP, "enforce-mutually-exclusive-labels"),
            (self._GATE_STEP, "check-blocking-labels"),
        ):
            with self.subTest(step=name):
                self.assertRegex(
                    self.steps[name][1].get("uses", ""),
                    r"^aunger/vcs-automation/%s@[0-9a-f]{40}$" % action,
                )

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

    def test_gate_drops_only_what_enforcement_removed(self):
        """The gate narrows the event-payload labels only by what step 1 reports removing.

        The action reads the payload itself, so the label that fired a
        `labeled` event still counts; what this workflow wires is the
        `removed` output into `removed-labels`.
        """
        enforce = self.steps[self._ENFORCE_STEP][1]
        gate = self.steps[self._GATE_STEP][1]
        self.assertEqual(
            gate["with"]["removed-labels"], "${{ steps.%s.outputs.removed }}" % enforce["id"]
        )

    def test_no_step_hides_a_failure(self):
        for _, step in self.steps.values():
            self.assertNotIn("continue-on-error", step)


if __name__ == "__main__":
    unittest.main()
