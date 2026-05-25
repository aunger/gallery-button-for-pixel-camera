#!/usr/bin/env python3
"""Unit tests for file_test_failure_issues.py."""

import os
import sys
import tempfile
import textwrap
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import file_test_failure_issues as ftfi  # noqa: E402


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
        <failure message="AssertionError: Expected true">Expected true but was false&#10;at com.example.BarTest.testD(BarTest.java:42)</failure>
      </testcase>
    </testsuite>
"""

ERROR_XML = """\
    <?xml version="1.0" encoding="UTF-8"?>
    <testsuite name="com.example.BazTest" tests="1" failures="0" errors="1">
      <testcase name="testE" classname="com.example.BazTest">
        <error message="NullPointerException">java.lang.NullPointerException&#10;at com.example.BazTest.testE(BazTest.java:10)</error>
      </testcase>
    </testsuite>
"""

SKIPPED_XML = """\
    <?xml version="1.0" encoding="UTF-8"?>
    <testsuite name="com.example.SkipTest" tests="2" failures="0" skipped="1">
      <testcase name="testPass" classname="com.example.SkipTest"/>
      <testcase name="testSkip" classname="com.example.SkipTest">
        <skipped/>
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
          <failure message="AssertionError">beta failed</failure>
        </testcase>
      </testsuite>
    </testsuites>
