#!/usr/bin/env python3
"""Guard: every wait on the emulator in a workflow carries a bound of its own.

A wait with no bound of its own is not unbounded in practice: it is held by
whatever encloses it, the step's `timeout-minutes` or, where the step declares
none, the job's. What that costs is the diagnosis. The run reports the step or
the job running out of time, the wait that did not finish is not named, and
whatever the enclosing budget still had to pay for is lost with it.

Issue #1154 found two such waits in `build.yml`: a `wait-for-device` in the
SetupActivityPermissionDialogE2ETest recovery path, and the `sys.boot_completed`
poll in "Wait for emulator service readiness". PR #1151 had replaced a third in
`scripts/ci/test-support/setup-e2e-emulator.sh` shortly before. Each now bounds
itself and says so on expiry. This keeps the next edit from quietly restoring
the old shape, which would break no test and fail no run until the day a device
hangs, which is the day the diagnosis is wanted.

The rule, over the `run:` blocks of every workflow in the repository:

    1. A `wait-for-device` call runs under `timeout`.
    2. An `until` or `while` loop whose condition drives adb carries a check of
       elapsed time against a bound in its body.

Those are the two shapes this tree writes a device wait in. A `for` loop over
`seq` is bounded by its own construction and is not examined.

Limits
------

This reads workflow files. The waits in `scripts/` are covered by those
scripts' own suites, `scripts/ci/test-support/test_setup_e2e_emulator.sh` among
them, which run the code rather than reading it.

It judges that a bound is present, never that its value is right, and never
that the bound can be reached: `timeout 1200` inside a step allowed
`timeout-minutes: 10` satisfies this guard and still cannot fire, because the
step is killed at 10 minutes first.

It is a text scan over shell. A wait assembled from a variable, or one inside a
script the workflow calls, is invisible to it. A loop whose condition is spread
over several lines is not recognized as a device poll, and a `timeout` earlier
in the same command list as a `wait-for-device` is not credited to it, though a
`timeout` in the same simple command is.

A bound anywhere in a loop's body satisfies the check, including one that
belongs to a loop nested inside that body and so cannot end the outer loop.
Telling the two apart means parsing the shell rather than reading it, which is
a large amount of machinery for a shape this tree does not write.

What it accepts as a bound is a numeric comparison, `-ge` or `-gt`, and nothing
establishes that the comparison is against elapsed time or that the body counts
up what it compares: `if [[ $RETRY_BUDGET -gt 0 ]]` passes as readily as `if [[
$BOOTWAIT -ge 180 ]]`. So this is weaker than "the loop gives up", and reads
better as "the loop was written with a way out in mind". Both blind spots here
are pinned by tests, so that they are known rather than merely undiscovered.
"""

import re
import unittest

import yaml
from workflow_files import load_workflow, relative, workflow_paths

WAIT_FOR_DEVICE = "wait-for-device"

# `timeout 120 adb ...`, `timeout -k 5s 120 adb ...`, `timeout 20m adb ...`.
TIMEOUT_PREFIX = re.compile(r"\btimeout\s+(?:-\S+\s+)*\d+[smhd]?\s")

# What makes a loop a device poll: its condition drives adb, through the $ADB
# the workflows resolve once per step, or through the binary itself, named bare
# or by a path.
#
# A path separator is deliberately absent from the lookbehind's excluded set.
# `$ANDROID_HOME/platform-tools/adb` is the string every step of build.yml
# assigns $ADB from, so a step that inlines it, or that names adb once and keeps
# no variable for it, would otherwise have its polls read as nothing at all. The
# set excludes what makes `adb` the tail of a longer word instead, `read-adb`
# among them.
ADB_CALL = re.compile(r"\$\{?ADB\b|(?<![\w.-])adb\s")

# An elapsed-against-bound check, as both loops in "Wait for emulator service
# readiness" and every bounded loop in setup-e2e-emulator.sh spell one:
# `if [[ $SVCWAIT -ge 120 ]]; then`.
BOUND_CHECK = re.compile(r"-(?:ge|gt)\s")

LOOP_HEADER = re.compile(r"^(?P<indent>[ \t]*)(?:until|while)\s")

# Where one command in a list ends and the next begins, so that the `timeout`
# credited to a wait is one in front of that wait rather than one before a
# semicolon.
COMMAND_SEPARATOR = re.compile(r"[;&|]+")


def step_label(job_name: str, step: dict) -> str:
    """Name the step a finding came from. `name:` is optional on a step."""
    name = step.get("name")
    if isinstance(name, str) and name.strip():
        return f"job {job_name!r} step {name.strip()!r}"
    return f"job {job_name!r} unnamed run step"


def run_blocks(workflow: dict):
    """Yield (label, script) for every `run:` step of a workflow."""
    for job_name, job in (workflow.get("jobs") or {}).items():
        for step in (job or {}).get("steps") or []:
            if not isinstance(step, dict):
                continue
            script = step.get("run")
            if isinstance(script, str):
                yield step_label(job_name, step), script


