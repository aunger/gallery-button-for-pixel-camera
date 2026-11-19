#!/usr/bin/env python3
"""Tests of the label tables the workflows pass to vcs-automation's label actions.

Several label actions read the same table, so the workflows hold more than one
copy of it. TestWorkflowTables fails when the copies in .github/workflows
disagree (the rules are label_tables.shared_table_problems'), and
TestSharedTableProblems feeds each rule two diverging copies to see it fail.
TestSnoozeLadder and TestTitleAndFileLabels hold this repository's own tables
to the choices its issues record.

TestTablesAtThePin parses each table with vcs-automation's own parsers, at the
commit the workflows pin the action, since a table that is valid JSON can still
be one the action refuses, and three of the label workflows never run on a
pull request (snooze on issue events, wake on a schedule, reconcile on
dispatch). It also checks each step's `with:` keys against the inputs the
action declares, since GitHub only warns about an unknown input, and a
misspelled optional table would then read as its default. Where the fetch
fails the test skips with the reason, except when CI is set, where it fails.
"""

import contextlib
import importlib
import os
import sys
import tempfile
import textwrap
import unittest

import label_tables as lt
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pinned_plugin_files import (
    VCS_AUTOMATION,
    FetchFailed,
    PinnedRepository,
    vcs_automation_pin,
)

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

    def test_a_prefix_with_a_trailing_space_differs(self):
        """The actions do not strip a prefix, so neither does the comparison."""
        tables = _good()
        tables[lt.RECONCILE]["exclusive-prefixes"] = ["c-a- ", *_PREFIXES[1:]]
        self.assertOneProblemNaming(tables, f"{lt.ENFORCE}.yml", f"{lt.RECONCILE}.yml")

    def test_prefixes_that_differ_only_in_case_agree(self):
        tables = _good()
        tables[lt.RECONCILE]["exclusive-prefixes"] = [p.upper() for p in _PREFIXES]
        self.assertEqual([], _problems(tables))

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


# ---------------------------------------------------------------------------
# The tables, parsed by vcs-automation's own code at the pin
# ---------------------------------------------------------------------------

# The modules the parsers need, at their paths in vcs-automation.
_PINNED_SOURCES = (
    "shared/label_common.py",
    "shared/github_api.py",
    "shared/github_headers.py",
    "shared/https_only.py",
    "shared/path_globs.py",
    "label-by-title/label_by_title.py",
    "label-by-files/label_by_files.py",
)
# Inputs every label action declares that are not tables.
_NOT_TABLES = {"github-token", "removed-labels"}


@contextlib.contextmanager
def _pinned_modules(root):
    """Import label_common, label_by_title and label_by_files from the tree at *root*.

    The names are taken out of sys.modules for the duration, and sys.path and
    sys.modules are put back afterwards, so nothing else in this process sees
    the pinned copies or loses its own.
    """
    names = [os.path.splitext(os.path.basename(path))[0] for path in _PINNED_SOURCES]
    saved_path = list(sys.path)
    saved = {name: sys.modules.pop(name) for name in names if name in sys.modules}
    sys.path[:0] = [os.path.join(root, os.path.dirname(path)) for path in _PINNED_SOURCES]
    try:
        yield tuple(
            importlib.import_module(name)
            for name in ("label_common", "label_by_title", "label_by_files")
        )
    finally:
        for name in names:
            sys.modules.pop(name, None)
        sys.modules.update(saved)
        sys.path[:] = saved_path


def _parsers(lc, lbt, lbf):
    """Return {(action, input): the call that action makes to parse that input}."""
    by_input = {
        "exclusive-sets": lambda text: lc.parse_label_sets("exclusive-sets", text),
        "exclusive-prefixes": lambda text: lc.parse_prefixes("exclusive-prefixes", text),
        "excluded-labels": lambda text: lc.parse_label_list("excluded-labels", text),
        "remove-on-wake": lambda text: lc.parse_label_list("remove-on-wake", text),
        "skip-on-pull-requests": lambda text: lc.parse_label_list("skip-on-pull-requests", text),
        "snooze-labels": lambda text: lc.parse_snooze_labels("snooze-labels", text),
        "blocking-labels": lambda text: lc.parse_label_list("blocking-labels", text, required=True),
    }
    parsers = {
        (action, name): by_input[name]
        for action, inputs in lt.TABLE_INPUTS.items()
        for name in inputs
        if name in by_input
    }
    parsers[(lt.TITLE, "rules")] = lambda text: lbt.parse_rules(text, "rules")
    parsers[(lt.FILES, "rules")] = lambda text: lbf.parse_rules(text, "rules")
    return parsers


