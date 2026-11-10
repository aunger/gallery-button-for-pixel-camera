#!/usr/bin/env python3
"""The committed .claude/orchestrate.json is a usable orchestrate configuration.

The orchestrate plugin reads this repository's facts from that file; its
check_config.py says what the file must hold. The plugin lives in
aunger/dot-and-tilde, and .claude/gb4pc-tools pins the commit this repository
installs, so this test fetches check_config.py at that commit (through
pinned_plugin_files.py) and runs it over the committed file. Where the fetch
fails (offline, or a sandbox that cannot reach GitHub) that check is skipped
with the reason, except when CI is set, where it fails, so CI always runs it.

The test also pins each value to the part of this repository it describes, so
a change on either side that leaves the other behind fails here:

- every named check is the check name of a job some workflow defines (the
  job's `name:`, or its id when it has none);
- the merge gate is left off the named checks, and the Monitor ignores it;
- artifact_name_regex selects every per-test feed build.yml uploads, and
  test_marker_regex matches the marker build.yml's test steps write;
- the repository and the verification-plan marker are this repository's.

It does not check that the named checks are the ruleset's required checks,
which only the GitHub API knows.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CONFIG = os.path.join(REPO_ROOT, ".claude", "orchestrate.json")
BUILD_YML = os.path.join(REPO_ROOT, ".github", "workflows", "build.yml")
MERGE_GATE = "Administrative merge holds (not failure)"

sys.path.insert(0, os.path.join(REPO_ROOT, "scripts", "ci"))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
from pinned_plugin_files import FetchFailed, PinnedCommit  # noqa: E402
from workflow_files import load_workflow, workflow_paths  # noqa: E402


def load_config():
    with open(CONFIG, encoding="utf-8") as handle:
        return json.load(handle)


def read_build_yml():
    with open(BUILD_YML, encoding="utf-8") as handle:
        return handle.read()


def workflow_check_names():
    """Return every job's check name across the workflows: its `name:`, else its id."""
    names = set()
    for path in workflow_paths():
        for job_id, job in (load_workflow(path).get("jobs") or {}).items():
            name = job.get("name", job_id) if isinstance(job, dict) else job_id
            names.add(str(name))
    return names


class TestCommittedConfig(unittest.TestCase):
    def test_check_config_accepts_it_without_warnings(self):
        with tempfile.TemporaryDirectory() as directory:
            check = os.path.join(directory, "check_config.py")
            try:
                text = PinnedCommit(directory).read("orchestrate", "scripts/check_config.py")
            except (FetchFailed, subprocess.CalledProcessError) as error:
                detail = getattr(error, "stderr", None) or error
                message = f"could not fetch the pinned check_config.py: {detail}"
                if os.environ.get("CI"):
                    self.fail(message)
                self.skipTest(message)
            with open(check, "w", encoding="utf-8") as handle:
                handle.write(text)
            completed = subprocess.run(
                [sys.executable, check, CONFIG], capture_output=True, text=True, check=False
            )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertNotIn("warning:", completed.stdout)

    def test_every_named_check_is_a_workflow_job(self):
        missing = sorted(set(load_config()["named_checks"]) - workflow_check_names())
        self.assertEqual(missing, [], "named checks no workflow job produces")

    def test_the_merge_gate_is_ignored_and_not_a_named_check(self):
        config = load_config()
        self.assertIn(MERGE_GATE, workflow_check_names())
        self.assertNotIn(MERGE_GATE, config["named_checks"])
        self.assertIsNotNone(re.search(config["ignored_check_regex"], MERGE_GATE))

    def test_the_artifact_regex_selects_every_feed_build_yml_uploads(self):
        feeds = re.findall(r"^\s*name:\s*(ci-monitor-feed-\S+)\s*$", read_build_yml(), re.MULTILINE)
        self.assertGreater(len(feeds), 0, "build.yml uploads no ci-monitor-feed-* artifact")
        pattern = load_config()["artifact_name_regex"]
        self.assertEqual([name for name in feeds if not re.search(pattern, name)], [])

    def test_the_marker_regex_matches_the_marker_build_yml_writes(self):
        markers = set(re.findall(r"grep '\^(##[A-Z0-9_]+##)'", read_build_yml()))
        self.assertGreater(len(markers), 0, "build.yml writes no ##...## test marker")
        pattern = load_config()["test_marker_regex"]
        for marker in markers:
            line = marker + '{"suite":"s","name":"n","outcome":"FAIL"}'
            match = re.search(pattern, line)
            self.assertIsNotNone(match, marker)
            self.assertTrue(line[match.end() :].startswith("{"), marker)

    def test_the_repository_is_this_one(self):
        config = load_config()
        self.assertEqual(config["repository"], "aunger/gallery-button-for-pixel-camera")
        self.assertEqual(config["verification_plan_marker"], "<!-- gb4pc-verification-plan -->")


if __name__ == "__main__":
    unittest.main()
