#!/usr/bin/env python3
"""Unit tests for check_allowed_failures.py."""

import io
import os
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import check_allowed_failures as caf  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(directory: Path, filename: str, content: str) -> Path:
    path = directory / filename
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


PASSING_XML = """\
    <?xml version="1.0" encoding="UTF-8"?>
    <testsuite name="com.example.FooTest" tests="2" failures="0" errors="0">
      <testcase name="testA" classname="com.example.FooTest"/>
      <testcase name="testB" classname="com.example.FooTest"/>
    </testsuite>
"""

FAILING_XML = """\
    <?xml version="1.0" encoding="UTF-8"?>
    <testsuite name="com.example.BarTest" tests="2" failures="1" errors="0">
      <testcase name="testC" classname="com.example.BarTest"/>
      <testcase name="testD" classname="com.example.BarTest">
        <failure message="AssertionError">Expected true but was false</failure>
      </testcase>
    </testsuite>
"""

ERROR_XML = """\
    <?xml version="1.0" encoding="UTF-8"?>
    <testsuite name="com.example.BazTest" tests="1" failures="0" errors="1">
      <testcase name="testE" classname="com.example.BazTest">
        <error message="NullPointerException">NPE</error>
      </testcase>
    </testsuite>
"""

SKIPPED_XML = """\
    <?xml version="1.0" encoding="UTF-8"?>
    <testsuite name="com.example.SkipTest" tests="1" failures="0" errors="0" skipped="1">
      <testcase name="testF" classname="com.example.SkipTest">
        <skipped/>
      </testcase>
    </testsuite>
"""


# ---------------------------------------------------------------------------
# Allowlist loading
# ---------------------------------------------------------------------------