def is_code(line: str) -> bool:
    """Whether a line of shell holds anything to judge, comments aside."""
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def wait_for_device_calls(script: str):
    """Yield (line number, text, bounded) for each `wait-for-device` call."""
    for number, line in enumerate(script.split("\n"), start=1):
        if not is_code(line) or WAIT_FOR_DEVICE not in line:
            continue
        before = line.split(WAIT_FOR_DEVICE, 1)[0]
        command = COMMAND_SEPARATOR.split(before)[-1]
        yield number, line.strip(), bool(TIMEOUT_PREFIX.search(command))


def loop_body(lines: list[str], header_index: int, indent: str) -> list[str] | None:
    """Return the body of the loop opened at `header_index`, or None if unclosed.

    The loop ends at the first `done` indented exactly as its header is, so a
    nested loop's own `done`, indented deeper, does not close it.
    """
    for index in range(header_index + 1, len(lines)):
        if lines[index].startswith(f"{indent}done"):
            return lines[header_index + 1 : index]
    return None


def device_poll_loops(script: str):
    """Yield (line number, text, bounded) for each loop polling the device."""
    lines = script.split("\n")
    for index, line in enumerate(lines):
        header = LOOP_HEADER.match(line)
        if header is None or not is_code(line) or not ADB_CALL.search(line):
            continue
        body = loop_body(lines, index, header.group("indent"))
        if body is None:
            yield index + 1, f"{line.strip()} (no `done` closes it)", False
            continue
        bounded = any(BOUND_CHECK.search(entry) for entry in body if is_code(entry))
        yield index + 1, line.strip(), bounded


def violations(workflow: dict) -> list[str]:
    """Return a message for each wait in a workflow that has no bound of its own."""
    found: list[str] = []
    for label, script in run_blocks(workflow):
        for number, text, bounded in wait_for_device_calls(script):
            if not bounded:
                found.append(
                    f"{label}, line {number} of its run block, calls {WAIT_FOR_DEVICE} "
                    f"under no `timeout`, so a device that never returns is reported as "
                    f"the enclosing step or job running out of time: {text}"
                )
        for number, text, bounded in device_poll_loops(script):
            if not bounded:
                found.append(
                    f"{label}, line {number} of its run block, polls the device in a loop "
                    f"that checks no elapsed time against a bound, so only the enclosing "
                    f"step or job can end it: {text}"
                )
    return found


class WorkflowDeviceWaitsTest(unittest.TestCase):
    """Every workflow in this repository obeys the rule."""

    def test_every_device_wait_carries_its_own_bound(self):
        paths = workflow_paths()
        self.assertTrue(paths, "found no workflow files to check")
        for path in paths:
            rel = relative(path)
            with self.subTest(workflow=rel):
                found = violations(load_workflow(path))
                self.assertEqual([], found, f"{rel}: " + "; ".join(found))

    def test_the_tree_still_holds_a_wait_of_each_shape(self):
        """A guard over shapes the tree had stopped writing would pass on an empty set."""
        waits, polls = [], []
        for path in workflow_paths():
            for _, script in run_blocks(load_workflow(path)):
                waits.extend(wait_for_device_calls(script))
                polls.extend(device_poll_loops(script))
        self.assertTrue(waits, f"no workflow calls {WAIT_FOR_DEVICE}")
        self.assertTrue(polls, "no workflow polls the device in a loop")


