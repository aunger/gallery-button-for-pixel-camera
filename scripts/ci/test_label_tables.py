#!/usr/bin/env python3
"""Tests of the label tables the workflows pass to vcs-automation's label actions.

Several label actions read the same table, so the workflows hold more than one
copy of it. TestWorkflowTables fails when the copies in .github/workflows
disagree (the rules are label_tables.shared_table_problems'), and
TestSharedTableProblems feeds each rule two diverging copies to see it fail.
TestSnoozeLadder and TestTitleAndFileLabels hold this repository's own tables
to the choices its issues record.
"""

import os
import tempfile
import textwrap
import unittest

import label_tables as lt

# ---------------------------------------------------------------------------
# The workflows' own tables
# ---------------------------------------------------------------------------


class TestWorkflowTables(unittest.TestCase):
    def setUp(self):
        self.steps = lt.steps_by_action(lt.label_steps())
        self.tables = lt.tables(self.steps)

    def test_every_label_action_has_one_step(self):
        self.assertEqual(set(self.steps), set(lt.LABEL_ACTIONS))

    def test_the_copies_of_each_shared_table_agree(self):
        self.assertEqual(lt.shared_table_problems(self.tables), [])


class TestSnoozeLadder(unittest.TestCase):
    """The snooze ladder: fixed rungs (#821), short ones added in #1019."""

    def setUp(self):
        steps = lt.steps_by_action(lt.label_steps())
        self.ladder = lt.tables(steps)[lt.SNOOZE].inputs["snooze-labels"]

    def test_ladder_rungs(self):
        self.assertEqual(sorted(self.ladder.values()), [3, 7, 14, 30, 90, 180])

    def test_every_rung_is_spelled_with_its_own_duration(self):
        """The label and its days are written separately, so a typo could split them."""
        for label, days in self.ladder.items():
            with self.subTest(label=label):
                self.assertEqual(label, f"snooze {days} days")

    def test_the_legacy_hold_spelling_is_not_a_snooze_label(self):
        """The long rungs were first spelled "hold N days"; #1028 renamed them and #1029
        retired that spelling, so it must name no rung."""
        for label in self.ladder:
            with self.subTest(label=label):
                self.assertFalse(label.lower().startswith("hold "))


class TestTitleAndFileLabels(unittest.TestCase):
    def setUp(self):
        self.tables = lt.tables(lt.steps_by_action(lt.label_steps()))

    def test_the_title_labels(self):
        self.assertEqual(
            set(self.tables[lt.TITLE].inputs["rules"]), {"ci", "agents", "automated tests"}
        )

    def test_agents_is_the_only_label_the_files_decide_alone(self):
        self.assertEqual(lt.any_file_labels(self.tables[lt.FILES].inputs["rules"]), {"agents"})


# ---------------------------------------------------------------------------
# shared_table_problems, on tables written here
# ---------------------------------------------------------------------------

_PROCESS = ["orchestrate", "orchestrating", "verified"]
_SNOOZE = {"snooze 3 days": 3, "snooze 7 days": 7}
_SETS = [["p1", "p2"], ["orchestrate", "orchestrating"], list(_SNOOZE)]
_PREFIXES = ["c-a-"]


def _good() -> dict[str, dict[str, object]]:
    """Return a consistent set of tables, keyed by action and then input."""
    exclusive = {"exclusive-sets": _SETS, "exclusive-prefixes": _PREFIXES}
    return {
        lt.TITLE: {"rules": {"agents": "agent"}, "skip-on-pull-requests": ["agents"]},
        lt.FILES: {
            "rules": {
                "ci": {"mode": "every-file", "paths": ["ci/**"]},
                "agents": {"mode": "any-file", "paths": ["AGENTS.md"]},
            }
        },
        lt.ENFORCE: dict(exclusive),
        lt.GATE: {"blocking-labels": ["orchestrating"]},
        lt.SNOOZE: {"snooze-labels": dict(_SNOOZE)},
        lt.WAKE: {"snooze-labels": dict(_SNOOZE), "remove-on-wake": list(_PROCESS)},
        lt.PROPAGATE: {"excluded-labels": [*_PROCESS, "agents"], **exclusive},
        lt.RECONCILE: {"excluded-labels": [*_PROCESS, "agents"], **exclusive},
    }


