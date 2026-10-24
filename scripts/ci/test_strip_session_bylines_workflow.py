#!/usr/bin/env python3
"""Guard: the strip-bylines job downloads its script from the default branch.

`.github/workflows/strip-session-bylines.yml` holds `issues: write` and
`pull-requests: write`, and runs on pull request events.
It has no `actions/checkout` step.
It downloads `strip_session_bylines.py` and the modules that script imports
with `curl`, and runs the result.

`test_privileged_workflow_checkouts.py` inspects only `actions/checkout` steps,
so it says nothing about this job.
Without this guard, an edit that pointed the download at the pull request's
base or head would pass every other check in the tree.
The base is not a safe source either: for a stacked pull request it is another
pull request's head branch, and the pull request under review would show no
change to the script it was running.

What this pins, for the job `strip-bylines`:

- It has no `actions/checkout` step.
- Exactly one step downloads from `raw.githubusercontent.com`, and its
  `TRUSTED_REF` is `${{ github.event.repository.default_branch }}`.
- The step's script contains no `${{ }}` expression.
  A context value spliced into shell text is a script injection; values reach
  it through `env:` instead.
- The URL is `$GITHUB_REPOSITORY/$TRUSTED_REF/scripts/ci/prs-and-issues/`.
- The downloaded files are exactly `strip_session_bylines.py` and the local
  modules it imports, directly or through each other.
  A module added to the script without being added to the download list would
  fail the job with an ImportError that only a real event shows.

What it does not pin: the workflow file itself comes from the pull request's
merge ref on `pull_request` events, so a pull request can edit this job and
this guard together.
That edit would appear in the workflow diff, which is all the pin ever bought;
see "What the pinning does not achieve" in `.github/workflows/CLAUDE.md`.
"""

import ast
import os
import re
import tempfile
import unittest

import yaml
from workflow_files import REPO_ROOT, load_workflow

WORKFLOW = os.path.join(REPO_ROOT, ".github", "workflows", "strip-session-bylines.yml")
SCRIPT_DIR = "scripts/ci/prs-and-issues"
ENTRY_POINT = "strip_session_bylines"
JOB = "strip-bylines"
TRUSTED_REF = "${{ github.event.repository.default_branch }}"
DOWNLOAD_HOST = "raw.githubusercontent.com"
CHECKOUT_ACTION = "actions/checkout"


