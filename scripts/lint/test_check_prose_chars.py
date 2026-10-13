#!/usr/bin/env python3
"""Unit tests for check_prose_chars.py."""

import contextlib
import io
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import check_prose_chars  # noqa: E402

EM_DASH = "\u2014"
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


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

    def test_ascii_prose_is_clean(self):
        path = _write(
            self.tmpdir, "ok.kts", "// Debug signing is fine; it is installed only on emulators.\n"
        )
        self.assertEqual(check_prose_chars.find_violations(path), [])

    def test_em_dash_in_a_comment_is_reported_with_line_and_column(self):
        path = _write(self.tmpdir, "bad.kts", f"val x = 1\n// fine {EM_DASH} installed\n")
        self.assertEqual(check_prose_chars.find_violations(path), [(2, 9, EM_DASH)])

    def test_every_banned_character_is_recognized(self):
        banned = "".join(check_prose_chars.BANNED_CHARACTERS)
        path = _write(self.tmpdir, "all.txt", banned + "\n")
        found = [ch for _, _, ch in check_prose_chars.find_violations(path)]
        self.assertEqual(found, list(banned))

    def test_the_set_matches_the_string_resource_guard(self):
        # The two guards apply one rule, so they ban one set of characters.
        kotlin = os.path.join(
            REPO_ROOT, "app/src/test/java/com/gb4pc/StringResourceProseStyleTest.kt"
        )
        with open(kotlin, encoding="utf-8") as f:
            source = f.read()
        # The keys of its bannedCharacters map, each a Kotlin char literal holding a unicode
        # escape (backslash, "u", four hex digits). The pattern is split in two so no such
        # escape appears in this file to be decoded by an editor.
        key = re.compile(r"'\\" + r"u([0-9a-fA-F]{4})' to ")
        kotlin_banned = {chr(int(h, 16)) for h in key.findall(source)}
        self.assertEqual(kotlin_banned, set(check_prose_chars.BANNED_CHARACTERS))

    def test_other_non_ascii_text_is_not_flagged(self):
        # Box-drawing rules and accented letters are not what the rule bans.
        path = _write(self.tmpdir, "ok.sh", "# ── (a) café ──\n")
        self.assertEqual(check_prose_chars.find_violations(path), [])

    def test_non_utf8_file_is_skipped(self):
        path = os.path.join(self.tmpdir, "latin1.txt")
        with open(path, "wb") as f:
            f.write(b"\xab not utf-8 \xbb\n")
        self.assertEqual(check_prose_chars.find_violations(path), [])


class TestMain(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = self._tmp.name

    def _main(self, argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = check_prose_chars.main(argv)
        return rc, err.getvalue()

    def test_no_files_is_a_usage_error(self):
        self.assertEqual(self._main([])[0], 2)

    def test_clean_file_exits_zero(self):
        path = _write(self.tmpdir, "ok.md", "Plain prose--like this.\n")
        self.assertEqual(self._main([path]), (0, ""))

    def test_dirty_file_exits_one_and_names_the_location_and_fix(self):
        path = _write(self.tmpdir, "bad.yml", f"- name: CI pre-flight {EM_DASH} smoke check\n")
        rc, err = self._main([path])
        self.assertEqual(rc, 1)
        self.assertEqual(
            err,
            f"{path}:1:23: em dash U+2014 (use a comma, semicolon, parentheses, or '--')\n",
        )

    def test_one_dirty_file_among_clean_ones_exits_one(self):
        clean = _write(self.tmpdir, "ok.md", "fine\n")
        dirty = _write(self.tmpdir, "bad.md", "wait\u2026\n")
        self.assertEqual(self._main([clean, dirty])[0], 1)

    def test_gradlew_is_exempt(self):
        # Exemption is by repository-relative path, which is how lint.sh passes files.
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        os.chdir(self.tmpdir)
        _write(self.tmpdir, "gradlew", "#   \u00abcase\u00bb\n")
        self.assertEqual(self._main(["gradlew"]), (0, ""))
        self.assertEqual(self._main(["./gradlew"]), (0, ""))

    def test_a_file_named_gradlew_elsewhere_is_not_exempt(self):
        os.mkdir(os.path.join(self.tmpdir, "sub"))
        path = _write(self.tmpdir, os.path.join("sub", "gradlew"), "#   \u00abcase\u00bb\n")
        self.assertEqual(self._main([path])[0], 1)


if __name__ == "__main__":
    unittest.main()
