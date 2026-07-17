#!/usr/bin/env python3
"""Unit tests for check_md040.py."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import check_md040  # noqa: E402


def _write(tmpdir: str, name: str, content: str) -> str:
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class TestFindViolations(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = self._tmp.name

    def test_labeled_fence_is_not_a_violation(self):
        path = _write(self.tmpdir, "ok.md", "# Title\n\n```python\nprint(1)\n```\n")
        self.assertEqual(check_md040.find_violations(path), [])

    def test_bare_backtick_fence_is_a_violation(self):
        path = _write(self.tmpdir, "bad.md", "# Title\n\n```\nno language\n```\n")
        self.assertEqual(check_md040.find_violations(path), [3])

    def test_bare_tilde_fence_is_a_violation(self):
        path = _write(self.tmpdir, "bad.md", "# Title\n\n~~~\nno language\n~~~\n")
        self.assertEqual(check_md040.find_violations(path), [3])

    def test_multiple_violations_are_all_reported(self):
        content = "```\nfirst\n```\n\ntext\n\n```\nsecond\n```\n"
        path = _write(self.tmpdir, "bad.md", content)
        self.assertEqual(check_md040.find_violations(path), [1, 7])

    def test_mixed_labeled_and_unlabeled_fences(self):
        content = "```python\nok = 1\n```\n\n```\nbad\n```\n"
        path = _write(self.tmpdir, "mixed.md", content)
        self.assertEqual(check_md040.find_violations(path), [5])

    def test_unlabeled_fence_nested_as_content_inside_a_labeled_fence(self):
        # A longer, labeled outer fence containing a shorter bare ``` as
        # literal content (e.g. documenting Markdown syntax) is not itself a
        # violation: the inner ``` never opens a new fence because the outer
        # fence is still open.
        content = "````text\nExample:\n```\nbare, but just content here\n```\n````\n"
        path = _write(self.tmpdir, "nested.md", content)
        self.assertEqual(check_md040.find_violations(path), [])

    def test_no_fences_at_all(self):
        path = _write(self.tmpdir, "plain.md", "# Title\n\nJust prose.\n")
        self.assertEqual(check_md040.find_violations(path), [])

    def test_indented_code_block_is_not_a_fence(self):
        # A 4-space-indented code block is CommonMark's other code-block
        # syntax; it never declares a language and MD040 does not apply to it.
        path = _write(self.tmpdir, "indented.md", "# Title\n\n    plain code\n")
        self.assertEqual(check_md040.find_violations(path), [])


class TestMain(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = self._tmp.name

    def test_no_files_is_a_usage_error(self):
        self.assertEqual(check_md040.main([]), 2)

    def test_clean_files_exit_zero(self):
        path = _write(self.tmpdir, "ok.md", "# Title\n\n```python\nprint(1)\n```\n")
        self.assertEqual(check_md040.main([path]), 0)

    def test_dirty_file_exits_one(self):
        path = _write(self.tmpdir, "bad.md", "```\nbad\n```\n")
        self.assertEqual(check_md040.main([path]), 1)

    def test_one_dirty_file_among_clean_ones_exits_one(self):
        clean = _write(self.tmpdir, "ok.md", "```python\nprint(1)\n```\n")
        dirty = _write(self.tmpdir, "bad.md", "```\nbad\n```\n")
        self.assertEqual(check_md040.main([clean, dirty]), 1)


if __name__ == "__main__":
    unittest.main()