class ViolationDetectionTest(unittest.TestCase):
    """The rule itself: without these, a guard that had stopped detecting
    anything would still report a clean tree."""

    def _violations(self, script: str) -> list[str]:
        workflow = {"jobs": {"build": {"steps": [{"name": "Wait", "run": script}]}}}
        return violations(workflow)

    def test_a_bare_wait_for_device_is_reported(self):
        found = self._violations('"$ADB" wait-for-device\n')
        self.assertEqual(1, len(found), found)
        self.assertIn("under no `timeout`", found[0])

    def test_a_wait_for_device_under_timeout_is_accepted(self):
        self.assertEqual([], self._violations('timeout 120 "$ADB" wait-for-device\n'))

    def test_a_wait_for_device_inside_an_if_is_accepted(self):
        self.assertEqual([], self._violations('if timeout 120 "$ADB" wait-for-device; then\n'))

    def test_a_timeout_with_options_and_a_unit_is_accepted(self):
        self.assertEqual([], self._violations('timeout -k 5s 20m "$ADB" wait-for-device\n'))

    def test_a_timeout_on_an_earlier_command_is_not_credited(self):
        found = self._violations('timeout 5 "$ADB" get-state; "$ADB" wait-for-device\n')
        self.assertEqual(1, len(found), found)

    def test_a_commented_out_wait_is_ignored(self):
        self.assertEqual([], self._violations('# "$ADB" wait-for-device\n'))

    def test_an_unbounded_device_poll_is_reported(self):
        found = self._violations(
            'until [[ "$("$ADB" shell getprop sys.boot_completed)" == "1" ]]; do\n  sleep 5\ndone\n'
        )
        self.assertEqual(1, len(found), found)
        self.assertIn("checks no elapsed time", found[0])

    def test_a_device_poll_with_an_elapsed_check_is_accepted(self):
        self.assertEqual(
            [],
            self._violations(
                'until [[ "$("$ADB" shell getprop sys.boot_completed)" == "1" ]]; do\n'
                "  if [[ $BOOTWAIT -ge 180 ]]; then\n"
                "    exit 1\n"
                "  fi\n"
                "  sleep 5\n"
                "  BOOTWAIT=$((BOOTWAIT + 5))\n"
                "done\n"
            ),
        )

    def test_a_bound_named_only_in_a_comment_does_not_count(self):
        found = self._violations(
            'until "$ADB" shell settings get global airplane_mode_on; do\n'
            "  # give up at -ge 120 one day\n"
            "  sleep 5\n"
            "done\n"
        )
        self.assertEqual(1, len(found), found)

    def test_a_nested_loop_does_not_close_the_poll_it_sits_in(self):
        # The inner `done` is indented deeper, so the outer loop's body, and the
        # bound missing from it, is read to the end.
        found = self._violations(
            "until adb shell getprop sys.boot_completed; do\n"
            "  for i in $(seq 1 3); do\n"
            "    sleep 1\n"
            "  done\n"
            "done\n"
        )
        self.assertEqual(1, len(found), found)

    def test_a_comparison_that_bounds_nothing_is_credited_as_a_bound(self):
        # The other blind spot, pinned for the same reason: `-gt` here compares a
        # retry budget the body never counts up, so the loop still cannot give up,
        # and the scan credits the comparison anyway. See "Limits" above.
        self.assertEqual(
            [],
            self._violations(
                'until "$ADB" shell getprop sys.boot_completed; do\n'
                "  if [[ $RETRY_BUDGET -gt 0 ]]; then echo hi; fi\n"
                "  sleep 5\n"
                "done\n"
            ),
        )

    def test_a_bound_nested_inside_the_body_is_credited_to_the_poll_around_it(self):
        # A blind spot, pinned here so it is a known one: the `-ge` bounds the
        # inner loop and cannot end the outer one, but the scan reads the body as
        # text and credits it. See "Limits" above for why it is left standing.
        self.assertEqual(
            [],
            self._violations(
                "until adb shell getprop sys.boot_completed; do\n"
                "  while [[ $I -ge 3 ]]; do\n"
                "    sleep 1\n"
                "  done\n"
                "done\n"
            ),
        )

    def test_an_unclosed_loop_is_reported_rather_than_passed(self):
        found = self._violations("until adb get-state; do\n  sleep 5\n")
        self.assertEqual(1, len(found), found)
        self.assertIn("no `done` closes it", found[0])

    def test_a_loop_that_drives_no_adb_is_ignored(self):
        self.assertEqual(
            [], self._violations('while IFS= read -r dir; do\n  echo "$dir"\ndone < list\n')
        )

    def test_a_poll_driving_adb_by_its_path_is_reported(self):
        # The form every step of build.yml assigns $ADB from. Read as nothing at
        # all until the lookbehind stopped excluding a path separator, which let
        # a loop with no bound of any kind report clean.
        found = self._violations(
            'until [[ "$($ANDROID_HOME/platform-tools/adb shell getprop sys.boot_completed)" '
            '== "1" ]]; do\n'
            "  sleep 5\n"
            "done\n"
        )
        self.assertEqual(1, len(found), found)
        self.assertIn("checks no elapsed time", found[0])

    def test_a_bounded_poll_driving_adb_by_its_path_is_accepted(self):
        self.assertEqual(
            [],
            self._violations(
                "until $ANDROID_HOME/platform-tools/adb shell true; do\n"
                "  if [[ $WAITED -ge 180 ]]; then\n"
                "    exit 1\n"
                "  fi\n"
                "  sleep 5\n"
                "  WAITED=$((WAITED + 5))\n"
                "done\n"
            ),
        )

    def test_a_word_ending_in_adb_is_not_an_adb_call(self):
        self.assertEqual([], self._violations("while read-adb line; do\n  sleep 1\ndone\n"))

    def test_a_step_with_no_run_block_is_ignored(self):
        workflow = yaml.safe_load(
            "jobs:\n  build:\n    steps:\n      - uses: actions/checkout@v7\n"
        )
        self.assertEqual([], violations(workflow))

    def test_an_unnamed_step_is_still_reported(self):
        workflow = yaml.safe_load("jobs:\n  build:\n    steps:\n      - run: adb wait-for-device\n")
        found = violations(workflow)
        self.assertEqual(1, len(found), found)
        self.assertIn("unnamed run step", found[0])


if __name__ == "__main__":
    unittest.main()
