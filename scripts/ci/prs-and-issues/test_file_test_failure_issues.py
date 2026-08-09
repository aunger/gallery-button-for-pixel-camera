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

    def test_finds_xml_files_in_subdirectory(self):
        """Recursive glob finds XML files nested under the given directory."""
        subdir = self.tmpdir / "app" / "build" / "test-results" / "testDebugUnitTest"
        subdir.mkdir(parents=True)
        _write_xml(subdir, "TEST-BarTest.xml", FAILING_XML)
        failures = ftfi.parse_failures(self.tmpdir, "Unit Tests", "unit-test-results")
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].class_name, "com.example.BarTest")

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
            "PrefsManagerTest",
            "readDefaultReturnsNull",
        )
        self.assertEqual(title, "[PrefsManagerTest] readDefaultReturnsNull")

    def test_strips_package_prefix(self):
        title = ftfi.make_issue_title(
            "com.gb4pc.e2e.PixelCameraOverlayE2ETest",
            "overlayAppearsWhenViewfinderOpens",
        )
        self.assertEqual(title, "[PixelCameraOverlayE2ETest] overlayAppearsWhenViewfinderOpens")

    def test_no_package_prefix_unaffected(self):
        title = ftfi.make_issue_title("MyClass", "myMethod")
        self.assertEqual(title, "[MyClass] myMethod")

    def test_does_not_contain_timestamp_or_sha(self):
        title = ftfi.make_issue_title("com.example.Foo", "testBar")
        # No date stamp or SHA fragment should appear
        self.assertNotIn("@", title)
        self.assertNotIn("260525", title)

    def test_does_not_contain_test_failure_prefix(self):
        title = ftfi.make_issue_title("Foo", "bar")
        self.assertNotIn("Test Failure", title)


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
            pr_url="",
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

    def test_pr_link_present_when_pr_url_provided(self):
        body = self._make_body(pr_url="https://github.com/owner/repo/pull/42")
        self.assertIn("https://github.com/owner/repo/pull/42", body)
        self.assertIn("[PR]", body)

    def test_pr_shows_unknown_when_pr_url_empty(self):
        body = self._make_body(pr_url="")
        self.assertNotIn("[PR]", body)
        self.assertIn("_unknown_", body)

    def test_failure_message_not_in_fenced_code_block(self):
        """Failure message must not be wrapped in a fenced code block (causes horizontal scroll)."""
        body = self._make_body()
        # Find the failure message section
        msg_idx = body.index("### Failure message")
        stack_idx = body.index("### Stack trace")
        failure_section = body[msg_idx:stack_idx]
        # The failure message text must be present
        self.assertIn("AssertionError: Expected true", failure_section)
        # But it must NOT appear inside a fenced code block
        self.assertNotIn("```", failure_section)

    def test_stack_trace_still_in_fenced_code_block(self):
        """Stack trace must remain in a fenced code block."""
        body = self._make_body()
        stack_idx = body.index("### Stack trace")
        links_idx = body.index("### Links")
        stack_section = body[stack_idx:links_idx]
        self.assertIn("```", stack_section)
        self.assertIn("BarTest.java:42", stack_section)


# ---------------------------------------------------------------------------
# make_summary_body tests
# ---------------------------------------------------------------------------


