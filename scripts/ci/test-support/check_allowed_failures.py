#!/usr/bin/env python3
"""Gate a CI run on test failures, tolerating an explicit allowlist.

This replaces the blanket ``continue-on-error: true`` previously applied to the
test steps in ``.github/workflows/build.yml``.  ``continue-on-error`` swallowed
*every* error, including real infrastructure failures (for example the broken
``date`` invocation in issue #307), so the build went green while genuine
breakage went unnoticed.

Instead, this script reads the JUnit XML produced by the test runs and fails the
build when a test fails, *unless* that test is named in an allowlist file.  The
allowlist exists only to let a few known-red tests stay red during a deliberate
red-to-green effort; everything else, including unexpected errors, still fails CI.

Allowlist format (one entry per line):
    com.gb4pc.e2e.GalleryButtonVisualE2ETest#someFlakyMethod   # whole method
    com.gb4pc.e2e.GalleryButtonVisualE2ETest                   # whole class
Blank lines and lines beginning with ``#`` are ignored.  A trailing inline
``#`` comment (one preceded by whitespace) is stripped, while the ``#`` that
separates a class from a method is preserved.

Usage:
    python3 scripts/ci/test-support/check_allowed_failures.py \\
        --allowlist .github/allowed-test-failures.txt \\
        path/to/unit-results          --suite-label "Unit Tests" \\
        path/to/instrumented-results  --suite-label "Instrumented Tests" --outcome failure \\
        path/to/e2e-results           --suite-label "E2E Tests"        --outcome success

Each directory path must be immediately followed by --suite-label <name>, with
an optional ``--outcome <value>`` carrying the GitHub Actions step outcome for
the step that produced those results.  When a step's outcome is ``failure`` but
it yielded no blocking test failure, the gate treats that as an infrastructure
failure and fails the build--this is what ``continue-on-error`` used to hide.

Exit codes:
    0  no failures, or every failure is allowlisted
    1  at least one non-allowlisted test failed, or an infrastructure failure
"""

import re
import sys
import defusedxml.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


# An inline comment is a `#` that follows whitespace (or starts the line).
# A bare `#` glued to the preceding token is the class/method separator
# (``com.example.FooTest#someMethod``) and must NOT be treated as a comment.
_INLINE_COMMENT = re.compile(r"(?:^|\s)#.*$")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FailedTest:
    class_name: str
    method_name: str
    suite_label: str

    @property
    def qualified(self) -> str:
        return f"{self.class_name}#{self.method_name}"


@dataclass(frozen=True)
class SuiteSpec:
    """One test suite to inspect: where its results live and how its step fared."""

    directory: Path
    label: str
    # The GitHub Actions step ``outcome`` for the step that produced these
    # results (``success``, ``failure``, ``skipped`` or empty/unknown). Used to
    # catch *non-test* failures, for example a step that aborted before any
    # JUnit XML was written (the ``date`` breakage in issue #307). Empty means
    # "outcome unknown--judge on XML alone".
    outcome: str = ""


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------


def load_allowlist(path: Path | None) -> set[str]:
    """Return the set of allowlist entries (class names and class#method names).

    Returns an empty set when *path* is None or does not exist; a missing
    allowlist simply means no failure is tolerated.
    """
    if path is None or not path.exists():
        return set()

    entries: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        # Drop whitespace-preceded inline comments, then surrounding whitespace.
        # A `#` glued to the preceding token is the class#method separator and
        # is preserved (e.g. ``com.example.FooTest#someMethod``).
        line = _INLINE_COMMENT.sub("", raw_line).strip()
        if line:
            entries.add(line)
    return entries


def is_allowed(failure: FailedTest, allowlist: set[str]) -> bool:
    """True when *failure* is tolerated by *allowlist* (class- or method-level)."""
    return failure.class_name in allowlist or failure.qualified in allowlist


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_failures(directory: Path, suite_label: str) -> list[FailedTest]:
    """Return one FailedTest for every failing test case found in *directory*."""
    failures: list[FailedTest] = []

    xml_files = sorted(directory.glob("**/TEST-*.xml"))
    for xml_path in xml_files:
        try:
            tree = ET.parse(xml_path)
        except ET.ParseError as exc:
            print(f"  Warning: could not parse {xml_path}: {exc}", file=sys.stderr)
            continue

        root = tree.getroot()
        suite_elements = root.findall("testsuite") if root.tag == "testsuites" else [root]

        for suite in suite_elements:
            class_name = suite.get("name", xml_path.stem)
            for tc in suite.findall("testcase"):
                method_name = tc.get("name", "<unknown>")
                if tc.find("skipped") is not None:
                    continue
                if tc.find("failure") is None and tc.find("error") is None:
                    continue
                failures.append(
                    FailedTest(
                        class_name=class_name,
                        method_name=method_name,
                        suite_label=suite_label,
                    )
                )

    return failures


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_USAGE = (
    "usage: check_allowed_failures.py [--allowlist <file>] "
    "<dir> --suite-label <name> [--outcome <value>] "
    "[<dir> --suite-label <name> [--outcome <value>] ...]"
)


