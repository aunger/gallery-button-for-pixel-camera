#!/usr/bin/env python3
"""Guard: a `timeout` in a workflow step is small enough that it can fire.

A bound only names the wait it belongs to if the wait reaches it first. A
`timeout` larger than the budget of the step holding it never does: the runner
kills the step, the run reports the step running out of time, and the bound's
own message, with whatever diagnosis it prints, is never reached. The wait is
bounded on paper and unbounded in effect.

Issue #1162 found one in `build.yml`: `timeout 1200` on the `wait-for-device`
of "Wait for emulator service readiness", a step declaring `timeout-minutes:
10`. Every emulator that failed to appear on adb was reported as that step
expiring, and the emulator-log tail that `timeout` guarded never ran. The wait
now asks for 240 seconds of the step's 600.

The rule, over every `run:` step of every workflow: each `timeout DURATION`
must be strictly less than the budget the step runs under, which is the step's
own `timeout-minutes` where it sets one, the job's where it does not, and
GitHub's own 6-hour default for a job that sets neither.

Strictly less, not at most: a bound equal to its enclosing budget is a race
between two clocks started at different moments, and the enclosing one is
already ahead.

Limits
------

It reads the durations a step writes literally. `timeout "$BUDGET"` is not
judged, and neither is a step or job whose `timeout-minutes` is an expression,
since what that evaluates to is not in the file.

It judges one `timeout` at a time and never adds up the waits in a step. Two
waits of 400 seconds each in a 10-minute step pass here and cannot both fire.
Nor does it see a bound written as a loop counting elapsed seconds, the other
shape this tree writes a wait in, so the room a `timeout` leaves for the loops
after it is a judgement for whoever writes the step, recorded in its comments.

What it does catch is the shape that has already happened here: a single
literal bound set larger than the budget enclosing it, which reads as generous
and is inert.

It says nothing about whether a bound is long enough for the work, which no
file in this tree records, and nothing about waits inside the scripts a
workflow calls, which those scripts' own suites cover by running them.
"""

import re
import unittest

import yaml
from workflow_files import load_workflow, relative, workflow_paths

# What GitHub kills a job at when it declares no `timeout-minutes` of its own.
DEFAULT_JOB_TIMEOUT_MINUTES = 360

# GNU timeout's duration suffixes. A bare number is seconds.
SUFFIX_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

# `timeout` as a command, rather than as the tail of `--timeout`, `read-timeout`
# or a path ending in it. The excluded set holds what would make it part of a
# longer word; `timeout-minutes` is excluded by the trailing `\s` alone, and
# comment lines are dropped before this runs anyway.
TIMEOUT_CALL = re.compile(r"(?<![\w.\-/])timeout(?=\s)")

# The options of GNU timeout that take their argument as a separate word, so
# that the word after them is not mistaken for the duration. The joined spellings
# (`-k5s`, `--kill-after=5s`) need no entry: they are one word starting with `-`.
OPTIONS_WITH_ARGUMENT = frozenset({"-k", "-s", "--kill-after", "--signal"})

# A duration written out, as opposed to `"$SOMETHING"`.
LITERAL_DURATION = re.compile(r"^(\d+(?:\.\d+)?)([smhd]?)$")


def is_code(line: str) -> bool:
    """Whether a line of shell holds anything to judge, comments aside."""
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def duration_seconds(token: str) -> float | None:
    """Return a GNU timeout duration in seconds, or None if it is not literal."""
    match = LITERAL_DURATION.match(token)
    if match is None:
        return None
    return float(match.group(1)) * SUFFIX_SECONDS.get(match.group(2), 1)


