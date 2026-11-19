#!/usr/bin/env python3
"""This repository's GitHub helpers are the same code as vcs-automation's.

scripts/ci/prs-and-issues/github_headers.py and https_only.py stay here while
scripts here import them (gradle_coordinates.py,
dependency-checks/github_rest.py, and post_pr_ci_summary_link.py and
watch_toolchain_bump.py among them).
aunger/vcs-automation's shared/ holds the copies its actions use, and the ones
aunger/dot-and-tilde's github-supplement plugin copies are checked against.

This test fetches vcs-automation at each commit the workflows pin and fails
when a function or assignment in either module stops being the same code as
the one in shared/ there, so a fix to the https-only check reaches this
repository with the next pin bump rather than staying behind in a copy nothing
reads. Docstrings and comments are left out of the comparison. Where the fetch
fails the test skips with the reason, except when CI is set, where it fails.
"""

import ast
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HELPERS = os.path.join(HERE, "ci", "prs-and-issues")
MODULES = ("github_headers.py", "https_only.py")

sys.path.insert(0, HERE)
from pinned_plugin_files import (  # noqa: E402
    VCS_AUTOMATION,
    FetchFailed,
    PinnedRepository,
    definition_without_docstring,
    vcs_automation_pins,
)


def top_level_names(source):
    """Return the names of the top-level functions and simple assignments in `source`."""
    names = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names.extend(target.id for target in node.targets if isinstance(target, ast.Name))
    return names


class TestSharedHelperCopies(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pins = vcs_automation_pins()
        cls._directory = tempfile.TemporaryDirectory()
        try:
            cls.repositories = {
                sha: PinnedRepository(cls._directory.name, VCS_AUTOMATION, sha) for sha in cls.pins
            }
        except FetchFailed as error:
            cls._directory.cleanup()
            if os.environ.get("CI"):
                raise
            raise unittest.SkipTest(str(error)) from error

    @classmethod
    def tearDownClass(cls):
        cls._directory.cleanup()

    def test_the_workflows_pin_vcs_automation(self):
        self.assertTrue(self.pins, "no workflow pins aunger/vcs-automation")

    def test_each_helper_matches_vcs_automation_at_each_pin(self):
        for module in MODULES:
            with open(os.path.join(HELPERS, module), encoding="utf-8") as handle:
                ours = handle.read()
            for sha, repository in self.repositories.items():
                theirs = repository.read("shared/" + module)
                self.assertEqual(
                    sorted(top_level_names(ours)),
                    sorted(top_level_names(theirs)),
                    "%s defines other names than vcs-automation's shared/%s at %s"
                    % (module, module, sha),
                )
                for name in top_level_names(ours):
                    with self.subTest(module=module, name=name, pin=sha):
                        self.assertEqual(
                            definition_without_docstring(ours, name, module),
                            definition_without_docstring(theirs, name, "shared/" + module),
                            "%s in scripts/ci/prs-and-issues/%s differs from vcs-automation's "
                            "shared/%s at %s, which %s pin; change both"
                            % (name, module, module, sha, ", ".join(self.pins[sha])),
                        )


if __name__ == "__main__":
    unittest.main()
