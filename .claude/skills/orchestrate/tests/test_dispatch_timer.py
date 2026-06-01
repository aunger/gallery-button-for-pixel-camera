#!/usr/bin/env python3
"""test_dispatch_timer.py--Tests for the /orchestrate skill's dispatch_timer.py.

Covers:
  (a) format_instant produces the canonical Z-suffixed UTC format
  (b) parse_instant round-trips a canonical string
  (c) parse_instant rejects a malformed timestamp
  (d) format_elapsed across second, minute, and hour boundaries
  (e) format_elapsed rejects a negative duration
  (f) build_report composes start/end/elapsed correctly
  (g) main mark prints a parseable canonical timestamp, exit 0
  (h) main report prints the expected line, exit 0
  (i) main report with end before start exits 2
  (j) main report with a malformed timestamp exits 2
  (k) main with no subcommand exits 2

No network or environment dependencies. Exits non-zero on any failure.
"""

import datetime as dt
import importlib.util
import io
import os
import sys
import unittest
import unittest.mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODULE_PATH = os.path.join(_HERE, os.pardir, "scripts", "dispatch_timer.py")

_spec = importlib.util.spec_from_file_location("dispatch_timer", _MODULE_PATH)
dispatch_timer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dispatch_timer)


class FormatInstantTest(unittest.TestCase):
    def test_canonical_format(self):
        instant = dt.datetime(2026, 6, 1, 15, 24, 7, tzinfo=dt.timezone.utc)
        self.assertEqual(dispatch_timer.format_instant(instant), "2026-06-01T15:24:07Z")

    def test_non_utc_is_normalized(self):
        tz = dt.timezone(dt.timedelta(hours=5))
        instant = dt.datetime(2026, 6, 1, 20, 24, 7, tzinfo=tz)
        self.assertEqual(dispatch_timer.format_instant(instant), "2026-06-01T15:24:07Z")


class ParseInstantTest(unittest.TestCase):
    def test_round_trip(self):
        text = "2026-06-01T15:24:07Z"
        self.assertEqual(dispatch_timer.format_instant(dispatch_timer.parse_instant(text)), text)

    def test_rejects_malformed(self):
        with self.assertRaises(ValueError):
            dispatch_timer.parse_instant("2026-06-01 15:24:07")


class FormatElapsedTest(unittest.TestCase):
    def test_seconds_only(self):
        self.assertEqual(dispatch_timer.format_elapsed(dt.timedelta(seconds=9)), "9s")

    def test_minutes_and_seconds(self):
        self.assertEqual(dispatch_timer.format_elapsed(dt.timedelta(minutes=2, seconds=5)), "2m 5s")

    def test_hours_minutes_seconds(self):
        delta = dt.timedelta(hours=1, minutes=0, seconds=3)
        self.assertEqual(dispatch_timer.format_elapsed(delta), "1h 0m 3s")

    def test_negative_rejected(self):
        with self.assertRaises(ValueError):
            dispatch_timer.format_elapsed(dt.timedelta(seconds=-1))


class BuildReportTest(unittest.TestCase):
    def test_composes_line(self):
        line = dispatch_timer.build_report("2026-06-01T15:24:00Z", "2026-06-01T15:26:05Z")
        self.assertEqual(
            line,
            "dispatch: start 2026-06-01T15:24:00Z end 2026-06-01T15:26:05Z elapsed 2m 5s",
        )


class MainTest(unittest.TestCase):
    def _run(self, argv):
        out = io.StringIO()
        err = io.StringIO()
        with unittest.mock.patch.object(sys, "stdout", out), \
                unittest.mock.patch.object(sys, "stderr", err):
            code = dispatch_timer.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_mark_prints_parseable_timestamp(self):
        code, out, _ = self._run(["mark"])
        self.assertEqual(code, 0)
        # The printed value must parse back through parse_instant.
        dispatch_timer.parse_instant(out.strip())

    def test_report_happy_path(self):
        code, out, _ = self._run(
            ["report", "--start", "2026-06-01T15:24:00Z", "--end", "2026-06-01T15:24:30Z"]
        )
        self.assertEqual(code, 0)
        self.assertIn("elapsed 30s", out)

    def test_report_end_before_start(self):
        code, _, err = self._run(
            ["report", "--start", "2026-06-01T15:24:30Z", "--end", "2026-06-01T15:24:00Z"]
        )
        self.assertEqual(code, 2)
        self.assertIn("before start", err)

    def test_report_malformed_timestamp(self):
        code, _, _ = self._run(
            ["report", "--start", "nonsense", "--end", "2026-06-01T15:24:00Z"]
        )
        self.assertEqual(code, 2)

    def test_no_subcommand(self):
        code, _, _ = self._run([])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
