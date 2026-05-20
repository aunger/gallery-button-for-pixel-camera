#!/usr/bin/env python3
"""Unit tests for summarize_test_results.py."""

import io
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import summarize_test_results as srt  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_xml(directory: Path, filename: str, content: str) -> Path:
    """Write an XML file to *directory* and return its path."""
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

MIXED_XML = """\
    <?xml version="1.0" encoding="UTF-8"?>
    <testsuites>
      <testsuite name="com.example.AlphaTest" tests="1" failures="0">
        <testcase name="alpha1"/>
      </testsuite>
      <testsuite name="com.example.BetaTest" tests="1" failures="1">
        <testcase name="beta1">
          <error message="NullPointerException">NPE</error>
        </testcase>
      </testsuite>
    </testsuites>
"""

SKIPPED_XML = """\
    <?xml version="1.0" encoding="UTF-8"?>
    <testsuite name="com.example.SkipTest" tests="3" failures="0" skipped="1">
      <testcase name="testPass" classname="com.example.SkipTest"/>
      <testcase name="testSkip" classname="com.example.SkipTest">
        <skipped/>
      </testcase>
      <testcase name="testFail" classname="com.example.SkipTest">
        <failure message="AssertionError">Expected true but was false</failure>
      </testcase>
    </testsuite>
"""


# ---------------------------------------------------------------------------
# parse_directory tests
# ---------------------------------------------------------------------------

