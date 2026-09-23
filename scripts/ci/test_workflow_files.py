#!/usr/bin/env python3
"""Unit tests for workflow_files.py.

The module's ordinary path is already covered: `test_privileged_workflow_checkouts.py`
and `test_setup_android_packages.py` both run it over the real tree on every CI run, and
each asserts that the path list comes back non-empty.
Two of its decisions are covered by nothing (issue #1132).

`WORKFLOW_GLOBS` covers `.github/workflows/*.yaml` as well as `*.yml`, and this
repository has no `.yaml` workflow, so dropping that half of the tuple would fail nothing
here while letting a `.yaml` workflow escape both guards.
`load_workflow()` returns `or {}` for a file that parses to nothing, and no workflow here
is empty, so dropping that arm would fail nothing here while handing both guards a None
to call `.get()` on.
Pinning those two arms is what this file is for.

Neither arm is reachable from the real tree, so the fixtures are built under a temporary
directory instead.
`workflow_paths()` reads `REPO_ROOT`, which the tests point at that directory;
`load_workflow()` opens the path it is handed, so it needs no such redirection.

Each arm is tested next to its companion: a `.yml` file beside the `.yaml` one, a
populated file beside the empty one.
Without the companions, a module that had stopped doing the general thing--a glob tuple
that had lost `.yml`, a `load_workflow()` that returned `{}` for everything--would still
pass on the arms alone.

Imported by bare module name, as the two guards beside it are, which resolves because
`.github/workflows/build.yml` discovers tests per directory rather than recursively,
putting `scripts/ci` on `sys.path`.
"""

import os
import tempfile
import unittest
from unittest import mock

import workflow_files
from workflow_files import load_workflow, workflow_paths


class WorkflowPathsTest(unittest.TestCase):
    """`workflow_paths()` over a tree built to hold what the real one does not."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.workflows = os.path.join(self.root, ".github", "workflows")
        os.makedirs(self.workflows)
        patcher = mock.patch.object(workflow_files, "REPO_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, name: str) -> str:
        """Write a minimal workflow under the temporary root, and return its path."""
        path = os.path.join(self.workflows, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("on: push\n")
        return path

    def test_a_yaml_workflow_is_found(self):
        """The half of `WORKFLOW_GLOBS` no file in this repository exercises."""
        path = self.write("build.yaml")
        self.assertEqual([path], workflow_paths())

    def test_a_yml_workflow_is_found(self):
        path = self.write("build.yml")
        self.assertEqual([path], workflow_paths())

    def test_both_extensions_are_returned_together_and_sorted(self):
        """The two globs are searched separately and their results concatenated, so
        sorting, not glob order, is what decides the order they come back in."""
        second = self.write("second.yml")
        first = self.write("first.yaml")
        self.assertEqual([first, second], workflow_paths())

    def test_a_file_of_another_extension_is_ignored(self):
        """GitHub reads none of these, and a guard that opened them would judge files
        that are not workflows. `build.yml.bak` is the one a looser pattern catches."""
        self.write("build.yml")
        for name in ("README.md", "build.yml.bak", "config.json"):
            with open(os.path.join(self.workflows, name), "w", encoding="utf-8") as f:
                f.write("on: push\n")
        self.assertEqual([os.path.join(self.workflows, "build.yml")], workflow_paths())


class LoadWorkflowTest(unittest.TestCase):
    """`load_workflow()` on files the real tree holds no example of."""

    def write(self, text: str) -> str:
        """Write a workflow file holding `text`, and return its path."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "workflow.yml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_an_empty_file_loads_as_an_empty_mapping(self):
        """The `or {}` arm: `yaml.safe_load` gives None, which every caller would
        then call `.get()` on."""
        self.assertEqual({}, load_workflow(self.write("")))

    def test_a_file_holding_only_comments_loads_as_an_empty_mapping(self):
        """The same arm, by the route a workflow actually empties out along."""
        self.assertEqual({}, load_workflow(self.write("# commented out for now\n")))

    def test_a_populated_file_loads_as_its_mapping(self):
        """The key reads True, not "on", because YAML 1.1 resolves the bare key `on` to
        the boolean and `load_workflow()` hands PyYAML's parse back unchanged.
        `triggers()` in `test_privileged_workflow_checkouts.py` looks the key up both
        ways for this reason."""
        loaded = load_workflow(self.write("on: push\njobs:\n  build:\n    steps: []\n"))
        self.assertEqual({True: "push", "jobs": {"build": {"steps": []}}}, loaded)


if __name__ == "__main__":
    unittest.main()
