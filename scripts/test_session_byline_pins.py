#!/usr/bin/env python3
"""The byline pattern is the same at both of this repository's pins.

Two copies of the Claude session-URL byline pattern act on this repository's
GitHub content, each at a commit pinned here:

- github-supplement's read-back hook, scripts/verify_github_write.py, at the
  aunger/dot-and-tilde commit .claude/gb4pc-tools pins. It strips bylines from
  what it wrote before comparing it with what GitHub stored.
- vcs-automation's strip-session-bylines action, whose shared/session_bylines.py
  removes them from GitHub, at the aunger/vcs-automation commit
  .github/workflows/strip-session-bylines.yml pins.

When the two disagree, the hook reports a mismatch for every byline the action
strips. A pin bump on either side can make them disagree, and neither of the
other repositories' CI knows this repository's pins, so this test fetches both
commits and fails when BYLINE_RE or strip_bylines() differ between them.
Docstrings and comments are left out of the comparison. Where a fetch fails the
test skips with the reason, except when CI is set, where it fails.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pinned_plugin_files import (
    VCS_AUTOMATION,
    FetchFailed,
    PinnedCommit,
    PinnedRepository,
    definition_without_docstring,
    vcs_automation_pin,
)

NAMES = ("BYLINE_RE", "strip_bylines")


class TestSessionBylinePins(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.action_sha = vcs_automation_pin("strip-session-bylines")
        cls._directory = tempfile.TemporaryDirectory()
        try:
            hook = PinnedCommit(cls._directory.name)
            action = PinnedRepository(cls._directory.name, VCS_AUTOMATION, cls.action_sha)
        except FetchFailed as error:
            cls._directory.cleanup()
            if os.environ.get("CI"):
                raise
            raise unittest.SkipTest(str(error)) from error
        cls.hook_sha = hook.sha
        cls.hook = hook.read("github-supplement", "scripts/verify_github_write.py")
        cls.action = action.read("shared/session_bylines.py")

    @classmethod
    def tearDownClass(cls):
        cls._directory.cleanup()

    def test_hook_and_action_strip_the_same_bylines(self):
        for name in NAMES:
            with self.subTest(name=name):
                self.assertEqual(
                    definition_without_docstring(self.hook, name, "verify_github_write.py"),
                    definition_without_docstring(self.action, name, "shared/session_bylines.py"),
                    "%s differs between github-supplement's scripts/verify_github_write.py at "
                    "dot-and-tilde %s and vcs-automation's shared/session_bylines.py at %s; "
                    "move the pins together" % (name, self.hook_sha, self.action_sha),
                )


class TestComparison(unittest.TestCase):
    """The comparison itself, so a check that stopped comparing would not pass quietly."""

    def test_docstrings_are_ignored_and_code_is_not(self):
        a = 'def f():\n    """One."""\n    return 1\n'
        b = 'def f():\n    """Two."""\n    return 1\n'
        c = 'def f():\n    """One."""\n    return 2\n'
        self.assertEqual(
            definition_without_docstring(a, "f", "a"), definition_without_docstring(b, "f", "b")
        )
        self.assertNotEqual(
            definition_without_docstring(a, "f", "a"), definition_without_docstring(c, "f", "c")
        )

    def test_a_changed_pattern_differs(self):
        a = 'import re\nBYLINE_RE = re.compile(r"x")\n'
        b = 'import re\nBYLINE_RE = re.compile(r"y")\n'
        self.assertNotEqual(
            definition_without_docstring(a, "BYLINE_RE", "a"),
            definition_without_docstring(b, "BYLINE_RE", "b"),
        )

    def test_a_missing_definition_fails(self):
        with self.assertRaises(AssertionError):
            definition_without_docstring("x = 1\n", "BYLINE_RE", "here")

    def test_the_workflows_pin_the_strip_action_once(self):
        self.assertRegex(vcs_automation_pin("strip-session-bylines"), r"^[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
