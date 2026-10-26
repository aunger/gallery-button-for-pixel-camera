#!/usr/bin/env python3
"""Tests for generate_strip_bylines_workflow.py.

The first test is the parity check: it fails whenever the copy of the strip
script in .github/workflows/strip-session-bylines.yml differs from what the
generator would write from the modules in this directory.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

import yaml
from generate_strip_bylines_workflow import BEGIN, END, WORKFLOW, generate, main, script


class WorkflowParityTest(unittest.TestCase):
    """The workflow holds exactly what the generator would write."""

    def test_workflow_is_up_to_date(self):
        self.assertEqual(
            0,
            main(["--check"]),
            "run scripts/ci/prs-and-issues/generate_strip_bylines_workflow.py",
        )

    def test_generated_step_runs(self):
        # A body with no byline makes no API call, so this runs the whole
        # generated script offline: every module's top level, and the
        # handler's call into session_bylines.
        with open(WORKFLOW, encoding="utf-8") as f:
            steps = yaml.safe_load(f)["jobs"]["strip-bylines"]["steps"]
        run = next(step["run"] for step in steps if BEGIN in step.get("run", ""))
        with tempfile.TemporaryDirectory() as d:
            script_path = os.path.join(d, "step.py")
            event_path = os.path.join(d, "event.json")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(run)
            with open(event_path, "w", encoding="utf-8") as f:
                json.dump({"comment": {"id": 1, "body": "no byline here"}}, f)
            env = {
                **os.environ,
                "GITHUB_TOKEN": "unused",
                "GITHUB_REPOSITORY": "owner/repo",
                "GITHUB_EVENT_NAME": "issue_comment",
                "GITHUB_EVENT_PATH": event_path,
            }
            result = subprocess.run(
                [sys.executable, script_path],
                check=False,
                env=env,
                capture_output=True,
                text=True,
                cwd=d,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("no bylines found", result.stdout)


class ScriptTest(unittest.TestCase):
    def test_local_imports_and_shebangs_are_dropped(self):
        code = script()
        self.assertNotIn("from github_headers import", code)
        self.assertNotIn("from session_bylines import", code)
        self.assertNotIn("#!", code)

    def test_modules_come_in_order(self):
        code = script()
        self.assertLess(code.index("def github_headers("), code.index("def strip_bylines("))
        self.assertLess(code.index("def strip_bylines("), code.index("def main("))


class GenerateTest(unittest.TestCase):
    WORKFLOW_TEXT = f"before\n    {BEGIN}\n    old line\n    {END}\nafter\n"

    def test_region_is_replaced_and_indented(self):
        self.assertEqual(
            f"before\n    {BEGIN}\n    x = 1\n\n    y = 2\n    {END}\nafter\n",
            generate(self.WORKFLOW_TEXT, "x = 1\n\ny = 2\n"),
        )

    def test_missing_marker_is_reported(self):
        for text in (f"{BEGIN}\n", f"{END}\n", "neither\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                generate(text, "x = 1\n")

    def test_repeated_marker_is_reported(self):
        with self.assertRaises(ValueError):
            generate(self.WORKFLOW_TEXT + f"{BEGIN}\n", "x = 1\n")

    def test_reversed_markers_are_reported(self):
        with self.assertRaises(ValueError):
            generate(f"{END}\n{BEGIN}\n", "x = 1\n")


if __name__ == "__main__":
    unittest.main()
