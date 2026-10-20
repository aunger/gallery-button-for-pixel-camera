#!/usr/bin/env python3
"""Find the test failures an emulator stall explains (issue #1334).

When a system or launcher process stops responding, Android raises an "isn't
responding" dialog over whatever test is running.
The dialog takes window focus and dims the screen, so tests fail with no fault
in the app: Espresso reports ``RootViewWithoutFocusException``, and the
green-feed checks read 0% coverage.
Nothing in the instrumented or E2E steps dismisses the dialog, so it stays up
for every test that follows.

The CI workflow records logcat through those steps, keeping only ActivityManager
errors and TestRunner lines (the "Start emulator-stall logcat recorder" step in
``.github/workflows/build.yml``).
This module reads that recording, finds the first ANR record whose process is a
system or launcher process, and names every test the recording shows running
after it.
``check_allowed_failures.py`` reports those tests' failures as infrastructure
failures, and the issue filer files no ticket for them.

Only the processes in ``STALL_PROCESSES`` qualify:

- An ANR in one of this repository's own packages never does.
  The mock camera's "Application does not have a focused window" ANR is also
  what a GB4PC overlay window that took focus would produce, so counting it
  could pass off a real overlay bug as a stall.
- Other system apps are left out because an ANR in a background app normally
  raises no dialog, so its record says nothing about what was on screen.

A failure in a test that ended before the first qualifying record is not
counted, and neither is any failure when the recording is missing or holds no
qualifying record.
Both fall back to treating the failure as a test failure.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# Process names, as an ActivityManager "ANR in" record gives them, whose ANR
# dialog can cover a running test. A ``:suffix`` naming a secondary process of
# the same package is ignored.
STALL_PROCESSES = frozenset(
    {
        "system",  # system_server
        "com.android.systemui",
        "com.google.android.apps.nexuslauncher",  # Pixel Launcher, on the CI image
        "com.android.launcher3",  # the AOSP launcher
    }
)

# `10-17 13:47:41.900   558  5485 E ActivityManager: ANR in com.example (com.example/.Main)`
_ANR = re.compile(r"\bActivityManager\s*: ANR in (?P<process>\S+)")

# `10-17 13:47:35.787  4982  4998 I TestRunner: finished: someTest(com.example.FooTest)`
# AndroidJUnitRunner writes `started:`, `failed:` and `finished:` lines naming
# each test, and `run started:` lines that this does not match.
_TEST = re.compile(
    r"\bTestRunner\s*: (?:started|failed|finished): (?P<method>.+)\((?P<cls>[^()]+)\)\s*$"
)


@dataclass(frozen=True)
class StallEvidence:
    """The first qualifying ANR record, and the tests that ran after it."""

    anr_record: str
    # ``class#method`` of every test named by a TestRunner line after the record.
    stalled_tests: frozenset[str]


def is_stall_process(process: str) -> bool:
    """True when an ANR in *process* can raise a dialog over a running test."""
    return process.split(":", 1)[0] in STALL_PROCESSES


def find_stall(lines: Iterable[str]) -> StallEvidence | None:
    """Return the stall evidence in a logcat recording, or None if it holds none."""
    anr_record: str | None = None
    stalled: set[str] = set()
    for line in lines:
        if anr_record is None:
            anr = _ANR.search(line)
            if anr and is_stall_process(anr["process"]):
                anr_record = line.strip()
            continue
        test = _TEST.search(line)
        if test:
            stalled.add(f"{test['cls']}#{test['method']}")
    if anr_record is None:
        return None
    return StallEvidence(anr_record=anr_record, stalled_tests=frozenset(stalled))


def read_stall_evidence(path: Path) -> StallEvidence | None:
    """Read the recording at *path*; None when it is missing or holds no stall."""
    if not path.is_file():
        return None
    with path.open(encoding="utf-8", errors="replace") as recording:
        return find_stall(recording)
