#!/usr/bin/env python3
"""The orchestrate plugin's copies of two shared helpers match their originals.

The plugin (.claude/skills/orchestrate/) imports nothing from this repository,
so that it can move to a repository of its own. Its link_gh_issues.py therefore
carries copies of github_headers() and of https_only.py's _after_https_scheme()
and https_request(), which every other script here imports from
scripts/ci/prs-and-issues/. This test fails when a copy and its original stop
being the same code, so a change to one reaches the other.

Docstrings are left out of the comparison: each copy's may say where it came
from. If the plugin leaves this repository, delete this test with it.
"""

import ast
import os
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ORIGINALS = os.path.join(REPO_ROOT, "scripts", "ci", "prs-and-issues")
PLUGIN_COPY = os.path.join(
    REPO_ROOT, ".claude", "skills", "orchestrate", "scripts", "link_gh_issues.py"
)

# function name -> the original module that defines it
COPIED = {
    "github_headers": "github_headers.py",
    "_after_https_scheme": "https_only.py",
    "https_request": "https_only.py",
}


def function_without_docstring(path, name):
    """Return the ast dump of the top-level function `name` in `path`, docstring removed."""
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in tree.body:
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
    raise AssertionError("%s defines no top-level function %s" % (path, name))


class TestHelperCopies(unittest.TestCase):
    def test_each_copy_matches_its_original(self):
        for name, module in COPIED.items():
            with self.subTest(function=name):
                self.assertEqual(
                    function_without_docstring(PLUGIN_COPY, name),
                    function_without_docstring(os.path.join(ORIGINALS, module), name),
                    "%s in link_gh_issues.py differs from scripts/ci/prs-and-issues/%s"
                    % (name, module),
                )


if __name__ == "__main__":
    unittest.main()