def timeout_calls(script: str):
    """Yield (line number, line, duration in seconds or None) per `timeout`."""
    for number, line in enumerate(script.splitlines(), start=1):
        if not is_code(line):
            continue
        for call in TIMEOUT_CALL.finditer(line):
            words = line[call.end() :].split()
            index = 0
            while index < len(words) and words[index].startswith("-"):
                option = words[index]
                index += 1
                if option in OPTIONS_WITH_ARGUMENT:
                    index += 1
            token = words[index] if index < len(words) else ""
            yield number, line.strip(), duration_seconds(token)


def budget_seconds(job: dict, step: dict) -> float | None:
    """Return the seconds the step runs under, or None where that is unreadable.

    The step's own `timeout-minutes` where it sets one, the job's where it does
    not, and GitHub's default for a job that sets neither. An expression in
    either place reads as None: what it evaluates to is not in the file.
    """
    for owner in (step, job):
        minutes = owner.get("timeout-minutes")
        if minutes is None:
            continue
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)):
            return None
        return float(minutes) * 60
    return DEFAULT_JOB_TIMEOUT_MINUTES * 60


def step_label(job_name: str, step: dict) -> str:
    """Name the step a finding came from. `name:` is optional on a step."""
    name = step.get("name")
    if isinstance(name, str) and name.strip():
        return f"job {job_name!r} step {name.strip()!r}"
    return f"job {job_name!r} unnamed run step"


def run_steps(workflow: dict):
    """Yield (job name, job, step) for every `run:` step of a workflow."""
    for job_name, job in (workflow.get("jobs") or {}).items():
        job = job or {}
        for step in job.get("steps") or []:
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                yield job_name, job, step


def violations(workflow: dict) -> list[str]:
    """Return a message for each `timeout` that its step would outlive."""
    found: list[str] = []
    for job_name, job, step in run_steps(workflow):
        budget = budget_seconds(job, step)
        if budget is None:
            continue
        where = step_label(job_name, step)
        for number, line, seconds in timeout_calls(step["run"]):
            if seconds is None or seconds < budget:
                continue
            found.append(
                f"{where}, run line {number}: `{line}` asks for {seconds:g}s "
                f"inside a step killed at {budget:g}s, so the bound cannot "
                f"fire and the wait reports as the step expiring"
            )
    return found


class ReachableWaitBoundsTest(unittest.TestCase):
    """Every workflow in this repository obeys the rule."""

    def test_every_timeout_can_fire_inside_its_step(self):
        paths = workflow_paths()
        self.assertTrue(paths, "found no workflow files to check")
        for path in paths:
            rel = relative(path)
            with self.subTest(workflow=rel):
                found = violations(load_workflow(path))
                self.assertEqual([], found, f"{rel}: " + "; ".join(found))

    def test_some_workflow_step_actually_calls_timeout(self):
        """A guard over a construct no workflow uses would pass on an empty set."""
        calls = [
            (relative(path), job_name, number)
            for path in workflow_paths()
            for job_name, _, step in run_steps(load_workflow(path))
            for number, _, _ in timeout_calls(step["run"])
        ]
        self.assertTrue(calls, "no workflow step calls `timeout`")