class LoadAllowlistTests(unittest.TestCase):
    def test_missing_file_returns_empty_set(self):
        self.assertEqual(caf.load_allowlist(None), set())
        self.assertEqual(caf.load_allowlist(Path("/no/such/file.txt")), set())

    def test_parses_entries_and_strips_comments(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write(
                Path(d),
                "allow.txt",
                """\
                # a header comment
                com.example.BarTest#testD

                com.example.FooTest      # inline comment
                  com.example.BazTest#testE
            """,
            )
            self.assertEqual(
                caf.load_allowlist(path),
                {
                    "com.example.BarTest#testD",
                    "com.example.FooTest",
                    "com.example.BazTest#testE",
                },
            )

    def test_method_separator_hash_is_not_treated_as_comment(self):
        # The `#` separating class from method must survive, even when the line
        # also carries a whitespace-preceded inline comment.
        with tempfile.TemporaryDirectory() as d:
            path = _write(
                Path(d),
                "allow.txt",
                """\
                com.example.BarTest#testD   # flaky, see #123
            """,
            )
            self.assertEqual(
                caf.load_allowlist(path),
                {"com.example.BarTest#testD"},
            )


class IsAllowedTests(unittest.TestCase):
    def test_method_level_match(self):
        f = caf.FailedTest("com.example.BarTest", "testD", "Unit Tests")
        self.assertTrue(caf.is_allowed(f, {"com.example.BarTest#testD"}))
        self.assertFalse(caf.is_allowed(f, {"com.example.BarTest#testC"}))

    def test_class_level_match_covers_all_methods(self):
        f = caf.FailedTest("com.example.BarTest", "testD", "Unit Tests")
        self.assertTrue(caf.is_allowed(f, {"com.example.BarTest"}))

    def test_no_match(self):
        f = caf.FailedTest("com.example.BarTest", "testD", "Unit Tests")
        self.assertFalse(caf.is_allowed(f, set()))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


class ParseFailuresTests(unittest.TestCase):
    def test_collects_failures_and_errors_but_not_passes_or_skips(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            _write(directory, "TEST-pass.xml", PASSING_XML)
            _write(directory, "TEST-fail.xml", FAILING_XML)
            _write(directory, "TEST-error.xml", ERROR_XML)
            _write(directory, "TEST-skip.xml", SKIPPED_XML)
            failures = caf.parse_failures(directory, "Unit Tests")
            qualified = sorted(f.qualified for f in failures)
            self.assertEqual(
                qualified,
                ["com.example.BarTest#testD", "com.example.BazTest#testE"],
            )

    def test_ignores_non_test_xml(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            _write(directory, "other.xml", FAILING_XML)
            self.assertEqual(caf.parse_failures(directory, "Unit Tests"), [])


# ---------------------------------------------------------------------------
# main() exit codes
# ---------------------------------------------------------------------------


class MainExitCodeTests(unittest.TestCase):
    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = caf.main(argv)
        return code, buf.getvalue()

    def test_passes_when_no_failures(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            _write(directory, "TEST-pass.xml", PASSING_XML)
            code, out = self._run([str(directory), "--suite-label", "Unit Tests"])
            self.assertEqual(code, 0)
            self.assertIn("No test failures", out)

    def test_fails_on_unallowlisted_failure(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            _write(directory, "TEST-fail.xml", FAILING_XML)
            code, out = self._run([str(directory), "--suite-label", "Unit Tests"])
            self.assertEqual(code, 1)
            self.assertIn("Blocking test failures", out)
            self.assertIn("com.example.BarTest#testD", out)

    def test_passes_when_failure_is_allowlisted_by_method(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            _write(directory, "TEST-fail.xml", FAILING_XML)
            allow = _write(directory, "allow.txt", "com.example.BarTest#testD\n")
            code, out = self._run(
                [
                    "--allowlist",
                    str(allow),
                    str(directory),
                    "--suite-label",
                    "Unit Tests",
                ]
            )
            self.assertEqual(code, 0)
            self.assertIn("allowlisted", out)

    def test_passes_when_failure_is_allowlisted_by_class(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            _write(directory, "TEST-fail.xml", FAILING_XML)
            allow = _write(directory, "allow.txt", "com.example.BarTest\n")
            code, _ = self._run(
                [
                    "--allowlist",
                    str(allow),
                    str(directory),
                    "--suite-label",
                    "Unit Tests",
                ]
            )
            self.assertEqual(code, 0)

    def test_fails_when_one_of_several_failures_is_not_allowlisted(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            _write(directory, "TEST-fail.xml", FAILING_XML)
            _write(directory, "TEST-error.xml", ERROR_XML)
            allow = _write(directory, "allow.txt", "com.example.BarTest#testD\n")
            code, out = self._run(
                [
                    "--allowlist",
                    str(allow),
                    str(directory),
                    "--suite-label",
                    "Unit Tests",
                ]
            )
            self.assertEqual(code, 1)
            self.assertIn("com.example.BazTest#testE", out)

    def test_missing_directory_is_skipped_not_fatal(self):
        code, out = self._run(["/no/such/dir", "--suite-label", "Unit Tests"])
        self.assertEqual(code, 0)

    def test_failure_outcome_without_test_failure_is_an_infra_failure(self):
        # Mirrors issue #307: a step aborts (date error) before producing any
        # failing JUnit XML. The directory has only passing results, yet the
        # step outcome is `failure`, so the build must still fail.
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            _write(directory, "TEST-pass.xml", PASSING_XML)
            code, out = self._run(
                [
                    str(directory),
                    "--suite-label",
                    "E2E Tests",
                    "--outcome",
                    "failure",
                ]
            )
            self.assertEqual(code, 1)
            self.assertIn("infrastructure", out)
            self.assertIn("E2E Tests", out)

    def test_failure_outcome_with_allowlisted_failure_still_passes(self):
        # The only failure is allowlisted, which fully explains the `failure`
        # outcome, so it is not an infra failure and the build passes.
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            _write(directory, "TEST-fail.xml", FAILING_XML)
            allow = _write(directory, "allow.txt", "com.example.BarTest#testD\n")
            code, _ = self._run(
                [
                    "--allowlist",
                    str(allow),
                    str(directory),
                    "--suite-label",
                    "E2E Tests",
                    "--outcome",
                    "failure",
                ]
            )
            self.assertEqual(code, 0)

    def test_success_outcome_is_not_an_infra_failure(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            _write(directory, "TEST-pass.xml", PASSING_XML)
            code, _ = self._run(
                [
                    str(directory),
                    "--suite-label",
                    "Unit Tests",
                    "--outcome",
                    "success",
                ]
            )
            self.assertEqual(code, 0)


class ParseArgsTests(unittest.TestCase):
    def test_requires_suite_label(self):
        with self.assertRaises(SystemExit) as cm:
            caf.parse_args(["somedir"])
        self.assertEqual(cm.exception.code, 2)

    def test_allowlist_requires_value(self):
        with self.assertRaises(SystemExit) as cm:
            caf.parse_args(["--allowlist"])
        self.assertEqual(cm.exception.code, 2)

    def test_parses_allowlist_and_specs(self):
        allowlist, specs = caf.parse_args(
            [
                "--allowlist",
                "a.txt",
                "dir1",
                "--suite-label",
                "Unit Tests",
                "dir2",
                "--suite-label",
                "E2E Tests",
            ]
        )
        self.assertEqual(allowlist, Path("a.txt"))
        self.assertEqual(
            specs,
            [
                caf.SuiteSpec(Path("dir1"), "Unit Tests", ""),
                caf.SuiteSpec(Path("dir2"), "E2E Tests", ""),
            ],
        )

    def test_parses_outcome(self):
        _, specs = caf.parse_args(
            [
                "dir1",
                "--suite-label",
                "Unit Tests",
                "--outcome",
                "success",
                "dir2",
                "--suite-label",
                "E2E Tests",
                "--outcome",
                "failure",
            ]
        )
        self.assertEqual(
            specs,
            [
                caf.SuiteSpec(Path("dir1"), "Unit Tests", "success"),
                caf.SuiteSpec(Path("dir2"), "E2E Tests", "failure"),
            ],
        )

    def test_outcome_requires_value(self):
        with self.assertRaises(SystemExit) as cm:
            caf.parse_args(["dir1", "--suite-label", "Unit Tests", "--outcome"])
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