class TestParseDirectory(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_empty_directory_returns_empty_dict(self):
        result = srt.parse_directory(self.tmpdir)
        self.assertEqual(result, {})

    def test_passing_xml_all_cases_pass(self):
        _write_xml(self.tmpdir, "TEST-com.example.FooTest.xml", PASSING_XML)
        classes = srt.parse_directory(self.tmpdir)
        self.assertIn("com.example.FooTest", classes)
        cls = classes["com.example.FooTest"]
        self.assertFalse(cls.any_failed)
        self.assertEqual(len(cls.cases), 2)
        self.assertTrue(all(tc.passed for tc in cls.cases))

    def test_failing_xml_class_fails(self):
        _write_xml(self.tmpdir, "TEST-com.example.BarTest.xml", FAILING_XML)
        classes = srt.parse_directory(self.tmpdir)
        self.assertIn("com.example.BarTest", classes)
        cls = classes["com.example.BarTest"]
        self.assertTrue(cls.any_failed)
        passed_cases = [tc for tc in cls.cases if tc.passed]
        failed_cases = [tc for tc in cls.cases if not tc.passed]
        self.assertEqual(len(passed_cases), 1)
        self.assertEqual(len(failed_cases), 1)
        self.assertEqual(failed_cases[0].name, "testD")

    def test_testsuites_wrapper_parsed(self):
        _write_xml(self.tmpdir, "TEST-mixed.xml", MIXED_XML)
        classes = srt.parse_directory(self.tmpdir)
        self.assertIn("com.example.AlphaTest", classes)
        self.assertIn("com.example.BetaTest", classes)
        self.assertFalse(classes["com.example.AlphaTest"].any_failed)
        self.assertTrue(classes["com.example.BetaTest"].any_failed)

    def test_non_xml_files_ignored(self):
        (self.tmpdir / "not-a-test.txt").write_text("ignore me")
        (self.tmpdir / "TEST-foo.xml").write_text(
            textwrap.dedent(PASSING_XML), encoding="utf-8"
        )
        classes = srt.parse_directory(self.tmpdir)
        self.assertEqual(len(classes), 1)

    def test_malformed_xml_skipped_with_warning(self):
        (self.tmpdir / "TEST-bad.xml").write_text("<not valid xml <<<")
        result = srt.parse_directory(self.tmpdir)
        self.assertEqual(result, {})

    def test_error_element_counts_as_failure(self):
        _write_xml(self.tmpdir, "TEST-mixed.xml", MIXED_XML)
        classes = srt.parse_directory(self.tmpdir)
        # BetaTest has an <error> child — should count as failed
        self.assertTrue(classes["com.example.BetaTest"].any_failed)

    def test_skipped_element_not_counted_as_pass(self):
        _write_xml(self.tmpdir, "TEST-skip.xml", SKIPPED_XML)
        classes = srt.parse_directory(self.tmpdir)
        cls = classes["com.example.SkipTest"]
        skipped_cases = [tc for tc in cls.cases if tc.skipped]
        passed_cases = [tc for tc in cls.cases if tc.passed]
        failed_cases = [tc for tc in cls.cases if not tc.passed and not tc.skipped]
        self.assertEqual(len(skipped_cases), 1)
        self.assertEqual(skipped_cases[0].name, "testSkip")
        self.assertEqual(len(passed_cases), 1)
        self.assertEqual(passed_cases[0].name, "testPass")
        self.assertEqual(len(failed_cases), 1)
        self.assertEqual(failed_cases[0].name, "testFail")

    def test_skipped_cases_do_not_inflate_pass_count(self):
        _write_xml(self.tmpdir, "TEST-skip.xml", SKIPPED_XML)
        classes = srt.parse_directory(self.tmpdir)
        cls = classes["com.example.SkipTest"]
        # A skipped test should have skipped=True and passed=False
        skipped = next(tc for tc in cls.cases if tc.name == "testSkip")
        self.assertTrue(skipped.skipped)
        self.assertFalse(skipped.passed)


# ---------------------------------------------------------------------------
# TestClass.any_failed tests
# ---------------------------------------------------------------------------

class TestTestClass(unittest.TestCase):

    def test_all_pass_not_any_failed(self):
        cls = srt.TestClass(name="Foo", cases=[
            srt.TestCase("a", True),
            srt.TestCase("b", True),
        ])
        self.assertFalse(cls.any_failed)

    def test_one_failure_is_any_failed(self):
        cls = srt.TestClass(name="Foo", cases=[
            srt.TestCase("a", True),
            srt.TestCase("b", False),
        ])
        self.assertTrue(cls.any_failed)

    def test_empty_cases_not_any_failed(self):
        cls = srt.TestClass(name="Empty")
        self.assertFalse(cls.any_failed)

    def test_all_skipped_not_any_failed(self):
        cls = srt.TestClass(name="Skipped", cases=[
            srt.TestCase("s", False, skipped=True),
        ])
        self.assertFalse(cls.any_failed)


# ---------------------------------------------------------------------------
# render_suite tests
# ---------------------------------------------------------------------------

class TestRenderSuite(unittest.TestCase):

    def test_empty_suite_shows_no_results_note(self):
        lines = srt.render_suite("Unit Tests", {})
        combined = "\n".join(lines)
        self.assertIn("No test results found", combined)
        self.assertNotIn("| Status |", combined)

    def test_passing_class_shows_green(self):
        classes = {
            "com.example.Foo": srt.TestClass(
                name="com.example.Foo",
                cases=[srt.TestCase("testA", True)],
            )
        }
        lines = srt.render_suite("Unit Tests", classes)
        combined = "\n".join(lines)
        self.assertIn("✅ PASS", combined)
        self.assertNotIn("❌ FAIL", combined)

    def test_failing_class_shows_red(self):
        classes = {
            "com.example.Foo": srt.TestClass(
                name="com.example.Foo",
                cases=[srt.TestCase("testA", False)],
            )
        }
        lines = srt.render_suite("Unit Tests", classes)
        combined = "\n".join(lines)
        self.assertIn("❌ FAIL", combined)

    def test_totals_line_correct(self):
        classes = {
            "com.example.Foo": srt.TestClass(
                name="com.example.Foo",
                cases=[
                    srt.TestCase("pass1", True),
                    srt.TestCase("pass2", True),
                    srt.TestCase("fail1", False),
                ],
            )
        }
        lines = srt.render_suite("Unit Tests", classes)
        combined = "\n".join(lines)
        self.assertIn("2 passed, 1 failed", combined)
        self.assertIn("(3 tests)", combined)

    def test_mixed_class_icon_is_fail_when_any_case_fails(self):
        classes = {
            "com.example.Mixed": srt.TestClass(
                name="com.example.Mixed",
                cases=[
                    srt.TestCase("ok", True),
                    srt.TestCase("bad", False),
                ],
            )
        }
        lines = srt.render_suite("Suite", classes)
        combined = "\n".join(lines)
        # The class row must contain FAIL
        class_row = next(
            line for line in lines
            if "com.example.Mixed" in line and "**" in line
        )
        self.assertIn("❌ FAIL", class_row)

    def test_skipped_case_shows_skip_icon(self):
        classes = {
            "com.example.Foo": srt.TestClass(
                name="com.example.Foo",
                cases=[
                    srt.TestCase("pass1", True, skipped=False),
                    srt.TestCase("skip1", False, skipped=True),
                ],
            )
        }
        lines = srt.render_suite("Unit Tests", classes)
        combined = "\n".join(lines)
        self.assertIn("⏭ SKIP", combined)
        self.assertNotIn("❌ FAIL", combined)

    def test_skipped_case_counted_in_totals_not_as_pass(self):
        classes = {
            "com.example.Foo": srt.TestClass(
                name="com.example.Foo",
                cases=[
                    srt.TestCase("pass1", True, skipped=False),
                    srt.TestCase("skip1", False, skipped=True),
                    srt.TestCase("fail1", False, skipped=False),
                ],
            )
        }
        lines = srt.render_suite("Unit Tests", classes)
        combined = "\n".join(lines)
        self.assertIn("1 passed, 1 failed, 1 skipped", combined)
        self.assertIn("(3 tests)", combined)

    def test_all_skipped_class_icon_is_pass(self):
        """A class with only skipped tests and no failures shows ✅ PASS."""
        classes = {
            "com.example.AllSkip": srt.TestClass(
                name="com.example.AllSkip",
                cases=[srt.TestCase("s1", False, skipped=True)],
            )
        }
        lines = srt.render_suite("Suite", classes)
        class_row = next(
            line for line in lines
            if "com.example.AllSkip" in line and "**" in line
        )
        self.assertIn("✅ PASS", class_row)


# ---------------------------------------------------------------------------
# build_markdown tests
# ---------------------------------------------------------------------------

class TestBuildMarkdown(unittest.TestCase):

    def test_starts_with_h2(self):
        md = srt.build_markdown([])
        self.assertTrue(md.startswith("## Test Results"))

    def test_multiple_suites_appear_in_order(self):
        suite_data = [
            ("Unit Tests", {}),
            ("Instrumented Tests", {}),
        ]
        md = srt.build_markdown(suite_data)
        unit_pos = md.index("Unit Tests")
        instrumented_pos = md.index("Instrumented Tests")
        self.assertLess(unit_pos, instrumented_pos)


# ---------------------------------------------------------------------------
# parse_args tests
# ---------------------------------------------------------------------------

class TestParseArgs(unittest.TestCase):

    def test_single_pair(self):
        pairs = srt.parse_args(["path/to/unit", "--suite-label", "Unit Tests"])
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0][0], Path("path/to/unit"))
        self.assertEqual(pairs[0][1], "Unit Tests")

    def test_two_pairs(self):
        pairs = srt.parse_args([
            "dir1", "--suite-label", "Label1",
            "dir2", "--suite-label", "Label2",
        ])
        self.assertEqual(len(pairs), 2)
        self.assertEqual(pairs[1][1], "Label2")

    def test_missing_suite_label_flag_raises(self):
        with self.assertRaises(SystemExit):
            srt.parse_args(["dir1", "--wrong-flag", "Label"])

    def test_no_args_raises(self):
        with self.assertRaises(SystemExit):
            srt.parse_args([])

    def test_incomplete_pair_raises(self):
        # directory without --suite-label following
        with self.assertRaises(SystemExit):
            srt.parse_args(["dir1"])