def _problems(tables: dict[str, dict[str, object]]) -> list[str]:
    return lt.shared_table_problems(
        {action: lt.Tables(f"{action}.yml", inputs) for action, inputs in tables.items()}
    )


class TestSharedTableProblems(unittest.TestCase):
    def assertOneProblemNaming(self, tables, *workflows):
        problems = _problems(tables)
        self.assertEqual(len(problems), 1, problems)
        for workflow in workflows:
            self.assertIn(workflow, problems[0])

    def test_consistent_tables_have_no_problem(self):
        self.assertEqual(_problems(_good()), [])

    def test_case_spacing_and_order_are_not_differences(self):
        tables = _good()
        tables[lt.RECONCILE]["exclusive-sets"] = [
            [" P2", "p1"],
            list(reversed(_SNOOZE)),
            ["Orchestrating", "orchestrate"],
        ]
        tables[lt.RECONCILE]["excluded-labels"] = ["Agents ", *reversed(_PROCESS)]
        tables[lt.WAKE]["snooze-labels"] = {"Snooze 7 Days": 7, "snooze 3 days": 3}
        self.assertEqual(_problems(tables), [])

    def test_exclusive_sets_that_differ(self):
        tables = _good()
        tables[lt.RECONCILE]["exclusive-sets"] = _SETS[:2]
        problems = _problems(tables)
        self.assertTrue(problems)
        self.assertIn("exclusive-sets differs", problems[0])
        self.assertIn(f"{lt.ENFORCE}.yml", problems[0])
        self.assertIn(f"{lt.RECONCILE}.yml", problems[0])

    def test_exclusive_sets_missing_from_one_copy(self):
        """A missing optional input reads as [], so it differs rather than passing."""
        tables = _good()
        tables[lt.PROPAGATE]["exclusive-sets"] = []
        problems = _problems(tables)
        self.assertIn(f"{lt.PROPAGATE}.yml", problems[0])

    def test_exclusive_prefixes_that_differ(self):
        tables = _good()
        tables[lt.PROPAGATE]["exclusive-prefixes"] = ["c-a-", "c-r-"]
        self.assertOneProblemNaming(tables, f"{lt.ENFORCE}.yml", f"{lt.PROPAGATE}.yml")

    def test_excluded_labels_that_differ(self):
        tables = _good()
        tables[lt.RECONCILE]["excluded-labels"] = [*_PROCESS, "agents", "p1"]
        # The extra label also breaks reconcile's tie to remove-on-wake.
        first, second = _problems(tables)
        self.assertIn("excluded-labels differs", first)
        self.assertIn(f"{lt.PROPAGATE}.yml", first)
        self.assertIn(f"{lt.RECONCILE}.yml", first)
        self.assertIn("remove-on-wake", second)

    def test_snooze_labels_that_differ(self):
        tables = _good()
        tables[lt.WAKE]["snooze-labels"] = {"snooze 3 days": 3, "snooze 7 days": 8}
        self.assertOneProblemNaming(tables, f"{lt.SNOOZE}.yml", f"{lt.WAKE}.yml")

    def test_snooze_labels_that_are_not_one_exclusive_set(self):
        tables = _good()
        ladder = {**_SNOOZE, "snooze 14 days": 14}
        tables[lt.SNOOZE]["snooze-labels"] = dict(ladder)
        tables[lt.WAKE]["snooze-labels"] = dict(ladder)
        self.assertOneProblemNaming(tables, f"{lt.SNOOZE}.yml", f"{lt.ENFORCE}.yml")

    def test_remove_on_wake_that_differs_from_the_excluded_process_labels(self):
        tables = _good()
        tables[lt.WAKE]["remove-on-wake"] = _PROCESS[:2]
        problems = _problems(tables)
        self.assertEqual(len(problems), 2, problems)
        for problem, copy in zip(problems, (lt.PROPAGATE, lt.RECONCILE), strict=True):
            self.assertIn(f"{lt.WAKE}.yml", problem)
            self.assertIn(f"{copy}.yml", problem)

    def test_an_any_file_label_that_is_not_excluded(self):
        tables = _good()
        for copy in (lt.PROPAGATE, lt.RECONCILE):
            tables[copy]["excluded-labels"] = list(_PROCESS)
        problems = _problems(tables)
        self.assertEqual(len(problems), 2, problems)
        for problem in problems:
            self.assertIn("lacks the any-file labels", problem)
            self.assertIn(f"{lt.FILES}.yml", problem)

    def test_skip_on_pull_requests_that_is_not_the_any_file_labels(self):
        tables = _good()
        tables[lt.TITLE]["skip-on-pull-requests"] = []
        self.assertOneProblemNaming(tables, f"{lt.TITLE}.yml", f"{lt.FILES}.yml")