class ViolationDetectionTest(unittest.TestCase):
    """The rule itself: without these, a guard that had stopped detecting
    anything would still report a clean tree."""

    def _violations(self, text: str) -> list[str]:
        return violations(yaml.safe_load(text))

    def _step(self, run: str, step_timeout: str = "", job_timeout: str = "") -> str:
        return f"""
jobs:
  e2e:
{job_timeout}
    steps:
      - name: Wait
{step_timeout}
        run: |
{run}
"""

    def test_a_bound_larger_than_its_step_is_reported(self):
        found = self._violations(
            self._step(
                '          timeout 1200 "$ADB" wait-for-device',
                step_timeout="        timeout-minutes: 10",
            )
        )
        self.assertEqual(1, len(found), found)
        self.assertIn("1200s", found[0])
        self.assertIn("600s", found[0])
        self.assertIn("'Wait'", found[0])

    def test_a_bound_inside_its_step_passes(self):
        self.assertEqual(
            [],
            self._violations(
                self._step(
                    '          timeout 240 "$ADB" wait-for-device',
                    step_timeout="        timeout-minutes: 10",
                )
            ),
        )

    def test_a_bound_equal_to_its_step_is_reported(self):
        """Two clocks started at different moments, the outer one first."""
        found = self._violations(
            self._step(
                '          timeout 600 "$ADB" wait-for-device',
                step_timeout="        timeout-minutes: 10",
            )
        )
        self.assertEqual(1, len(found), found)

    def test_a_suffixed_duration_is_read_as_its_unit(self):
        found = self._violations(
            self._step(
                '          timeout 20m "$ADB" wait-for-device',
                step_timeout="        timeout-minutes: 10",
            )
        )
        self.assertEqual(1, len(found), found)
        self.assertIn("1200s", found[0])

    def test_the_argument_of_kill_after_is_not_read_as_the_duration(self):
        found = self._violations(
            self._step(
                '          timeout -k 5s 1200 "$ADB" wait-for-device',
                step_timeout="        timeout-minutes: 10",
            )
        )
        self.assertEqual(1, len(found), found)
        self.assertIn("1200s", found[0])

    def test_a_joined_option_is_not_read_as_the_duration(self):
        found = self._violations(
            self._step(
                '          timeout --kill-after=5s 1200 "$ADB" wait-for-device',
                step_timeout="        timeout-minutes: 10",
            )
        )
        self.assertEqual(1, len(found), found)
        self.assertIn("1200s", found[0])

    def test_a_step_without_its_own_budget_is_judged_against_the_job(self):
        found = self._violations(
            self._step(
                '          timeout 4000 "$ADB" wait-for-device',
                job_timeout="    timeout-minutes: 60",
            )
        )
        self.assertEqual(1, len(found), found)
        self.assertIn("3600s", found[0])

    def test_a_job_without_a_budget_is_judged_against_github_s_default(self):
        self.assertEqual(
            [],
            self._violations(self._step('          timeout 4000 "$ADB" wait-for-device')),
        )
        found = self._violations(self._step('          timeout 25h "$ADB" wait-for-device'))
        self.assertEqual(1, len(found), found)

    def test_a_commented_out_bound_is_not_judged(self):
        self.assertEqual(
            [],
            self._violations(
                self._step(
                    "          # timeout 1200 used to be the bound here",
                    step_timeout="        timeout-minutes: 10",
                )
            ),
        )

    def test_a_word_ending_in_timeout_is_not_a_call(self):
        self.assertEqual(
            [],
            self._violations(
                self._step(
                    "          ./gradlew --timeout 1200 test",
                    step_timeout="        timeout-minutes: 10",
                )
            ),
        )

    def test_a_duration_held_in_a_variable_is_not_judged(self):
        """A blind spot, pinned so that it is known rather than undiscovered."""
        self.assertEqual(
            [],
            self._violations(
                self._step(
                    '          timeout "$DEVICE_TIMEOUT" "$ADB" wait-for-device',
                    step_timeout="        timeout-minutes: 10",
                )
            ),
        )

    def test_a_budget_written_as_an_expression_is_not_judged(self):
        """The same blind spot from the other side."""
        self.assertEqual(
            [],
            self._violations(
                self._step(
                    '          timeout 1200 "$ADB" wait-for-device',
                    step_timeout="        timeout-minutes: ${{ inputs.budget }}",
                )
            ),
        )

    def test_two_waits_that_cannot_both_fire_are_not_summed(self):
        """The other blind spot: each bound is judged on its own."""
        self.assertEqual(
            [],
            self._violations(
                self._step(
                    '          timeout 400 "$ADB" wait-for-device\n'
                    '          timeout 400 "$ADB" shell true',
                    step_timeout="        timeout-minutes: 10",
                )
            ),
        )


if __name__ == "__main__":
    unittest.main()