"""

_FIXED_TS = datetime(2026, 5, 25, 6, 29, 0, tzinfo=timezone.utc)
_FIXED_SHA = "a1b2c3d4e5f6789"


# ---------------------------------------------------------------------------
# parse_failures tests
# ---------------------------------------------------------------------------

class TestParseFailures(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_empty_directory_returns_empty_list(self):
        result = ftfi.parse_failures(self.tmpdir, "Unit Tests", "unit-test-results")
        self.assertEqual(result, [])

    def test_passing_xml_returns_no_failures(self):
        _write_xml(self.tmpdir, "TEST-FooTest.xml", PASSING_XML)
        failures = ftfi.parse_failures(self.tmpdir, "Unit Tests", "unit-test-results")
        self.assertEqual(failures, [])

    def test_failing_xml_returns_one_failure(self):
        _write_xml(self.tmpdir, "TEST-BarTest.xml", FAILING_XML)
        failures = ftfi.parse_failures(self.tmpdir, "Unit Tests", "unit-test-results")
        self.assertEqual(len(failures), 1)
        f = failures[0]
        self.assertEqual(f.class_name, "com.example.BarTest")
        self.assertEqual(f.method_name, "testD")
        self.assertEqual(f.suite_label, "Unit Tests")
        self.assertEqual(f.artifact_name, "unit-test-results")

    def test_error_element_counts_as_failure(self):
        _write_xml(self.tmpdir, "TEST-BazTest.xml", ERROR_XML)
        failures = ftfi.parse_failures(self.tmpdir, "Unit Tests", "unit-test-results")
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].method_name, "testE")

    def test_skipped_not_counted_as_failure(self):
        _write_xml(self.tmpdir, "TEST-SkipTest.xml", SKIPPED_XML)
        failures = ftfi.parse_failures(self.tmpdir, "Unit Tests", "unit-test-results")
        self.assertEqual(failures, [])

    def test_testsuites_wrapper_parsed(self):
        _write_xml(self.tmpdir, "TEST-mixed.xml", MIXED_XML)
        failures = ftfi.parse_failures(self.tmpdir, "Unit Tests", "unit-test-results")
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].class_name, "com.example.BetaTest")
        self.assertEqual(failures[0].method_name, "beta1")

    def test_malformed_xml_skipped_with_warning(self):
        (self.tmpdir / "TEST-bad.xml").write_text("<not valid xml <<<", encoding="utf-8")
        result = ftfi.parse_failures(self.tmpdir, "Unit Tests", "unit-test-results")
        self.assertEqual(result, [])

    def test_failure_message_and_stack_trace_captured(self):
        _write_xml(self.tmpdir, "TEST-BarTest.xml", FAILING_XML)
        failures = ftfi.parse_failures(self.tmpdir, "Unit Tests", "unit-test-results")
        self.assertEqual(len(failures), 1)
        f = failures[0]
        self.assertEqual(f.failure_message, "AssertionError: Expected true")
        self.assertIn("Expected true but was false", f.stack_trace)
        self.assertIn("BarTest.java:42", f.stack_trace)


# ---------------------------------------------------------------------------
# make_issue_title tests
# ---------------------------------------------------------------------------

class TestMakeIssueTitle(unittest.TestCase):

    def test_format_matches_spec(self):
        title = ftfi.make_issue_title(
            "PrefsManagerTest", "readDefaultReturnsNull",
            _FIXED_TS, "a1b2c3d",
        )
        self.assertEqual(
            title,
            "[Test Failure] PrefsManagerTest.readDefaultReturnsNull @ 260525-0629-a1b2c3d",
        )

    def test_sha_truncated_to_7_chars(self):
        title = ftfi.make_issue_title(
            "com.example.Foo", "testBar",
            _FIXED_TS, _FIXED_SHA,
        )
        # Only first 7 chars of _FIXED_SHA = "a1b2c3d"
        self.assertIn("a1b2c3d", title)
        self.assertNotIn(_FIXED_SHA[7:], title)

    def test_empty_sha_uses_unknown(self):
        title = ftfi.make_issue_title("Foo", "bar", _FIXED_TS, "")
        self.assertIn("unknown", title)

    def test_timestamp_format(self):
        # 2026-05-25 06:29 UTC → yyMMdd-hhmm = 260525-0629
        title = ftfi.make_issue_title("Foo", "bar", _FIXED_TS, "abc1234")
        self.assertIn("260525-0629", title)

    def test_includes_class_and_method(self):
        title = ftfi.make_issue_title("MyClass", "myMethod", _FIXED_TS, "abc1234")
        self.assertIn("MyClass.myMethod", title)


# ---------------------------------------------------------------------------
# make_issue_body tests
# ---------------------------------------------------------------------------

class TestMakeIssueBody(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)
        self.failure = ftfi.FailedTest(
            class_name="com.example.BarTest",
            method_name="testD",
            failure_message="AssertionError: Expected true",
            stack_trace="Expected true but was false\nat com.example.BarTest.testD(BarTest.java:42)",
            suite_label="Unit Tests",
            artifact_name="unit-test-results",
        )

    def tearDown(self):
        self._tmpdir.cleanup()

    def _make_body(self, **kwargs):
        defaults = dict(
            failure=self.failure,
            directory=self.tmpdir,
            timestamp=_FIXED_TS,
            sha=_FIXED_SHA,
            github_server_url="https://github.com",
            github_repository="aunger/gallery-button-for-pixel-camera",
            github_run_id="12345",
            workflow_run_branch="main",
        )
        defaults.update(kwargs)
        return ftfi.make_issue_body(**defaults)

    def test_body_contains_suite_label(self):
        body = self._make_body()
        self.assertIn("Unit Tests", body)

    def test_body_contains_class_name(self):
        body = self._make_body()
        self.assertIn("com.example.BarTest", body)

    def test_body_contains_method_name(self):
        body = self._make_body()
        self.assertIn("testD", body)

    def test_body_contains_failure_message(self):
        body = self._make_body()
        self.assertIn("AssertionError: Expected true", body)

    def test_body_contains_stack_trace(self):
        body = self._make_body()
        self.assertIn("BarTest.java:42", body)

    def test_body_contains_ci_run_link(self):
        body = self._make_body()
        self.assertIn(
            "https://github.com/aunger/gallery-button-for-pixel-camera/actions/runs/12345",
            body,
        )

    def test_body_contains_artifact_link(self):
        body = self._make_body()
        self.assertIn("unit-test-results", body)

    def test_stack_trace_truncated_at_2000_chars(self):
        long_trace = "x" * 3000
        self.failure = ftfi.FailedTest(
            class_name="com.example.LongTest",
            method_name="testLong",
            failure_message="msg",
            stack_trace=long_trace,
            suite_label="Unit Tests",
            artifact_name="unit-test-results",
        )
        body = self._make_body(failure=self.failure)
        # The truncated excerpt should be at most 2000 chars plus the "(truncated)" suffix
        self.assertIn("... (truncated)", body)
        # The full 3000-char trace should not appear verbatim
        self.assertNotIn("x" * 2001, body)

    def test_stack_trace_not_truncated_when_short(self):
        short_trace = "short trace"
        self.failure = ftfi.FailedTest(
            class_name="com.example.ShortTest",
            method_name="testShort",
            failure_message="msg",
            stack_trace=short_trace,
            suite_label="Unit Tests",
            artifact_name="unit-test-results",
        )
        body = self._make_body(failure=self.failure)
        self.assertIn(short_trace, body)
        self.assertNotIn("... (truncated)", body)

    def test_branch_shown_in_body(self):
        body = self._make_body(workflow_run_branch="feature/foo")
        self.assertIn("feature/foo", body)

    def test_no_run_id_shows_unavailable(self):
        body = self._make_body(github_run_id="")
        self.assertIn("not available", body)

    def test_ocr_text_inlined_when_present(self):
        """OCR companion file matching class+method prefix is inlined."""
        ocr_file = self.tmpdir / "BarTest_testD_screenshot.ocr.txt"
        ocr_file.write_text("OCR content here", encoding="utf-8")
        body = self._make_body()
        self.assertIn("OCR text from screenshot", body)
        self.assertIn("OCR content here", body)

    def test_ocr_section_absent_when_no_file(self):
        """No OCR section when no companion .ocr.txt file exists."""
        body = self._make_body()
        self.assertNotIn("OCR text from screenshot", body)


# ---------------------------------------------------------------------------
# _find_ocr_text tests
# ---------------------------------------------------------------------------

class TestFindOcrText(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_returns_none_when_no_ocr_files(self):
        result = ftfi._find_ocr_text(self.tmpdir, "com.example.Foo", "testBar")
        self.assertIsNone(result)

    def test_matches_short_class_and_method_prefix(self):
        (self.tmpdir / "Foo_testBar_001.ocr.txt").write_text("screen text", encoding="utf-8")
        result = ftfi._find_ocr_text(self.tmpdir, "com.example.Foo", "testBar")
        self.assertEqual(result, "screen text")

    def test_returns_none_when_no_prefix_match(self):
        (self.tmpdir / "Other_testBaz_001.ocr.txt").write_text("irrelevant", encoding="utf-8")
        result = ftfi._find_ocr_text(self.tmpdir, "com.example.Foo", "testBar")
        self.assertIsNone(result)

    def test_strips_whitespace_from_ocr_text(self):
        (self.tmpdir / "Foo_testBar_001.ocr.txt").write_text("  trimmed  \n", encoding="utf-8")
        result = ftfi._find_ocr_text(self.tmpdir, "com.example.Foo", "testBar")
        self.assertEqual(result, "trimmed")


# ---------------------------------------------------------------------------
# Duplicate suppression tests
# ---------------------------------------------------------------------------

class TestFindExistingIssue(unittest.TestCase):

    @patch("file_test_failure_issues.requests")
    def test_returns_issue_number_when_found(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": [{"number": 42}]}
        mock_requests.get.return_value = mock_resp
        result = ftfi.find_existing_issue("token", "owner/repo", "FooTest", "testBar")
        self.assertEqual(result, 42)

    @patch("file_test_failure_issues.requests")
    def test_returns_none_when_not_found(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": []}
        mock_requests.get.return_value = mock_resp
        result = ftfi.find_existing_issue("token", "owner/repo", "FooTest", "testBar")
        self.assertIsNone(result)

    @patch("file_test_failure_issues.requests")
    def test_returns_none_on_api_error(self, mock_requests):
        mock_requests.get.side_effect = Exception("network error")
        result = ftfi.find_existing_issue("token", "owner/repo", "FooTest", "testBar")
        self.assertIsNone(result)

    def test_returns_none_when_requests_unavailable(self):
        with patch.object(ftfi, "requests", None):
            result = ftfi.find_existing_issue("token", "owner/repo", "FooTest", "testBar")
        self.assertIsNone(result)


class TestCreateIssue(unittest.TestCase):

    @patch("file_test_failure_issues.requests")
    def test_returns_issue_number_on_success(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"number": 99}
        mock_requests.post.return_value = mock_resp
        result = ftfi.create_issue("token", "owner/repo", "Title", "Body")
        self.assertEqual(result, 99)

    @patch("file_test_failure_issues.requests")
    def test_returns_none_on_api_error(self, mock_requests):
        mock_requests.post.side_effect = Exception("server error")
        result = ftfi.create_issue("token", "owner/repo", "Title", "Body")
        self.assertIsNone(result)

    def test_returns_none_when_requests_unavailable(self):
        with patch.object(ftfi, "requests", None):
            result = ftfi.create_issue("token", "owner/repo", "Title", "Body")
        self.assertIsNone(result)

    @patch("file_test_failure_issues.requests")
    def test_sends_correct_labels(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"number": 1}
        mock_requests.post.return_value = mock_resp
        ftfi.create_issue("token", "owner/repo", "Title", "Body")
        call_kwargs = mock_requests.post.call_args
        payload = call_kwargs[1]["json"]
        self.assertIn("test-failure", payload["labels"])
        self.assertIn("ci", payload["labels"])
        self.assertIn("for ai to do", payload["labels"])


class TestAddIssueComment(unittest.TestCase):

    @patch("file_test_failure_issues.requests")
    def test_returns_true_on_success(self, mock_requests):
        mock_resp = MagicMock()
        mock_requests.post.return_value = mock_resp
        result = ftfi.add_issue_comment("token", "owner/repo", 42, "Comment body")
        self.assertTrue(result)

    @patch("file_test_failure_issues.requests")
    def test_returns_false_on_api_error(self, mock_requests):
        mock_requests.post.side_effect = Exception("network error")
        result = ftfi.add_issue_comment("token", "owner/repo", 42, "Comment body")
        self.assertFalse(result)

    def test_returns_false_when_requests_unavailable(self):
        with patch.object(ftfi, "requests", None):
            result = ftfi.add_issue_comment("token", "owner/repo", 42, "Comment body")
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# process_failure tests (duplicate suppression)
# ---------------------------------------------------------------------------

class TestProcessFailure(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)
        self.failure = ftfi.FailedTest(
            class_name="com.example.FooTest",
            method_name="testBar",
            failure_message="msg",
            stack_trace="trace",
            suite_label="Unit Tests",
            artifact_name="unit-test-results",
        )

    def tearDown(self):
        self._tmpdir.cleanup()

    @patch("file_test_failure_issues.add_issue_comment")
    @patch("file_test_failure_issues.create_issue")
    @patch("file_test_failure_issues.find_existing_issue")
    def test_comments_on_existing_issue_instead_of_creating(
        self, mock_find, mock_create, mock_comment
    ):
        mock_find.return_value = 55  # existing issue found
        ftfi.process_failure(
            failure=self.failure,
            directory=self.tmpdir,
            token="tok",
            repository="owner/repo",
            timestamp=_FIXED_TS,
            sha=_FIXED_SHA,
            github_server_url="https://github.com",
            github_run_id="1",
            workflow_run_branch="main",
        )
        mock_comment.assert_called_once()
        mock_create.assert_not_called()
        # Comment is posted to the existing issue number
        self.assertEqual(mock_comment.call_args[0][2], 55)

    @patch("file_test_failure_issues.add_issue_comment")
    @patch("file_test_failure_issues.create_issue")
    @patch("file_test_failure_issues.find_existing_issue")
    def test_creates_issue_when_no_duplicate(
        self, mock_find, mock_create, mock_comment
    ):
        mock_find.return_value = None  # no existing issue
        mock_create.return_value = 77
        ftfi.process_failure(
            failure=self.failure,
            directory=self.tmpdir,
            token="tok",
            repository="owner/repo",
            timestamp=_FIXED_TS,
            sha=_FIXED_SHA,
            github_server_url="https://github.com",
            github_run_id="1",
            workflow_run_branch="main",
        )
        mock_create.assert_called_once()
        mock_comment.assert_not_called()


# ---------------------------------------------------------------------------
# main() exit-0 tests
# ---------------------------------------------------------------------------

class TestMain(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)
        self._env_patch = patch.dict(os.environ, {
            "GITHUB_TOKEN": "test-token",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_RUN_ID": "42",
            "WORKFLOW_RUN_SHA": "abc1234",
            "WORKFLOW_RUN_BRANCH": "main",
        })
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def test_exit_0_with_no_failures(self):
        _write_xml(self.tmpdir, "TEST-foo.xml", PASSING_XML)
        result = ftfi.main([str(self.tmpdir), "--suite-label", "Unit Tests"])
        self.assertEqual(result, 0)

    def test_exit_0_when_directory_missing(self):
        missing = self.tmpdir / "nonexistent"
        result = ftfi.main([str(missing), "--suite-label", "Unit Tests"])
        self.assertEqual(result, 0)

    def test_exit_0_when_token_missing(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
            result = ftfi.main([str(self.tmpdir), "--suite-label", "Unit Tests"])
        self.assertEqual(result, 0)

    def test_exit_0_when_repository_missing(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": ""}):
            result = ftfi.main([str(self.tmpdir), "--suite-label", "Unit Tests"])
        self.assertEqual(result, 0)

    @patch("file_test_failure_issues.process_failure")
    def test_exit_0_even_when_api_raises(self, mock_process):
        """main() must return 0 even if process_failure raises an exception."""
        mock_process.side_effect = Exception("API is down")
        _write_xml(self.tmpdir, "TEST-bar.xml", FAILING_XML)
        result = ftfi.main([str(self.tmpdir), "--suite-label", "Unit Tests"])
        self.assertEqual(result, 0)

    @patch("file_test_failure_issues.process_failure")
    def test_processes_each_failure(self, mock_process):
        """One process_failure call per failed test case."""
        _write_xml(self.tmpdir, "TEST-bar.xml", FAILING_XML)
        ftfi.main([str(self.tmpdir), "--suite-label", "Unit Tests"])
        self.assertEqual(mock_process.call_count, 1)

    @patch("file_test_failure_issues.process_failure")
    def test_multiple_suites_processed(self, mock_process):
        """Failures from all suite directories are processed."""
        dir2 = Path(self._tmpdir.name) / "e2e"
        dir2.mkdir()
        _write_xml(self.tmpdir, "TEST-bar.xml", FAILING_XML)
        _write_xml(dir2, "TEST-baz.xml", ERROR_XML)
        result = ftfi.main([
            str(self.tmpdir), "--suite-label", "Unit Tests",
            str(dir2), "--suite-label", "Instrumented Tests",
        ])
        self.assertEqual(result, 0)
        self.assertEqual(mock_process.call_count, 2)


# ---------------------------------------------------------------------------
# parse_args tests
# ---------------------------------------------------------------------------

class TestParseArgs(unittest.TestCase):

    def test_single_pair(self):
        pairs = ftfi.parse_args(["path/to/unit", "--suite-label", "Unit Tests"])
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0][0], Path("path/to/unit"))
        self.assertEqual(pairs[0][1], "Unit Tests")

    def test_two_pairs(self):
        pairs = ftfi.parse_args([
            "dir1", "--suite-label", "Label1",
            "dir2", "--suite-label", "Label2",
        ])
        self.assertEqual(len(pairs), 2)
        self.assertEqual(pairs[1][1], "Label2")

    def test_missing_suite_label_flag_raises(self):
        with self.assertRaises(SystemExit):
            ftfi.parse_args(["dir1", "--wrong-flag", "Label"])

    def test_no_args_raises(self):
        with self.assertRaises(SystemExit):
            ftfi.parse_args([])

    def test_incomplete_pair_raises(self):
        with self.assertRaises(SystemExit):
            ftfi.parse_args(["dir1"])


# ---------------------------------------------------------------------------
# _artifact_name_for_label tests
# ---------------------------------------------------------------------------

class TestArtifactNameForLabel(unittest.TestCase):

    def test_unit_label_maps_to_unit_test_results(self):
        self.assertEqual(ftfi._artifact_name_for_label("Unit Tests"), "unit-test-results")

    def test_instrumented_label_maps_to_e2e(self):
        self.assertEqual(
            ftfi._artifact_name_for_label("Instrumented Tests"), "e2e-test-results"
        )

    def test_e2e_label_maps_to_e2e(self):
        self.assertEqual(ftfi._artifact_name_for_label("E2E Tests"), "e2e-test-results")

    def test_unknown_label_slugified(self):
        self.assertEqual(
            ftfi._artifact_name_for_label("My Custom Suite"), "my-custom-suite"
        )


if __name__ == "__main__":
    unittest.main()
