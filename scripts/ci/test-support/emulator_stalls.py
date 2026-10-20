#!/usr/bin/env python3
"""Find the test failures an emulator stall explains (issue #1334).

When a system or launcher process stops responding, Android raises an "isn't
responding" dialog over whatever test is running.
The dialog takes window focus and dims the screen, so tests fail with no fault
in the app: Espresso reports ``RootViewWithoutFocusException``, and the
green-feed checks read 0% coverage.

The CI workflow records logcat through the instrumented and E2E steps, keeping
only ActivityManager errors and TestRunner lines (the "Start emulator-stall
logcat recorder" step in ``.github/workflows/build.yml``).
This module reads that recording and, for each ANR record whose process is a
system or launcher process, names the one test that record covers: the test in
progress when the record was written or, when none is, the next test to start.
``check_allowed_failures.py`` reports those tests' failures as infrastructure
failures, and the issue filer files no ticket for them.

The window is one test because that is all the evidence supports.
In run 39037527808 the Pixel Launcher dialog shows in test4a's screenshots, and
the next test, test5a, passed, though it checks the green coverage the dialog's
dim breaks (``fixtures/emulator_stalls/run-39037527808-stall.txt``).
So the dialog does not reliably outlast the test it appears in, and a wider
window would withhold tickets from failures the stall did not cause.

Only the processes in ``STALL_PROCESSES`` qualify:

- An ANR in one of this repository's own packages never does.
  The mock camera's "Application does not have a focused window" ANR is also
  what a GB4PC overlay window that took focus would produce, so counting it
  could pass off a real overlay bug as a stall.
- Other system apps are left out because an ANR in a background app normally
  raises no dialog, so its record says nothing about what was on screen.

A failure in any other test stays a test failure, as does every failure when
the recording is missing or holds no qualifying record.
"""

import re
from collections.abc import Iterable
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
    r"\bTestRunner\s*: (?P<event>started|failed|finished): "
    r"(?P<method>.+)\((?P<cls>[^()]+)\)\s*$"
)


def is_stall_process(process: str) -> bool:
    """True when an ANR in *process* can raise a dialog over a running test."""
    return process.split(":", 1)[0] in STALL_PROCESSES


def find_stalls(lines: Iterable[str]) -> dict[str, str]:
    """Map ``class#method`` of each test a qualifying ANR covers to that ANR's record."""
    stalls: dict[str, str] = {}
    running: str | None = None  # the test started and not yet finished
    pending: str | None = None  # a record written while no test was running
    for line in lines:
        anr = _ANR.search(line)
        if anr and is_stall_process(anr["process"]):
            if running is None:
                pending = pending or line.strip()
            else:
                stalls.setdefault(running, line.strip())
            continue
        test = _TEST.search(line)
        if not test:
            continue
        name = f"{test['cls']}#{test['method']}"
        if test["event"] == "started":
            running = name
            if pending is not None:
                stalls.setdefault(name, pending)
                pending = None
        elif test["event"] == "finished" and running == name:
            running = None
    return stalls


def read_stalls(path: Path) -> dict[str, str]:
    """Read the recording at *path*; empty when it is missing or holds no stall."""
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", errors="replace") as recording:
        return find_stalls(recording)
