#!/usr/bin/env python3
"""Find the test failures an emulator stall explains (issue #1334).

A system or launcher ANR raises a dialog that takes focus from the running test.
Reading the CI's logcat recording (ActivityManager errors and TestRunner lines),
this maps each ANR in a ``STALL_PROCESSES`` process to one test: the test in
progress, or the next to start if none is.

- One test: the dialog does not reliably outlast the test it appears in.
- A test that writes nothing after the ANR is taken as dead, and the ANR goes to
  the next test, so a crashed test keeps its ticket.
- Own-package ANRs never count: a focused GB4PC overlay produces the same record.
- Other system apps are left out: a background ANR normally shows no dialog.
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
    held: str | None = None  # a record written during `running`, until it writes again
    pending: str | None = None  # a record for the next test to start
    for line in lines:
        anr = _ANR.search(line)
        if anr and is_stall_process(anr["process"]):
            if running is None:
                pending = pending or line.strip()
            else:
                held = held or line.strip()
            continue
        test = _TEST.search(line)
        if not test:
            continue
        name = f"{test['cls']}#{test['method']}"
        if test["event"] == "started":
            # `running` wrote nothing after the held record, so it had died.
            pending = pending or held
            held = None
            running = name
            if pending is not None:
                stalls.setdefault(name, pending)
                pending = None
        elif name == running:
            if held is not None:
                stalls.setdefault(name, held)
                held = None
            if test["event"] == "finished":
                running = None
    return stalls


def read_stalls(path: Path) -> dict[str, str]:
    """Read the recording at *path*; empty when it is missing or holds no stall."""
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", errors="replace") as recording:
        return find_stalls(recording)
