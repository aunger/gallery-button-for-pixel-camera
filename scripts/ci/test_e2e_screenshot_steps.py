#!/usr/bin/env python3
"""Tests for the E2E screenshot steps of `build.yml`'s build-and-test job.

"Pull and upload E2E screenshots on failure" runs whenever an E2E suite failed,
and its pull tolerates a screenshot directory that does not exist, so the step
succeeds whether or not a screenshot came back. "OCR on E2E screenshots" used to
run on that success alone, installing Tesseract with nothing to read (issue
#1351). The pull step now reports `has_png`, and the OCR step runs only on
`has_png == 'true'`.

The pull step's `run:` block is executed here, not read: `adb` is replaced by a
fake under a temporary `ANDROID_HOME`, `GITHUB_OUTPUT` points at a temporary
file, and the block's screenshot directory is rewritten to a temporary one so
that nothing is written to the real `/tmp`. The fake adb plays the three things
a device can hand back: a PNG, a window-hierarchy dump with no PNG beside it
(what FailureScreenshotRule leaves when its screenshot fails), and no directory
at all.

The OCR step's condition is read, since running it needs the Actions
expression engine. The upload step is pinned too: it keeps running on the pull
step's success, because a window-hierarchy dump is worth uploading without a
screenshot beside it.

Imported by bare module name, as the guards beside it import it, which resolves
because `.github/workflows/build.yml` discovers tests per directory rather than
recursively, putting `scripts/ci` on `sys.path`.
"""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest

from workflow_files import REPO_ROOT, load_workflow

BUILD_YML = os.path.join(REPO_ROOT, ".github", "workflows", "build.yml")
JOB = "build-and-test"
PULL_STEP_ID = "pull_e2e_screenshots"
OCR_STEP_NAME = "OCR on E2E screenshots"
UPLOAD_STEP_NAME = "Upload E2E screenshots on failure"

# The directory the pull step writes into, as the step spells it.
SCREENSHOT_DIR = "/tmp/e2e-screenshots"

# Stands in for `adb pull <device-dir> <host-dir>`. `adb pull` of a directory
# lands it under the host directory by its own name, as this does.
FAKE_ADB = """#!/usr/bin/env bash
[[ "$1" == pull ]] || exit 2
dest="$3/screenshots"
case "$FAKE_ADB_MODE" in
  png)
    mkdir -p "$dest"
    : > "$dest/Suite-test-failure.png"
    : > "$dest/Suite-test-failure-window-hierarchy.xml"
    ;;
  xml-only)
    mkdir -p "$dest"
    : > "$dest/Suite-test-failure-window-hierarchy.xml"
    ;;
  missing)
    echo "adb: error: failed to stat remote object" >&2
    exit 1
    ;;
  *)
    exit 3
    ;;
esac
"""


def _steps():
    return load_workflow(BUILD_YML)["jobs"][JOB]["steps"]


def _step(predicate, description):
    matches = [s for s in _steps() if predicate(s)]
    if len(matches) != 1:
        raise AssertionError(f"expected one {description} in {JOB}, found {len(matches)}")
    return matches[0]


def _pull_step():
    return _step(lambda s: s.get("id") == PULL_STEP_ID, f"step with id {PULL_STEP_ID}")


def _named_step(name):
    return _step(lambda s: s.get("name") == name, f"step named {name!r}")


class PullStepReportsPngTest(unittest.TestCase):
    """The pull step's `run:` block, executed against a fake adb."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name

        tools = os.path.join(self.tmp, "android", "platform-tools")
        os.makedirs(tools)
        adb = os.path.join(tools, "adb")
        with open(adb, "w", encoding="utf-8") as f:
            f.write(FAKE_ADB)
        os.chmod(adb, stat.S_IRWXU)

        script = _pull_step()["run"]
        # Without this the rewrite below would silently do nothing, and the
        # block would write into the real /tmp.
        self.assertIn(SCREENSHOT_DIR, script)
        self.screenshot_dir = os.path.join(self.tmp, "e2e-screenshots")
        self.script = script.replace(SCREENSHOT_DIR, self.screenshot_dir)

    def run_step(self, mode):
        output = os.path.join(self.tmp, "github_output")
        env = dict(
            os.environ,
            ANDROID_HOME=os.path.join(self.tmp, "android"),
            GITHUB_OUTPUT=output,
            FAKE_ADB_MODE=mode,
        )
        # `bash -e` is what Actions runs a `run:` block under on Linux.
        bash = shutil.which("bash")
        self.assertIsNotNone(bash, "bash not found on PATH")
        result = subprocess.run(
            [bash, "-e", "-c", self.script],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(output, encoding="utf-8") as f:
            return f.read().splitlines()

    def test_png_pulled_reports_true(self):
        self.assertEqual(self.run_step("png"), ["has_png=true"])

    def test_hierarchy_dump_alone_reports_false(self):
        self.assertEqual(self.run_step("xml-only"), ["has_png=false"])

    def test_no_directory_on_device_reports_false(self):
        self.assertEqual(self.run_step("missing"), ["has_png=false"])


class StepConditionsTest(unittest.TestCase):
    """What the OCR and upload steps run on.

    Each whole condition is compared, not searched: a dropped `always() && `
    would let Actions' implicit `success()` skip the step after any earlier
    failure in the job, and a substring check would also accept a widened
    condition such as `... || <anything>`.
    """

    def test_ocr_runs_only_when_a_png_was_pulled(self):
        self.assertEqual(
            _named_step(OCR_STEP_NAME)["if"],
            f"always() && steps.{PULL_STEP_ID}.outputs.has_png == 'true'",
        )

    def test_upload_still_runs_on_pull_success(self):
        self.assertEqual(
            _named_step(UPLOAD_STEP_NAME)["if"],
            f"always() && steps.{PULL_STEP_ID}.outcome == 'success'",
        )


if __name__ == "__main__":
    unittest.main()