class TestMakeSummaryBody(unittest.TestCase):
    def setUp(self):
        self.failure = ftfi.FailedTest(
            class_name="com.example.BarTest",
            method_name="testD",
            failure_message="AssertionError: Expected true",
            stack_trace="Expected true but was false",
            suite_label="Unit Tests",
            artifact_name="unit-test-results",
        )

    def _make_summary(self, **kwargs):
        defaults = dict(
            failure=self.failure,
            github_server_url="https://github.com",
            github_repository="aunger/gallery-button-for-pixel-camera",
            workflow_run_branch="main",
        )
        defaults.update(kwargs)
        return ftfi.make_summary_body(**defaults)

    def test_has_automated_test_failure_heading(self):
        self.assertIn("# Automated test failure", self._make_summary())

    def test_contains_suite_label_as_job(self):
        self.assertIn("Unit Tests", self._make_summary())

    def test_contains_class_and_method(self):
        body = self._make_summary()
        self.assertIn("com.example.BarTest", body)
        self.assertIn("testD", body)

    def test_links_job_to_workflow_file_on_branch(self):
        body = self._make_summary(workflow_run_branch="feature/foo")
        self.assertIn(
            "https://github.com/aunger/gallery-button-for-pixel-camera/blob/feature/foo/.github/workflows/build.yml",
            body,
        )

    def test_workflow_link_falls_back_to_head_without_branch(self):
        body = self._make_summary(workflow_run_branch="")
        self.assertIn("/blob/HEAD/.github/workflows/build.yml", body)

    def test_mentions_comments_aggregation(self):
        self.assertIn(
            "Failed runs will be added as comments on this ticket over time.",
            self._make_summary(),
        )

    def test_excludes_per_run_details(self):
        """The summary body must not contain timestamp, stack trace, or run-specific info."""
        body = self._make_summary()
        self.assertNotIn("### Stack trace", body)
        self.assertNotIn("### Failure message", body)
        self.assertNotIn("Detected at", body)
        self.assertNotIn("### Failed on", body)


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

    def test_does_not_match_when_prefix_is_substring_of_another_test(self):
        """Foo_testBar should NOT match Foo_testBarBaz.ocr.txt (startswith, not contains)."""
        (self.tmpdir / "Foo_testBarBaz_001.ocr.txt").write_text("wrong match", encoding="utf-8")
        result = ftfi._find_ocr_text(self.tmpdir, "com.example.Foo", "testBar")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Duplicate suppression tests
# ---------------------------------------------------------------------------


