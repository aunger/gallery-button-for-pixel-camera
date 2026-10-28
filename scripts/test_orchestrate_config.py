#!/usr/bin/env python3
"""The committed .claude/orchestrate.json is a usable orchestrate configuration.

The orchestrate plugin (.claude/skills/orchestrate/) reads this repository's
facts from that file; its check_config.py says what the file must hold. This
test runs that check over the committed file, and pins the two values the
repository's own CI depends on: the merge gate is ignored by the Monitor, and
the named checks are the job names build.yml and the dependency-checks
workflow produce.
"""

import json
import os
import subprocess
import sys
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CONFIG = os.path.join(REPO_ROOT, ".claude", "orchestrate.json")
CHECK = os.path.join(REPO_ROOT, ".claude", "skills", "orchestrate", "scripts", "check_config.py")


class TestCommittedConfig(unittest.TestCase):
    def test_check_config_accepts_it_without_warnings(self):
        completed = subprocess.run(
            [sys.executable, CHECK, CONFIG], capture_output=True, text=True, check=False
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertNotIn("warning:", completed.stdout)

    def test_the_merge_gate_is_ignored_and_not_a_named_check(self):
        with open(CONFIG, encoding="utf-8") as handle:
            config = json.load(handle)
        gate = "Administrative merge holds (not failure)"
        self.assertNotIn(gate, config["named_checks"])
        import re

        self.assertIsNotNone(re.search(config["ignored_check_regex"], gate))

    def test_the_repository_is_this_one(self):
        with open(CONFIG, encoding="utf-8") as handle:
            config = json.load(handle)
        self.assertEqual(config["repository"], "aunger/gallery-button-for-pixel-camera")
        self.assertEqual(config["verification_plan_marker"], "<!-- gb4pc-verification-plan -->")


if __name__ == "__main__":
    unittest.main()