# ---------------------------------------------------------------------------
# Reading the steps
# ---------------------------------------------------------------------------

_SHA = "3a677839fad0d24f69887ad547a3f911b9d876e3"


def _step(action: str, inputs: dict[str, str] | None = None) -> lt.LabelStep:
    return lt.LabelStep(f"{action}.yml", "job", action, _SHA, inputs or {})


class TestLabelSteps(unittest.TestCase):
    def test_reads_label_steps_and_skips_the_rest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "w.yml")
            with open(path, "w", encoding="utf-8") as f:
                f.write(
                    textwrap.dedent(
                        f"""\
                        jobs:
                          one:
                            steps:
                              - uses: actions/checkout@v7
                              - uses: aunger/vcs-automation/archive-stale-test-failures@{_SHA}
                              - name: Snooze
                                uses: aunger/vcs-automation/snooze-issues@{_SHA} # v1.2.0
                                with:
                                  snooze-labels: |
                                    {{"snooze 3 days": 3}}
                              - run: echo
                        """
                    )
                )
            (step,) = lt.label_steps([path])
        self.assertEqual(step.action, lt.SNOOZE)
        self.assertEqual(step.ref, _SHA)
        self.assertEqual(step.job, "one")
        self.assertEqual(step.inputs, {"snooze-labels": '{"snooze 3 days": 3}\n'})


class TestStepsByAction(unittest.TestCase):
    def _every(self) -> list[lt.LabelStep]:
        return [_step(action) for action in lt.LABEL_ACTIONS]

    def test_one_step_per_action(self):
        self.assertEqual(set(lt.steps_by_action(self._every())), set(lt.LABEL_ACTIONS))

    def test_a_missing_action_fails_rather_than_passing_by_absence(self):
        steps = [step for step in self._every() if step.action != lt.WAKE]
        with self.assertRaisesRegex(lt.LabelTableError, lt.WAKE):
            lt.steps_by_action(steps)

    def test_a_repeated_action_fails(self):
        with self.assertRaisesRegex(lt.LabelTableError, lt.GATE):
            lt.steps_by_action([*self._every(), _step(lt.GATE)])


class TestTable(unittest.TestCase):
    def test_parses_json(self):
        self.assertEqual(
            lt.table(_step(lt.GATE, {"blocking-labels": '["a"]'}), "blocking-labels"), ["a"]
        )

    def test_a_missing_optional_input_is_its_default(self):
        self.assertEqual(lt.table(_step(lt.WAKE, {}), "remove-on-wake"), [])

    def test_a_missing_required_input_fails(self):
        for action, name in (
            (lt.FILES, "rules"),
            (lt.SNOOZE, "snooze-labels"),
            (lt.GATE, "blocking-labels"),
        ):
            with self.subTest(action=action), self.assertRaisesRegex(lt.LabelTableError, name):
                lt.table(_step(action, {name: "  \n"}), name)

    def test_text_that_is_not_json_fails(self):
        with self.assertRaisesRegex(lt.LabelTableError, "not JSON"):
            lt.table(_step(lt.GATE, {"blocking-labels": "[a]"}), "blocking-labels")

    def test_tables_reads_every_table_input_and_nothing_else(self):
        step = _step(lt.GATE, {"blocking-labels": '["a"]', "removed-labels": "${{ x }}"})
        (found,) = lt.tables({lt.GATE: step}).values()
        self.assertEqual(found.inputs, {"blocking-labels": ["a"]})


if __name__ == "__main__":
    unittest.main()
