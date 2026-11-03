#!/usr/bin/env python3
"""Unit tests for check_config.py."""

import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import check_config

GOOD = {
    "repository": "owner/repo",
    "named_checks": ["build", "lint"],
    "ignored_check_regex": r"^Merge gate \(not failure\)$",
    "artifact_name_regex": "^feed-",
    "test_marker_regex": "##TEST##",
    "verification_plan_marker": "<!-- orchestrate-verification-plan -->",
    "default_model": "opus",
    "max_rounds": 4,
}


def errors_of(config):
    errors, _ = check_config.problems(config)
    return errors


class TestProblems(unittest.TestCase):
    def test_a_complete_config_has_no_problems(self):
        self.assertEqual(check_config.problems(GOOD), ([], []))

    def test_only_required_keys_warns_about_each_default(self):
        errors, warnings = check_config.problems({"repository": "o/r", "named_checks": ["a"]})
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), len(check_config.OPTIONAL_KEYS))
        self.assertTrue(any("max_rounds" in w and "4" in w for w in warnings), warnings)

    def test_not_an_object(self):
        self.assertEqual(errors_of([]), ["the file must hold a JSON object"])

    def test_missing_required_keys(self):
        errors = errors_of({})
        self.assertEqual(len(errors), 2)
        self.assertTrue(all("missing required key" in e for e in errors), errors)

    def test_repository_shape(self):
        for bad in ("owner", "owner/repo/extra", 7, "", "a b/c"):
            self.assertTrue(
                any("repository" in e for e in errors_of({**GOOD, "repository": bad})), bad
            )

    def test_named_checks_shape(self):
        for bad in ([], "build", [""], [1], ["a", "a"]):
            errors = errors_of({**GOOD, "named_checks": bad})
            self.assertTrue(any("named_checks" in e or "named check" in e for e in errors), bad)

    def test_marker_must_be_an_html_comment(self):
        for bad in ("plan", "<!-- -->", "<!-- a --> <!-- b -->", 3):
            self.assertTrue(
                any(
                    "verification_plan_marker" in e
                    for e in errors_of({**GOOD, "verification_plan_marker": bad})
                ),
                bad,
            )
        self.assertEqual(
            errors_of({**GOOD, "verification_plan_marker": "<!-- gb4pc-verification-plan -->"}), []
        )

    def test_max_rounds_is_a_positive_integer(self):
        for bad in (0, -1, "4", 2.5, True):
            self.assertTrue(
                any("max_rounds" in e for e in errors_of({**GOOD, "max_rounds": bad})), bad
            )

    def test_default_model_is_a_name(self):
        self.assertTrue(any("default_model" in e for e in errors_of({**GOOD, "default_model": ""})))

    def test_monitor_regexes_must_compile(self):
        errors = errors_of({**GOOD, "ignored_check_regex": "("})
        self.assertTrue(any("ignored_check_regex does not compile" in e for e in errors), errors)
        errors = errors_of({**GOOD, "artifact_name_regex": 5})
        self.assertTrue(
            any("artifact_name_regex must be a regex string" in e for e in errors), errors
        )

    def test_a_named_check_may_not_be_ignored(self):
        errors = errors_of({**GOOD, "ignored_check_regex": "^lint$"})
        self.assertTrue(
            any("'lint' is matched by ignored_check_regex" in e for e in errors), errors
        )

    def test_unknown_keys_and_comments_are_ignored(self):
        self.assertEqual(errors_of({**GOOD, "_comment": "x", "future_key": 1}), [])


class TestMain(unittest.TestCase):
    def run_main(self, content):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        out = io.StringIO()
        try:
            with patch("sys.stdout", new=out):
                code = check_config.main(["check_config.py", path])
        finally:
            os.remove(path)
        return code, out.getvalue()

    def test_good_file_exits_zero(self):
        code, out = self.run_main(json.dumps(GOOD))
        self.assertEqual(code, 0)
        self.assertTrue(out.rstrip().endswith(": ok"), out)

    def test_bad_file_exits_one_and_names_each_problem(self):
        code, out = self.run_main(json.dumps({"repository": "x"}))
        self.assertEqual(code, 1)
        self.assertIn("error: repository must be", out)
        self.assertIn("error: missing required key 'named_checks'", out)

    def test_invalid_json_exits_one(self):
        code, out = self.run_main("{")
        self.assertEqual(code, 1)
        self.assertIn("cannot read as JSON", out)

    def test_usage(self):
        with patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(check_config.main(["check_config.py"]), 1)


if __name__ == "__main__":
    unittest.main()