class TestTablesAtThePin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.steps = lt.steps_by_action(lt.label_steps())
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.trees = {}  # {sha: a tree holding _PINNED_SOURCES at that commit}
        cls.manifests = {}  # {action: its action.yml at the commit the workflows pin}
        repositories = {}
        for action in lt.LABEL_ACTIONS:
            sha = vcs_automation_pin(action)
            if sha not in repositories:
                repositories[sha] = cls._fetch(directory.name, sha)
                cls.trees[sha] = cls._write_tree(directory.name, repositories[sha])
            cls.manifests[action] = yaml.safe_load(repositories[sha].read(f"{action}/action.yml"))

    @staticmethod
    def _fetch(directory, sha):
        try:
            return PinnedRepository(directory, VCS_AUTOMATION, sha)
        except FetchFailed as error:
            if os.environ.get("CI"):
                raise
            raise unittest.SkipTest(str(error)) from error

    @staticmethod
    def _write_tree(directory, pinned):
        tree = os.path.join(directory, f"tree-{pinned.sha}")
        for path in _PINNED_SOURCES:
            os.makedirs(os.path.join(tree, os.path.dirname(path)), exist_ok=True)
            with open(os.path.join(tree, path), "w", encoding="utf-8") as f:
                f.write(pinned.read(path))
        return tree

    def test_each_step_passes_only_inputs_its_action_declares(self):
        for action, step in self.steps.items():
            with self.subTest(workflow=step.workflow, action=action):
                declared = set(self.manifests[action].get("inputs") or {})
                self.assertLessEqual(set(step.inputs), declared)

    def test_table_inputs_match_what_each_action_declares(self):
        """TABLE_INPUTS, and which of them are required, follow each action.yml at the pin."""
        for action in lt.LABEL_ACTIONS:
            with self.subTest(action=action):
                inputs = self.manifests[action].get("inputs") or {}
                declared = {
                    name: bool(spec.get("required"))
                    for name, spec in inputs.items()
                    if name not in _NOT_TABLES
                }
                self.assertEqual(lt.TABLE_INPUTS[action], declared)
                for name, spec in inputs.items():
                    if name in declared and not declared[name]:
                        self.assertEqual(spec.get("default"), lt.OPTIONAL_DEFAULT, name)

    def test_every_table_parses_with_the_action_parser(self):
        for sha, tree in self.trees.items():
            with _pinned_modules(tree) as modules:
                parsers = _parsers(*modules)
                lc = modules[0]
                for action, step in self.steps.items():
                    if vcs_automation_pin(action) != sha:
                        continue
                    for name in lt.TABLE_INPUTS[action]:
                        text = step.inputs.get(name, "") or lt.OPTIONAL_DEFAULT
                        with self.subTest(workflow=step.workflow, input=name):
                            try:
                                parsers[(action, name)](text)
                            except lc.InputError as error:
                                self.fail(f"{step.workflow}: {error}")

    def test_the_parsers_refuse_a_malformed_table(self):
        """The parsers are strict about what JSON alone allows: a check that they ran."""
        tree = next(iter(self.trees.values()))
        with _pinned_modules(tree) as modules:
            parsers = _parsers(*modules)
            lc = modules[0]
            for (action, name), text in {
                (lt.ENFORCE, "exclusive-sets"): '[["only one"]]',
                (lt.SNOOZE, "snooze-labels"): '{"snooze 3 days": 3, "snooze 3 days": 4}',
                (lt.WAKE, "snooze-labels"): '{"snooze 0 days": 0}',
                (lt.GATE, "blocking-labels"): "[]",
                (lt.FILES, "rules"): '{"ci": {"mode": "every-file"}}',
                (lt.TITLE, "rules"): '{"ci": "("}',
            }.items():
                with self.subTest(action=action, input=name), self.assertRaises(lc.InputError):
                    parsers[(action, name)](text)


if __name__ == "__main__":
    unittest.main()