class TestFindExistingIssue(unittest.TestCase):
    @patch("file_test_failure_issues.requests")
    def test_returns_issue_number_and_state_when_found_open(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": [{"number": 42, "state": "open"}]}
        mock_requests.get.return_value = mock_resp
        result = ftfi.find_existing_issue("token", "owner/repo", "FooTest", "testBar")
        self.assertEqual(result, (42, "open"))

    @patch("file_test_failure_issues.requests")
    def test_returns_issue_number_and_state_when_found_closed(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": [{"number": 7, "state": "closed"}]}
        mock_requests.get.return_value = mock_resp
        result = ftfi.find_existing_issue("token", "owner/repo", "FooTest", "testBar")
        self.assertEqual(result, (7, "closed"))

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

    @patch("file_test_failure_issues.requests")
    def test_searches_by_test_failure_label_and_bracketed_title(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": []}
        mock_requests.get.return_value = mock_resp
        ftfi.find_existing_issue("token", "owner/repo", "com.gb4pc.FooTest", "testBar")
        query = mock_requests.get.call_args[1]["params"]["q"]
        self.assertIn("label:test-failure", query)
        self.assertIn('"[FooTest] testBar" in:title', query)


class TestLookupIssueByTitle(unittest.TestCase):
    """The generic lookup that find_existing_issue is built on."""

    @patch("file_test_failure_issues.requests")
    def test_query_uses_supplied_title_and_label(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": []}
        mock_requests.get.return_value = mock_resp
        ftfi.lookup_issue_by_title("token", "owner/repo", "Some tracking issue", "ci")
        query = mock_requests.get.call_args[1]["params"]["q"]
        self.assertIn("repo:owner/repo is:issue", query)
        self.assertIn("label:ci", query)
        self.assertIn('"Some tracking issue" in:title', query)
        self.assertNotIn("is:open", query)
        self.assertNotIn("is:closed", query)

    @patch("file_test_failure_issues.requests")
    def test_found_reports_number_state_and_fetch_ok(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": [{"number": 5, "state": "closed"}]}
        mock_requests.get.return_value = mock_resp
        self.assertEqual(
            ftfi.lookup_issue_by_title("token", "owner/repo", "Title", "ci"),
            ftfi.IssueLookup(True, 5, "closed"),
        )

    @patch("file_test_failure_issues.requests")
    def test_no_match_is_a_successful_fetch(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": []}
        mock_requests.get.return_value = mock_resp
        lookup = ftfi.lookup_issue_by_title("token", "owner/repo", "Title", "ci")
        self.assertTrue(lookup.fetch_ok)
        self.assertIsNone(lookup.number)

    @patch("file_test_failure_issues.requests")
    def test_api_error_is_distinguishable_from_no_match(self, mock_requests):
        """The whole point of the type: a failed search must not read as "absent"."""
        mock_requests.get.side_effect = Exception("network error")
        lookup = ftfi.lookup_issue_by_title("token", "owner/repo", "Title", "ci")
        self.assertFalse(lookup.fetch_ok)
        self.assertIsNone(lookup.number)

    @patch("file_test_failure_issues.requests")
    def test_http_error_status_is_an_api_error(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("403 rate limited")
        mock_requests.get.return_value = mock_resp
        self.assertFalse(ftfi.lookup_issue_by_title("token", "owner/repo", "T", "ci").fetch_ok)


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

    @patch("file_test_failure_issues.requests")
    def test_sends_correct_labels(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"number": 1}
        mock_requests.post.return_value = mock_resp
        ftfi.create_issue("token", "owner/repo", "Title", "Body")
        call_kwargs = mock_requests.post.call_args
        payload = call_kwargs[1]["json"]
        self.assertEqual(["test-failure"], payload["labels"])

    @patch("file_test_failure_issues.requests")
    def test_labels_argument_overrides_the_default(self, mock_requests):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"number": 2}
        mock_requests.post.return_value = mock_resp
        ftfi.create_issue("token", "owner/repo", "Title", "Body", labels=["ci"])
        payload = mock_requests.post.call_args[1]["json"]
        self.assertEqual(["ci"], payload["labels"])


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


class TestReopenIssue(unittest.TestCase):
    @patch("file_test_failure_issues.requests")
    def test_returns_true_on_success(self, mock_requests):
        mock_resp = MagicMock()
        mock_requests.patch.return_value = mock_resp
        result = ftfi.reopen_issue("token", "owner/repo", 42)
        self.assertTrue(result)

    @patch("file_test_failure_issues.requests")
    def test_sends_state_open(self, mock_requests):
        mock_resp = MagicMock()
        mock_requests.patch.return_value = mock_resp
        ftfi.reopen_issue("token", "owner/repo", 42)
        call_kwargs = mock_requests.patch.call_args
        self.assertEqual(call_kwargs[1]["json"], {"state": "open"})

    @patch("file_test_failure_issues.requests")
    def test_returns_false_on_api_error(self, mock_requests):
        mock_requests.patch.side_effect = Exception("server error")
        result = ftfi.reopen_issue("token", "owner/repo", 42)
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

    def _call_process_failure(self):
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
            pr_url="",
        )

    @patch("file_test_failure_issues.add_issue_comment")
    @patch("file_test_failure_issues.create_issue")
    @patch("file_test_failure_issues.find_existing_issue")
    def test_comments_on_existing_open_issue_instead_of_creating(
        self, mock_find, mock_create, mock_comment
    ):
        mock_find.return_value = (55, "open")  # existing open issue found
        self._call_process_failure()
        mock_comment.assert_called_once()
        mock_create.assert_not_called()
        # Comment is posted to the existing issue number
        self.assertEqual(mock_comment.call_args[0][2], 55)
        # Comment body header uses "Failed on <sha> @ <timestamp>" format
        comment_body = mock_comment.call_args[0][3]
        self.assertIn("### Failed on", comment_body)
        self.assertIn(_FIXED_SHA[:7], comment_body)
        self.assertIn("2026-05-25", comment_body)

    @patch("file_test_failure_issues.add_issue_comment")
    @patch("file_test_failure_issues.create_issue")
    @patch("file_test_failure_issues.find_existing_issue")
    def test_creates_issue_when_no_duplicate(self, mock_find, mock_create, mock_comment):
        mock_find.return_value = None  # no existing issue
        mock_create.return_value = 77
        self._call_process_failure()
        mock_create.assert_called_once()
        # The first occurrence's per-run details go into a comment too (issue #504).
        mock_comment.assert_called_once()
        self.assertEqual(mock_comment.call_args[0][2], 77)
        comment_body = mock_comment.call_args[0][3]
        self.assertIn("### Failed on", comment_body)

    @patch("file_test_failure_issues.add_issue_comment")
    @patch("file_test_failure_issues.create_issue")
    @patch("file_test_failure_issues.find_existing_issue")
    def test_new_issue_body_is_aggregate_summary_not_run_details(
        self, mock_find, mock_create, mock_comment
    ):
        """The created issue body is the aggregate summary, not per-run details (issue #504)."""
        mock_find.return_value = None
        mock_create.return_value = 77
        self._call_process_failure()
        created_body = mock_create.call_args[0][3]
        self.assertIn("# Automated test failure", created_body)
        self.assertIn("Failed runs will be added as comments", created_body)
        # Per-run details (timestamp, sha, stack trace) must NOT be in the body.
        self.assertNotIn("### Failed on", created_body)
        self.assertNotIn(_FIXED_SHA[:7], created_body)
        self.assertNotIn("### Stack trace", created_body)

    @patch("file_test_failure_issues.add_issue_comment")
    @patch("file_test_failure_issues.create_issue")
    @patch("file_test_failure_issues.find_existing_issue")
    def test_no_comment_when_issue_creation_fails(self, mock_find, mock_create, mock_comment):
        """If the issue can't be created, don't try to comment on a nonexistent issue."""
        mock_find.return_value = None
        mock_create.return_value = None  # creation failed
        self._call_process_failure()
        mock_comment.assert_not_called()

    @patch("file_test_failure_issues.reopen_issue")
    @patch("file_test_failure_issues.add_issue_comment")
    @patch("file_test_failure_issues.create_issue")
    @patch("file_test_failure_issues.find_existing_issue")
    def test_reopens_closed_issue_before_commenting(
        self, mock_find, mock_create, mock_comment, mock_reopen
    ):
        mock_find.return_value = (88, "closed")  # existing closed issue
        self._call_process_failure()
        mock_reopen.assert_called_once_with("tok", "owner/repo", 88)
        mock_comment.assert_called_once()
        mock_create.assert_not_called()

    @patch("file_test_failure_issues.reopen_issue")
    @patch("file_test_failure_issues.add_issue_comment")
    @patch("file_test_failure_issues.create_issue")
    @patch("file_test_failure_issues.find_existing_issue")
    def test_does_not_reopen_open_issue(self, mock_find, mock_create, mock_comment, mock_reopen):
        mock_find.return_value = (55, "open")  # already open
        self._call_process_failure()
        mock_reopen.assert_not_called()
        mock_comment.assert_called_once()


# ---------------------------------------------------------------------------
# main() exit-0 tests
# ---------------------------------------------------------------------------


class TestMain(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)
        self._env_patch = patch.dict(
            os.environ,
            {
                "GITHUB_TOKEN": "test-token",
                "GITHUB_REPOSITORY": "owner/repo",
                "GITHUB_SERVER_URL": "https://github.com",
                "GITHUB_RUN_ID": "42",
                "WORKFLOW_RUN_SHA": "abc1234",
                "WORKFLOW_RUN_BRANCH": "main",
            },
        )
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
        # Use a separate temp directory for dir2 so it is not a subdirectory of
        # self.tmpdir; the recursive glob would otherwise pick up its XML files
        # when scanning the first suite directory.
        with tempfile.TemporaryDirectory() as e2e_tmpdir:
            dir2 = Path(e2e_tmpdir)
            _write_xml(self.tmpdir, "TEST-bar.xml", FAILING_XML)
            _write_xml(dir2, "TEST-baz.xml", ERROR_XML)
            result = ftfi.main(
                [
                    str(self.tmpdir),
                    "--suite-label",
                    "Unit Tests",
                    str(dir2),
                    "--suite-label",
                    "Instrumented Tests",
                ]
            )
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
        pairs = ftfi.parse_args(
            [
                "dir1",
                "--suite-label",
                "Label1",
                "dir2",
                "--suite-label",
                "Label2",
            ]
        )
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

    def test_instrumented_label_maps_to_instrumented_test_results(self):
        self.assertEqual(
            ftfi._artifact_name_for_label("Instrumented Tests"),
            "instrumented-test-results",
        )

    def test_e2e_label_maps_to_e2e_test_results(self):
        self.assertEqual(ftfi._artifact_name_for_label("E2E Tests"), "e2e-test-results")

    def test_instrumented_and_e2e_map_to_distinct_artifacts(self):
        """The two instrumented suites must not collide on one artifact name."""
        self.assertNotEqual(
            ftfi._artifact_name_for_label("Instrumented Tests"),
            ftfi._artifact_name_for_label("E2E Tests"),
        )

    def test_unknown_label_slugified(self):
        self.assertEqual(ftfi._artifact_name_for_label("My Custom Suite"), "my-custom-suite")


if __name__ == "__main__":
    unittest.main()
