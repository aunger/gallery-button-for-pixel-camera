#!/usr/bin/env python3
"""The dot-and-tilde plugins' copies of this repository's helpers match their originals.

aunger/dot-and-tilde's github-supplement plugin imports nothing from this
repository, so two of its scripts carry copies of helpers that every script
here imports from scripts/ci/prs-and-issues/:

- bin/link_gh_issues.py: github_headers(), and https_only.py's
  _after_https_scheme() and https_request();
- scripts/verify_github_write.py (the read-back hook): the same three, and
  session_bylines.py's BYLINE_RE and strip_bylines(), which must recognize the
  bylines strip_session_bylines.py removes.

This test reads both scripts at the commit .claude/gb4pc-tools pins (through
pinned_plugin_files.py) and fails when a copy and its original stop being the
same code, so a change to one reaches the other, at the latest when the pin is
next bumped. Where the fetch fails it skips with the reason, except when CI is
set, where it fails.

Docstrings are left out of the comparison: each copy's may say where it came
from. orchestrate's ci_monitor.py has its own _after_https_scheme, written in
its own style, and is not compared.
"""

import ast
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ORIGINALS = os.path.join(REPO_ROOT, "scripts", "ci", "prs-and-issues")

sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
from pinned_plugin_files import FetchFailed, PinnedCommit  # noqa: E402

HTTPS_HELPERS = {
    "github_headers": "github_headers.py",
    "_after_https_scheme": "https_only.py",
    "https_request": "https_only.py",
}

# (plugin, path in the plugin) -> {top-level name: the original module that defines it}
COPIES = {
    ("github-supplement", "bin/link_gh_issues.py"): HTTPS_HELPERS,
    ("github-supplement", "scripts/verify_github_write.py"): {
        **HTTPS_HELPERS,
        "BYLINE_RE": "session_bylines.py",
        "strip_bylines": "session_bylines.py",
    },
}


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


def read_original(module):
    with open(os.path.join(ORIGINALS, module), encoding="utf-8") as handle:
        return handle.read()


class TestHelperCopies(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._directory = tempfile.TemporaryDirectory()
        try:
            cls.pinned = PinnedCommit(cls._directory.name)
        except FetchFailed as error:
            cls._directory.cleanup()
            if os.environ.get("CI"):
                raise
            raise unittest.SkipTest(str(error))

    @classmethod
    def tearDownClass(cls):
        cls._directory.cleanup()

    def test_each_copy_matches_its_original(self):
        for (plugin, path), names in COPIES.items():
            copy = self.pinned.read(plugin, path)
            for name, module in names.items():
                with self.subTest(copy=path, name=name):
                    self.assertEqual(
                        definition_without_docstring(copy, name, path),
                        definition_without_docstring(read_original(module), name, module),
                        "%s in %s's %s at %s differs from scripts/ci/prs-and-issues/%s"
                        % (name, plugin, path, self.pinned.sha, module),
                    )


if __name__ == "__main__":
    unittest.main()
