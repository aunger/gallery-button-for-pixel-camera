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

import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STRIP_WORKFLOW = os.path.join(REPO_ROOT, ".github", "workflows", "strip-session-bylines.yml")
ACTION_USES = re.compile(r"uses:\s*aunger/vcs-automation/strip-session-bylines@([0-9a-f]{40})\b")
VCS_AUTOMATION = "https://github.com/aunger/vcs-automation.git"
GIT_TIMEOUT_SECONDS = 120

sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
from pinned_plugin_files import FetchFailed, PinnedCommit  # noqa: E402

NAMES = ("BYLINE_RE", "strip_bylines")


def strip_action_sha():
    """Return the vcs-automation commit strip-session-bylines.yml pins."""
    with open(STRIP_WORKFLOW, encoding="utf-8") as handle:
        found = set(ACTION_USES.findall(handle.read()))
    if len(found) != 1:
        raise AssertionError(
            "expected one aunger/vcs-automation/strip-session-bylines@<sha> in %s, found %r"
            % (os.path.relpath(STRIP_WORKFLOW, REPO_ROOT), sorted(found))
        )
    return found.pop()


def read_vcs_automation(directory, sha, path):
    """Fetch *sha* of vcs-automation into a bare repository under *directory*; return *path*."""
    git = shutil.which("git")
    git_dir = os.path.join(directory, "vcs-automation.git")

    def run(*args):
        # --git-dir, so a GIT_DIR inherited from a git hook cannot redirect it.
        return subprocess.run(
            [git, "--git-dir", git_dir, *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout

    try:
        if git is None:
            raise FileNotFoundError("git is not on PATH")
        run("init", "-q", "--bare", git_dir)
        run("fetch", "-q", "--depth", "1", VCS_AUTOMATION, sha)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as error:
        detail = getattr(error, "stderr", None) or error
        raise FetchFailed("could not fetch %s at %s: %s" % (VCS_AUTOMATION, sha, detail)) from error
    return run("show", "%s:%s" % (sha, path))


def definition_without_docstring(source, name, where):
    """Return the ast dump of the top-level function or assignment `name`, docstring removed."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:]
            return ast.dump(node)
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.dump(node)
    raise AssertionError("%s defines no top-level %s" % (where, name))


class TestSessionBylinePins(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.action_sha = strip_action_sha()
        cls._directory = tempfile.TemporaryDirectory()
        try:
            pinned = PinnedCommit(cls._directory.name)
            cls.hook_sha = pinned.sha
            cls.hook = pinned.read("github-supplement", "scripts/verify_github_write.py")
            cls.action = read_vcs_automation(
                cls._directory.name, cls.action_sha, "shared/session_bylines.py"
            )
        except FetchFailed as error:
            cls._directory.cleanup()
            if os.environ.get("CI"):
                raise
            raise unittest.SkipTest(str(error)) from error

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

    def test_the_workflow_pins_the_action_once(self):
        self.assertRegex(strip_action_sha(), r"^[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