def local_modules(entry: str, directory: str) -> set[str]:
    """Return `entry` and every module in `directory` it imports, transitively.

    A module counts when `<name>.py` exists in `directory`, which is how the
    scripts there find each other: the interpreter puts the running script's
    directory first on `sys.path`.
    Standard library and third-party imports have no such file and are ignored.
    """
    found: set[str] = set()
    pending = [entry]
    while pending:
        name = pending.pop()
        if name in found:
            continue
        found.add(name)
        with open(os.path.join(directory, name + ".py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported = [node.module.split(".")[0]]
            else:
                continue
            pending.extend(
                m for m in imported if os.path.exists(os.path.join(directory, m + ".py"))
            )
    return found


def problems(workflow: dict, directory: str) -> list[str]:
    """Return a message for each way the strip-bylines job departs from the pin."""
    job = (workflow.get("jobs") or {}).get(JOB)
    if job is None:
        return [f"no job named {JOB!r}"]
    steps = job.get("steps") or []

    found: list[str] = []
    for step in steps:
        uses = step.get("uses")
        if isinstance(uses, str) and uses.split("@", 1)[0] == CHECKOUT_ACTION:
            found.append(f"job {JOB!r} checks out the repository; it must download instead")

    downloads = [s for s in steps if isinstance(s.get("run"), str) and DOWNLOAD_HOST in s["run"]]
    if len(downloads) != 1:
        return found + [
            f"expected exactly one step downloading from {DOWNLOAD_HOST}, found {len(downloads)}"
        ]
    step = downloads[0]
    run = step["run"]

    ref = (step.get("env") or {}).get("TRUSTED_REF")
    if not isinstance(ref, str) or ref.strip() != TRUSTED_REF:
        found.append(f"TRUSTED_REF is {ref!r}; it must be {TRUSTED_REF!r}")
    if "${{" in run:
        found.append(
            "the download step's script contains a ${{ }} expression; pass values through env:"
        )

    prefix = f"https://{DOWNLOAD_HOST}/$GITHUB_REPOSITORY/$TRUSTED_REF/{SCRIPT_DIR}/"
    if prefix not in run:
        found.append(f"the download URL does not start with {prefix}")

    glob = re.search(r"\{([^}]*)\}\.py", run)
    downloaded = set(glob.group(1).split(",")) if glob else set()
    needed = local_modules(ENTRY_POINT, os.path.join(directory, *SCRIPT_DIR.split("/")))
    if downloaded != needed:
        found.append(
            f"downloaded modules {sorted(downloaded)} differ from the script's local imports "
            f"{sorted(needed)}"
        )

    if f'python3 "$RUNNER_TEMP/strip/{ENTRY_POINT}.py"' not in run or (
        '--output-dir "$RUNNER_TEMP/strip"' not in run
    ):
        found.append(
            f"the step must download into $RUNNER_TEMP/strip and run {ENTRY_POINT}.py from there"
        )
    return found


class StripBylinesWorkflowTest(unittest.TestCase):
    """The real workflow obeys the pin."""

    def test_job_downloads_from_the_default_branch(self):
        found = problems(load_workflow(WORKFLOW), REPO_ROOT)
        self.assertEqual([], found, "; ".join(found))


class ProblemDetectionTest(unittest.TestCase):
    """The pin itself, exercised against edited copies of the real workflow.

    Without these, a guard that had stopped detecting anything would still
    report a clean tree.
    """

    def setUp(self):
        with open(WORKFLOW, encoding="utf-8") as f:
            self.text = f.read()

    def _problems(self, old: str, new: str) -> list[str]:
        self.assertIn(old, self.text, "the edit would not change the workflow")
        return problems(yaml.safe_load(self.text.replace(old, new, 1)), REPO_ROOT)

    def test_pull_request_base_is_reported(self):
        found = self._problems(
            TRUSTED_REF, "${{ github.event.pull_request.base.sha || github.sha }}"
        )
        self.assertEqual(1, len(found), found)
        self.assertIn("TRUSTED_REF", found[0])

    def test_pull_request_head_is_reported(self):
        found = self._problems(TRUSTED_REF, "${{ github.event.pull_request.head.sha }}")
        self.assertEqual(1, len(found), found)

    def test_a_branch_name_is_reported(self):
        found = self._problems(TRUSTED_REF, "main")
        self.assertEqual(1, len(found), found)

    def test_a_checkout_step_is_reported(self):
        found = self._problems(
            "    steps:\n",
            "    steps:\n      - uses: actions/checkout@v7\n",
        )
        self.assertEqual(1, len(found), found)
        self.assertIn("checks out", found[0])

    def test_an_expression_in_the_script_is_reported(self):
        found = self._problems("mkdir -p", 'echo "${{ github.head_ref }}"\n          mkdir -p')
        self.assertEqual(1, len(found), found)
        self.assertIn("expression", found[0])

    def test_a_different_download_url_is_reported(self):
        found = self._problems(
            "$GITHUB_REPOSITORY/$TRUSTED_REF", "$GITHUB_REPOSITORY/refs/pull/1/head"
        )
        self.assertEqual(1, len(found), found)
        self.assertIn("URL", found[0])

    def test_a_missing_module_is_reported(self):
        found = self._problems(
            "{strip_session_bylines,github_headers,session_bylines}",
            "{strip_session_bylines,github_headers}",
        )
        self.assertEqual(1, len(found), found)
        self.assertIn("session_bylines", found[0])

    def test_an_extra_module_is_reported(self):
        found = self._problems(
            "{strip_session_bylines,github_headers,session_bylines}",
            "{strip_session_bylines,github_headers,session_bylines,retry}",
        )
        self.assertEqual(1, len(found), found)

    def test_a_missing_job_is_reported(self):
        found = problems({"jobs": {}}, REPO_ROOT)
        self.assertEqual(1, len(found), found)

    def test_no_download_step_is_reported(self):
        found = problems({"jobs": {JOB: {"steps": [{"run": "true"}]}}}, REPO_ROOT)
        self.assertEqual(1, len(found), found)


class LocalModulesTest(unittest.TestCase):
    """The import walk that decides which files the job must download."""

    def _write(self, directory: str, name: str, text: str) -> None:
        with open(os.path.join(directory, name + ".py"), "w", encoding="utf-8") as f:
            f.write(text)

    def test_imports_are_followed_through_each_other(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "entry", "import json\nimport b\nfrom requests import get\n")
            self._write(d, "b", "from c import thing\n")
            self._write(d, "c", "import os\n")
            self._write(d, "unused", "import entry\n")
            self.assertEqual({"entry", "b", "c"}, local_modules("entry", d))

    def test_an_import_cycle_terminates(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "a", "import b\n")
            self._write(d, "b", "import a\n")
            self.assertEqual({"a", "b"}, local_modules("a", d))

    def test_the_real_script_needs_its_two_sibling_modules(self):
        directory = os.path.join(REPO_ROOT, *SCRIPT_DIR.split("/"))
        self.assertEqual(
            {"strip_session_bylines", "github_headers", "session_bylines"},
            local_modules(ENTRY_POINT, directory),
        )


if __name__ == "__main__":
    unittest.main()
