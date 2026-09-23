#!/usr/bin/env python3
"""Unit tests for workflow_files.py.

The module's ordinary path is already covered: `test_privileged_workflow_checkouts.py`
and `test_setup_android_packages.py` both run it over the real tree on every CI run, and
each asserts that the path list comes back non-empty.
Three of its decisions were covered by nothing: two branches (issue #1132), and
`relative()` entire (issue #1140).

`WORKFLOW_GLOBS` covers `.github/workflows/*.yaml` as well as `*.yml`, and this
repository has no `.yaml` workflow, so dropping that half of the tuple would fail nothing
here while letting a `.yaml` workflow escape both guards.
`load_workflow()` returns `or {}` for a file that parses to nothing, and no workflow here
is empty, so dropping that arm would fail nothing here while handing both guards a None
to call `.get()` on.
`relative()` names a workflow for the guards' failure messages, and nothing asserts on what
it returns, so a body of `return path` would fail nothing here while spelling those
messages as absolute paths of whatever directory the runner checked the repository out
into.
Pinning those three decisions is what this file is for.

Neither branch is reachable from the real tree, so the fixtures for those two are built
under a temporary directory instead.
`workflow_paths()` and `relative()` read `REPO_ROOT`, which the tests point elsewhere;
`load_workflow()` opens the path it is handed, so it needs no such redirection.

Each arm is tested next to its companion: a `.yml` file beside the `.yaml` one, a
populated file beside the empty one, the real tree's own workflow paths beside the
fabricated ones.
Without the companions, a module that had stopped doing the general thing--a glob tuple
that had lost `.yml`, a `load_workflow()` that returned `{}` for everything, a
`relative()` that answered for the fixture alone--would still pass on the arms.

Imported by bare module name, as the two guards beside it are, which resolves because
`.github/workflows/build.yml` discovers tests per directory rather than recursively,
putting `scripts/ci` on `sys.path`.
"""

import os
import tempfile
import unittest
from unittest import mock

import workflow_files
from workflow_files import load_workflow, relative, workflow_paths


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


class RelativeTest(unittest.TestCase):
    """`relative()`, which no caller asserts on.

    Both guards beside this file and `scripts/test_dependabot_config.sh` call it for the
    name a failure message gives a workflow, and none of them asserts on what comes back,
    so nothing else in the tree would notice a body of `return path` (issue #1140).

    `os.path.relpath` opens nothing, so the roots below need not exist: a path under a
    fabricated root is enough to say where the result is cut.
    """

    CHECKOUT = os.path.join(os.sep, "runner", "work", "checkout")
    WORKFLOW = os.path.join(CHECKOUT, ".github", "workflows", "build.yml")

    def relative_under(self, root: str, path: str) -> str:
        """Return `relative(path)` as computed with `REPO_ROOT` pointing at `root`."""
        with mock.patch.object(workflow_files, "REPO_ROOT", root):
            return relative(path)

    def test_a_workflow_under_the_root_comes_back_without_it(self):
        """What the messages carry: the path a reader can look up in the repository,
        rather than one that names the directory the runner checked it out into."""
        self.assertEqual(
            os.path.join(".github", "workflows", "build.yml"),
            self.relative_under(self.CHECKOUT, self.WORKFLOW),
        )

    def test_the_cut_is_made_at_the_repository_root(self):
        """`REPO_ROOT` is read at the call, not baked into the answer: the same path
        under a shallower root keeps the segments that root does not cover."""
        self.assertEqual(
            os.path.join("checkout", ".github", "workflows", "build.yml"),
            self.relative_under(os.path.dirname(self.CHECKOUT), self.WORKFLOW),
        )

    def test_the_real_tree_s_workflows_come_back_relative(self):
        """Over the paths the callers actually hand it, unpatched: each result is
        relative, and rejoins `REPO_ROOT` to the file it was asked about.

        The two tests above hold for a `relative()` that had been narrowed to the
        fabricated root they build; this one does not."""
        paths = workflow_paths()
        self.assertTrue(paths, "found no workflow files to check")
        for path in paths:
            rel = relative(path)
            with self.subTest(workflow=rel):
                self.assertFalse(os.path.isabs(rel))
                self.assertEqual(path, os.path.join(workflow_files.REPO_ROOT, rel))


if __name__ == "__main__":
    unittest.main()