def parse_args(argv: list[str]) -> tuple[Path | None, list[SuiteSpec]]:
    """Parse argv into (allowlist_path, [SuiteSpec, ...]).

    Each suite is ``<dir> --suite-label <name>`` with an optional trailing
    ``--outcome <value>``. Raises SystemExit(2) on bad input (matching the
    argparse convention).
    """
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        print(_USAGE)
        raise SystemExit(0)

    tokens = list(argv)
    allowlist_path: Path | None = None
    if tokens and tokens[0] == "--allowlist":
        if len(tokens) < 2:
            print(f"error: --allowlist requires a path\n{_USAGE}", file=sys.stderr)
            raise SystemExit(2)
        allowlist_path = Path(tokens[1])
        tokens = tokens[2:]

    specs: list[SuiteSpec] = []
    i = 0
    while i < len(tokens):
        directory = tokens[i]
        if i + 2 >= len(tokens) or tokens[i + 1] != "--suite-label":
            print(
                f"error: expected --suite-label <name> after '{directory}'\n{_USAGE}",
                file=sys.stderr,
            )
            raise SystemExit(2)
        label = tokens[i + 2]
        i += 3

        outcome = ""
        if i + 1 < len(tokens) and tokens[i] == "--outcome":
            outcome = tokens[i + 1]
            i += 2
        elif i < len(tokens) and tokens[i] == "--outcome":
            print(
                f"error: --outcome requires a value (suite '{label}')\n{_USAGE}",
                file=sys.stderr,
            )
            raise SystemExit(2)

        specs.append(SuiteSpec(Path(directory), label, outcome))

    if not specs:
        print(
            f"error: at least one <directory> --suite-label <name> spec is required\n{_USAGE}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    return allowlist_path, specs


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    allowlist_path, specs = parse_args(argv)
    allowlist = load_allowlist(allowlist_path)

    all_failures: list[FailedTest] = []
    infra_failures: list[str] = []

    for spec in specs:
        if not spec.directory.is_dir():
            suite_failures: list[FailedTest] = []
            print(
                f"Note: result directory not found, skipping: {spec.directory}",
                file=sys.stderr,
            )
        else:
            suite_failures = parse_failures(spec.directory, spec.label)
            all_failures.extend(suite_failures)

        # A step that reported `failure` but produced no failing test at all
        # failed for a *non-test* reason (e.g. the broken `date` invocation in
        # issue #307). A genuine test failure--even an allowlisted one--
        # explains the outcome; an empty result set does not. The allowlist
        # tolerates flaky *tests*, never silent infrastructure breakage, so an
        # unexplained step failure still fails the build.
        if spec.outcome == "failure" and not suite_failures:
            infra_failures.append(spec.label)

    allowed = [f for f in all_failures if is_allowed(f, allowlist)]
    blocked = [f for f in all_failures if not is_allowed(f, allowlist)]

    if allowed:
        print("Allowed (red-to-green) test failures:")
        for f in allowed:
            print(f"  - [{f.suite_label}] {f.qualified}")

    if blocked:
        print("Blocking test failures (not on allowlist):")
        for f in blocked:
            print(f"  - [{f.suite_label}] {f.qualified}")

    if infra_failures:
        print("Non-test (infrastructure) step failures:")
        for label in infra_failures:
            print(f"  - [{label}] step failed without a corresponding test failure")

    if blocked or infra_failures:
        print(
            f"\n{len(blocked)} blocking test failure(s); "
            f"{len(infra_failures)} infrastructure failure(s); "
            f"{len(allowed)} allowlisted failure(s).",
            file=sys.stderr,
        )
        return 1

    if allowed:
        print(f"\nAll {len(allowed)} failure(s) are allowlisted; not failing the build.")
    else:
        print("No test failures.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
