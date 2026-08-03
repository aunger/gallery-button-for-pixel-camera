#!/usr/bin/env python3
"""Summarize JUnit XML test results as a GitHub Job Summary Markdown table.

Reads TEST-*.xml files from one or more result directories, groups test cases
by class name, and writes a pass/fail table to $GITHUB_STEP_SUMMARY (falling
back to stdout when the env var is not set).

Usage:
    python3 scripts/ci/test-support/summarize_test_results.py \\
        path/to/unit-results          --suite-label "Unit Tests" \\
        path/to/instrumented-results  --suite-label "Instrumented Tests" --outcome failure \\
        path/to/e2e-results           --suite-label "E2E Tests"        --outcome skipped

Each directory path must be immediately followed by --suite-label <name>, with
an optional trailing --outcome <value> carrying the GitHub Actions step outcome
for the step that produced those results.  When a step was skipped (for example
because a CI pre-flight failure skipped the whole test phase) it leaves no JUnit
XML behind; reporting that empty suite as "no results" reads as if nothing was
wrong, so the outcome lets the summary render it as skipped instead.

Exit code is always 0 (display only; failures are surfaced by earlier steps).
"""

import os
import sys
import defusedxml.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class TestCase:
    name: str
    passed: bool
    skipped: bool = False


@dataclass
class TestClass:
    name: str
    cases: list[TestCase] = field(default_factory=list)

    @property
    def any_failed(self) -> bool:
        return any(not tc.passed and not tc.skipped for tc in self.cases)


@dataclass(frozen=True)
class SuiteSpec:
    """One test suite to summarize: where its results live and how its step fared."""

    directory: Path
    label: str
    # The GitHub Actions step ``outcome`` for the step that produced these
    # results (``success``, ``failure``, ``skipped`` or empty/unknown). When a
    # step is ``skipped`` (e.g. a CI pre-flight failure skipped the whole test
    # phase) it writes no JUnit XML, so the suite is rendered as skipped rather
    # than as an empty/green "no results" suite. Empty means "outcome unknown".
    outcome: str = ""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_directory(directory: Path) -> dict[str, TestClass]:
    """Parse all TEST-*.xml files in *directory* and return classes keyed by name."""
    classes: dict[str, TestClass] = {}

    xml_files = sorted(directory.glob("TEST-*.xml"))
    for xml_path in xml_files:
        try:
            tree = ET.parse(xml_path)
        except ET.ParseError as exc:
            print(f"  Warning: could not parse {xml_path}: {exc}", file=sys.stderr)
            continue

        root = tree.getroot()
        if root.tag == "testsuites":
            suite_elements = root.findall("testsuite")
        else:
            suite_elements = [root]

        for suite in suite_elements:
            class_name = suite.get("name", xml_path.stem)
            if class_name not in classes:
                classes[class_name] = TestClass(name=class_name)

            for tc in suite.findall("testcase"):
                tc_name = tc.get("name", "<unknown>")
                skipped = tc.find("skipped") is not None
                failed = not skipped and (
                    tc.find("failure") is not None or tc.find("error") is not None
                )
                classes[class_name].cases.append(
                    TestCase(name=tc_name, passed=not failed and not skipped, skipped=skipped)
                )

    return classes


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def render_class(cls: "TestClass") -> tuple[list[str], int, int, int]:
    """Render one class as a collapsible ``<details>`` block.

    Returns ``(lines, pass_count, fail_count, skip_count)``.

    Classes that fully succeeded start collapsed; classes with any failures
    start expanded (``<details open>``), so attention is drawn to them without
    the user needing to click through every class.
    """
    any_passed = any(tc.passed for tc in cls.cases)
    if cls.any_failed:
        class_icon = "❌ FAIL"
    elif not any_passed:
        class_icon = "⏭ SKIP"
    else:
        class_icon = "✅ PASS"

    open_attr = " open" if cls.any_failed else ""

    lines: list[str] = []
    lines.append(f"<details{open_attr}>")
    lines.append(f"<summary>{class_icon} <strong>{cls.name}</strong></summary>")
    lines.append("")
    lines.append("| Status | Test |")
    lines.append("|--------|------|")

    pass_count = 0
    fail_count = 0
    skip_count = 0
    for tc in cls.cases:
        if tc.skipped:
            tc_icon = "⏭ SKIP"
            skip_count += 1
        elif tc.passed:
            tc_icon = "✅ PASS"
            pass_count += 1
        else:
            tc_icon = "❌ FAIL"
            fail_count += 1
        lines.append(f"| {tc_icon} | `{tc.name}` |")

    lines.append("")
    lines.append("</details>")
    lines.append("")
    return lines, pass_count, fail_count, skip_count