# ---------------------------------------------------------------------------
# main() integration tests
# ---------------------------------------------------------------------------

class TestMain(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_exit_0_on_passing_results(self):
        _write_xml(self.tmpdir, "TEST-foo.xml", PASSING_XML)
        result = srt.main([str(self.tmpdir), "--suite-label", "Unit"])
        self.assertEqual(result, 0)

    def test_exit_0_on_failing_results(self):
        """Script always exits 0 — failures are surfaced by earlier steps."""
        _write_xml(self.tmpdir, "TEST-foo.xml", FAILING_XML)
        result = srt.main([str(self.tmpdir), "--suite-label", "Unit"])
        self.assertEqual(result, 0)

    def test_exit_0_on_missing_directory(self):
        missing = self.tmpdir / "nonexistent"
        result = srt.main([str(missing), "--suite-label", "Unit"])
        self.assertEqual(result, 0)

    def test_writes_to_github_step_summary(self):
        _write_xml(self.tmpdir, "TEST-foo.xml", PASSING_XML)
        summary_file = self.tmpdir / "summary.md"
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary_file)}):
            srt.main([str(self.tmpdir), "--suite-label", "Unit Tests"])
        content = summary_file.read_text(encoding="utf-8")
        self.assertIn("## Test Results", content)
        self.assertIn("Unit Tests", content)

    def test_falls_back_to_stdout_when_no_env_var(self):
        _write_xml(self.tmpdir, "TEST-foo.xml", PASSING_XML)
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"}
        with patch.dict(os.environ, env, clear=True):
            captured = io.StringIO()
            with patch("sys.stdout", captured):
                srt.main([str(self.tmpdir), "--suite-label", "Unit Tests"])
        output = captured.getvalue()
        self.assertIn("## Test Results", output)

    def test_appends_to_existing_summary_file(self):
        _write_xml(self.tmpdir, "TEST-foo.xml", PASSING_XML)
        summary_file = self.tmpdir / "summary.md"
        summary_file.write_text("existing content\n", encoding="utf-8")
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary_file)}):
            srt.main([str(self.tmpdir), "--suite-label", "Unit Tests"])
        content = summary_file.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("existing content"))
        self.assertIn("## Test Results", content)


if __name__ == "__main__":
    unittest.main()
