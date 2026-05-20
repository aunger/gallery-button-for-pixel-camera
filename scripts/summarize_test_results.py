#!/usr/bin/env python3
"""Summarize JUnit XML test results as a GitHub Job Summary Markdown table.

Reads TEST-*.xml files from one or more result directories, groups test cases
by class name, and writes a pass/fail table to $GITHUB_STEP_SUMMARY (falling
back to stdout when the env var is not set).

Usage:
    python3 scripts/summarize_test_results.py \\
        path/to/unit-results       --suite-label "Unit Tests" \\
        path/to/e2e-results        --suite-label "Instrumented Tests"

Each directory path must be immediately followed by --suite-label <name>.
Exit code is always 0 (display only; failures are surfaced by earlier steps).
"""

import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class TestCase:
    name: str
    passed: bool


@dataclass
class TestClass:
    name: str
    cases: list[TestCase] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(tc.passed for tc in self.cases)


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
                failed = (
                    tc.find("failure") is not None
                    or tc.find("error") is not None
                )
                classes[class_name].cases.append(
                    TestCase(name=tc_name, passed=not failed)
                )

    return classes


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def render_suite(label: str, classes: dict[str, TestClass]) -> list[str]:
    """Render one suite as Markdown lines (header + table rows + totals)."""
    lines: list[str] = []
    lines.append(f"### {label}")

    if not classes:
        lines.append("_No test results found._")
        lines.append("")
        return lines

    lines.append("| Status | Suite / Test |")
    lines.append("|--------|--------------|")

    total_pass = 0
    total_fail = 0

    for cls in classes.values():
        class_icon = "✅ PASS" if cls.passed else "❌ FAIL"
        lines.append(f"| {class_icon} | **{cls.name}** |")
        for tc in cls.cases:
            tc_icon = "✅ PASS" if tc.passed else "❌ FAIL"
            lines.append(f"| {tc_icon} |     `{tc.name}` |")
            if tc.passed:
                total_pass += 1
            else:
                total_fail += 1

    total = total_pass + total_fail
    lines.append("")
    lines.append(f"**Total: {total_pass} passed, {total_fail} failed** ({total} tests)")
    lines.append("")
    return lines


def build_markdown(suite_data: list[tuple[str, dict[str, TestClass]]]) -> str:
    """Build the full Markdown document for all suites."""
    lines: list[str] = ["## Test Results", ""]
    for label, classes in suite_data:
        lines.extend(render_suite(label, classes))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_USAGE = (
    "usage: summarize_test_results.py "
    "<dir> --suite-label <name> [<dir> --suite-label <name> ...]"
)


def parse_args(argv: list[str]) -> list[tuple[Path, str]]:
    """Parse argv into a list of (directory, label) pairs.

    Expected pattern: <dir> --suite-label <name> [<dir> --suite-label <name> ...]

    Raises SystemExit(2) on bad input (matching argparse convention).
    """
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        print(_USAGE)
        raise SystemExit(0)

    pairs: list[tuple[Path, str]] = []
    tokens = list(argv)
    i = 0
    while i < len(tokens):
        directory = tokens[i]
        # We need tokens[i], tokens[i+1], and tokens[i+2] to all be present.
        if i + 3 > len(tokens):
            print(
                f"error: expected --suite-label <name> after '{directory}'\n{_USAGE}",
                file=sys.stderr,
            )
            raise SystemExit(2)
        flag = tokens[i + 1]
        label = tokens[i + 2]
        if flag != "--suite-label":
            print(
                f"error: expected --suite-label after '{directory}', got '{flag}'\n{_USAGE}",
                file=sys.stderr,
            )
            raise SystemExit(2)
        pairs.append((Path(directory), label))
        i += 3

    if not pairs:
        print(
            f"error: at least one <directory> --suite-label <name> pair is required\n{_USAGE}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    return pairs


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    pairs = parse_args(argv)

    suite_data: list[tuple[str, dict[str, TestClass]]] = []
    for directory, label in pairs:
        if not directory.exists():
            print(
                f"Note: result directory not found, skipping: {directory}",
                file=sys.stderr,
            )
            suite_data.append((label, {}))
            continue
        if not directory.is_dir():
            print(
                f"Note: path is not a directory, skipping: {directory}",
                file=sys.stderr,
            )
            suite_data.append((label, {}))
            continue

        classes = parse_directory(directory)
        if not classes:
            print(
                f"Note: no TEST-*.xml files found in {directory}",
                file=sys.stderr,
            )
        suite_data.append((label, classes))

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