def render_suite(label: str, classes: dict[str, TestClass], outcome: str = "") -> list[str]:
    """Render one suite as Markdown lines (header + collapsible class blocks + totals).

    *outcome* is the GitHub Actions step outcome for the step that produced
    these results.  When it is ``skipped`` and there are no results, the suite
    is rendered as skipped rather than as an empty "no results" suite, so a
    skipped test phase (e.g. after a pre-flight failure) never reads as green.
    """
    lines: list[str] = []
    lines.append(f"### {label}")

    if not classes:
        if outcome == "skipped":
            lines.append(
                "⏭ SKIPPED--the test step did not run "
                "(e.g. a pre-flight failure skipped the test phase)."
            )
        else:
            lines.append("_No test results found._")
        lines.append("")
        return lines

    total_pass = 0
    total_fail = 0
    total_skip = 0

    for cls in classes.values():
        class_lines, pass_count, fail_count, skip_count = render_class(cls)
        lines.extend(class_lines)
        total_pass += pass_count
        total_fail += fail_count
        total_skip += skip_count

    total = total_pass + total_fail + total_skip
    skip_str = f", {total_skip} skipped" if total_skip else ""
    lines.append("")
    lines.append(f"**Total: {total_pass} passed, {total_fail} failed{skip_str}** ({total} tests)")
    lines.append("")
    return lines


def build_markdown(suite_data: list[tuple[str, dict[str, TestClass], str]]) -> str:
    """Build the full Markdown document for all suites.

    Each suite tuple is ``(label, classes, outcome)`` where *outcome* is the
    GitHub Actions step outcome (empty when unknown).
    """
    lines: list[str] = ["## Test Results", ""]
    for label, classes, outcome in suite_data:
        lines.extend(render_suite(label, classes, outcome))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_USAGE = (
    "usage: summarize_test_results.py "
    "<dir> --suite-label <name> [--outcome <value>] "
    "[<dir> --suite-label <name> [--outcome <value>] ...]"
)


def parse_args(argv: list[str]) -> list[SuiteSpec]:
    """Parse argv into a list of SuiteSpec.

    Each suite is ``<dir> --suite-label <name>`` with an optional trailing
    ``--outcome <value>`` carrying the GitHub Actions step outcome.

    Raises SystemExit(2) on bad input (matching argparse convention).
    """
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        print(_USAGE)
        raise SystemExit(0)

    specs: list[SuiteSpec] = []
    tokens = list(argv)
    i = 0
    while i < len(tokens):
        directory = tokens[i]
        # We need tokens[i], tokens[i+1], and tokens[i+2] to all be present.
        if i + 3 > len(tokens) or tokens[i + 1] != "--suite-label":
            print(
                f"error: expected --suite-label <name> after '{directory}'\n{_USAGE}",
                file=sys.stderr,
            )
            raise SystemExit(2)
        label = tokens[i + 2]
        i += 3

        outcome = ""
        if i < len(tokens) and tokens[i] == "--outcome":
            if i + 1 >= len(tokens):
                print(
                    f"error: --outcome requires a value (suite '{label}')\n{_USAGE}",
                    file=sys.stderr,
                )
                raise SystemExit(2)
            outcome = tokens[i + 1]
            i += 2

        specs.append(SuiteSpec(Path(directory), label, outcome))

    if not specs:
        print(
            f"error: at least one <directory> --suite-label <name> spec is required\n{_USAGE}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    return specs


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    specs = parse_args(argv)

    suite_data: list[tuple[str, dict[str, TestClass], str]] = []
    for spec in specs:
        directory = spec.directory
        if not directory.exists():
            print(
                f"Note: result directory not found, skipping: {directory}",
                file=sys.stderr,
            )
            suite_data.append((spec.label, {}, spec.outcome))
            continue
        if not directory.is_dir():
            print(
                f"Note: path is not a directory, skipping: {directory}",
                file=sys.stderr,
            )
            suite_data.append((spec.label, {}, spec.outcome))
            continue

        classes = parse_directory(directory)
        if not classes:
            print(
                f"Note: no TEST-*.xml files found in {directory}",
                file=sys.stderr,
            )
        suite_data.append((spec.label, classes, spec.outcome))

    markdown = build_markdown(suite_data)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(markdown)
            fh.write("\n")
    else:
        print(markdown)

    return 0


if __name__ == "__main__":
    sys.exit(main())
