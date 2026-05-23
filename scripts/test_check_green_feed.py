#!/usr/bin/env python3
"""Unit tests for check_green_feed.py."""

import io
import os
import struct
import sys
import tempfile
import unittest
import zlib
from unittest.mock import MagicMock, call, patch

# Allow importing the script as a module.
sys.path.insert(0, os.path.dirname(__file__))
import check_green_feed as cgf  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_png(width: int, height: int, r: int, g: int, b: int) -> bytes:
    """Build a minimal valid PNG filled with a solid RGB color."""

    def chunk(name: bytes, data: bytes) -> bytes:
        length = struct.pack(">I", len(data))
        crc = struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)
        return length + name + data + crc

    # IHDR
    ihdr_data = struct.pack(">IIBB", width, height, 8, 2) + b"\x00\x00\x00"
    ihdr = chunk(b"IHDR", ihdr_data)

    # Build raw image data (filter byte 0 per scanline + RGB pixels)
    raw = b""
    row = bytes([r, g, b] * width)
    for _ in range(height):
        raw += b"\x00" + row

    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")

    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend


def _write_png(r: int, g: int, b: int, width: int = 400, height: int = 400) -> str:
    """Write a solid-color PNG to a temp file and return its path."""
    data = _make_png(width, height, r, g, b)
    fd, path = tempfile.mkstemp(suffix=".png")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


# ---------------------------------------------------------------------------
# check_image tests (unchanged single-shot behaviour)
# ---------------------------------------------------------------------------

class TestCheckImageGreen(unittest.TestCase):
    def test_pass_on_green_image(self):
        path = _write_png(cgf.TARGET_R, cgf.TARGET_G, cgf.TARGET_B)
        try:
            self.assertEqual(cgf.check_image(path), 0)
        finally:
            os.unlink(path)

    def test_fail_on_non_green_image(self):
        path = _write_png(255, 0, 0)  # red
        try:
            self.assertEqual(cgf.check_image(path), 1)
        finally:
            os.unlink(path)

    def test_error_on_missing_file(self):
        self.assertEqual(cgf.check_image("/nonexistent/path.png"), 2)


# ---------------------------------------------------------------------------
# main() --adb retry-loop tests (new behaviour)
# ---------------------------------------------------------------------------

class TestMainAdbRetryLoop(unittest.TestCase):
    """Tests for the --adb retry-loop path through main()."""

    def _run_main(self, extra_argv: list[str]) -> int:
        with patch.object(sys, "argv", ["check_green_feed.py"] + extra_argv):
            return cgf.main()

    def _make_ok_run_result(self):
        """Return a mock CompletedProcess with returncode=0 and empty stderr."""
        r = MagicMock()
        r.returncode = 0
        r.stderr = ""
        return r

    def test_succeeds_on_first_attempt(self):
        """When the image passes on the first try, main exits 0."""
        green_png = _write_png(cgf.TARGET_R, cgf.TARGET_G, cgf.TARGET_B)
        try:
            with patch("check_green_feed.subprocess.run",
                       return_value=self._make_ok_run_result()) as mock_run, \
                 patch("check_green_feed.time.sleep") as mock_sleep, \
                 patch("builtins.open", create=True) as mock_open:
                # subprocess.run writes nothing; check_image reads the pre-existing file
                mock_open.return_value.__enter__ = lambda s: s
                mock_open.return_value.__exit__ = MagicMock(return_value=False)
                with patch("check_green_feed.check_image", return_value=0) as mock_check:
                    result = self._run_main(["--adb", "/fake/adb", green_png])
            self.assertEqual(result, 0)
            # Two sleeps per attempt: one at the top of the loop and one between
            # the swipe and the screencap.
            self.assertEqual(mock_sleep.call_count, 2)
            mock_sleep.assert_called_with(cgf.RETRY_DELAY_SECONDS)
            mock_check.assert_called_once_with(green_png)
        finally:
            os.unlink(green_png)

    def test_retries_until_success(self):
        """main retries when check_image returns 1 and succeeds on a later attempt."""
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            side_effects = [1, 1, 0]  # fail, fail, pass
            with patch("check_green_feed.subprocess.run",
                       return_value=self._make_ok_run_result()), \
                 patch("check_green_feed.time.sleep"), \
                 patch("builtins.open", unittest.mock.mock_open()), \
                 patch("check_green_feed.check_image", side_effect=side_effects):
                result = self._run_main(["--adb", "/fake/adb", path])
            self.assertEqual(result, 0)
        finally:
            os.unlink(path)

    def test_returns_1_after_max_attempts(self):
        """main returns 1 when all MAX_ATTEMPTS fail."""
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            with patch("check_green_feed.subprocess.run",
                       return_value=self._make_ok_run_result()), \
                 patch("check_green_feed.time.sleep"), \
                 patch("builtins.open", unittest.mock.mock_open()), \
                 patch("check_green_feed.check_image", return_value=1):
                result = self._run_main(["--adb", "/fake/adb", path])
            self.assertEqual(result, 1)
        finally:
            os.unlink(path)

    def test_sleep_called_on_every_attempt(self):
        """Sleep is called twice per attempt: once at the top of the loop and
        once between the swipe and the screencap, for 2 * MAX_ATTEMPTS total."""
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            with patch("check_green_feed.subprocess.run",
                       return_value=self._make_ok_run_result()), \
                 patch("check_green_feed.time.sleep") as mock_sleep, \
                 patch("builtins.open", unittest.mock.mock_open()), \
                 patch("check_green_feed.check_image", return_value=1):
                self._run_main(["--adb", "/fake/adb", path])
            self.assertEqual(mock_sleep.call_count, 2 * cgf.MAX_ATTEMPTS)
            mock_sleep.assert_called_with(cgf.RETRY_DELAY_SECONDS)
        finally:
            os.unlink(path)

    def test_wakeup_and_swipe_sent_before_each_screencap(self):
        """KEYCODE_WAKEUP (224) and an upward swipe are sent before every screencap,
        in that order, with the screencap coming after both in each retry iteration.

        The per-retry ANR check (dumpsys window) runs between the swipe and the
        screencap, so the ordering within a single iteration is:
          wakeup → swipe → [sleep] → dumpsys-window → screencap
        """
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            # Run two iterations (fail once, then pass) so we can check ordering
            # across multiple retry cycles.
            side_effects = [1, 0]
            with patch("check_green_feed.subprocess.run",
                       return_value=self._make_ok_run_result()) as mock_run, \
                 patch("check_green_feed.time.sleep"), \
                 patch("builtins.open", unittest.mock.mock_open()), \
                 patch("check_green_feed.check_image", side_effect=side_effects):
                self._run_main(["--adb", "/fake/adb", path])

            # Build a flat list of the command argument lists from each call.
            arg_lists = [c.args[0] for c in mock_run.call_args_list if c.args]

            def is_wakeup(args):
                return "keyevent" in args and "224" in args

            def is_swipe(args):
                return "swipe" in args and "300" in args and "1000" in args

            def is_screencap(args):
                return "screencap" in args

            def is_dumpsys_window(args):
                return "dumpsys" in args and "window" in args

            # Walk the call list and verify the ordering within every retry
            # iteration: wakeup precedes swipe, which precedes the screencap
            # (there is now a dumpsys-window call between swipe and screencap).
            screencap_indices = [i for i, a in enumerate(arg_lists) if is_screencap(a)]
            self.assertGreater(len(screencap_indices), 0, "No screencap call found")

            for sc_idx in screencap_indices:
                self.assertGreaterEqual(sc_idx, 3,
                    "Not enough preceding calls before screencap to fit "
                    "wakeup + swipe + dumpsys-window")
                dumpsys_idx = sc_idx - 1
                swipe_idx = sc_idx - 2
                wakeup_idx = sc_idx - 3
                self.assertTrue(
                    is_dumpsys_window(arg_lists[dumpsys_idx]),
                    f"Call immediately before screencap (index {dumpsys_idx}) is not "
                    f"a dumpsys window call: {arg_lists[dumpsys_idx]}"
                )
                self.assertTrue(
                    is_swipe(arg_lists[swipe_idx]),
                    f"Call two before screencap (index {swipe_idx}) is not "
                    f"an upward swipe: {arg_lists[swipe_idx]}"
                )
                self.assertTrue(
                    is_wakeup(arg_lists[wakeup_idx]),
                    f"Call three before screencap (index {wakeup_idx}) is not "
                    f"KEYCODE_WAKEUP (224): {arg_lists[wakeup_idx]}"
                )
        finally:
            os.unlink(path)

    def test_sleep_between_swipe_and_screencap(self):
        """A sleep call must appear between the swipe and every screencap so that
        any swipe animation has settled before the screen is captured.

        The per-retry ANR check (dumpsys window) runs after the sleep but before
        the screencap, so the ordering within a single iteration is:
          swipe → sleep → other(dumpsys-window) → screencap
        """
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            # Two iterations so ordering can be verified across retry cycles.
            side_effects = [1, 0]

            # Track interleaved calls to both subprocess.run and time.sleep in
            # the order they actually happen.
            call_log: list[str] = []

            def fake_run(args, **kwargs):
                if "swipe" in args and "300" in args and "1000" in args:
                    call_log.append("swipe")
                elif "screencap" in args:
                    call_log.append("screencap")
                else:
                    call_log.append("other")
                r = MagicMock()
                r.returncode = 0
                r.stderr = ""
                return r

            def fake_sleep(seconds):
                call_log.append(f"sleep({seconds})")

            with patch("check_green_feed.subprocess.run", side_effect=fake_run), \
                 patch("check_green_feed.time.sleep", side_effect=fake_sleep), \
                 patch("builtins.open", unittest.mock.mock_open()), \
                 patch("check_green_feed.check_image", side_effect=side_effects):
                self._run_main(["--adb", "/fake/adb", path])

            # Find every screencap in the log and assert the expected ordering:
            #   swipe → sleep → other(dumpsys-window) → screencap
            # The per-retry ANR check (dumpsys-window) sits between the sleep and
            # the screencap, so the sleep is now two positions before screencap.
            screencap_positions = [i for i, e in enumerate(call_log) if e == "screencap"]
            self.assertGreater(len(screencap_positions), 0, "No screencap logged")

            for sc_pos in screencap_positions:
                self.assertGreaterEqual(sc_pos, 3,
                    "Not enough preceding log entries before screencap")
                self.assertEqual(
                    call_log[sc_pos - 1],
                    "other",
                    f"Entry immediately before screencap (index {sc_pos - 1}) is not "
                    f"the ANR check (other): {call_log[sc_pos - 1]}"
                )
                self.assertIn(
                    "sleep",
                    call_log[sc_pos - 2],
                    f"Entry two before screencap (index {sc_pos - 2}) is not "
                    f"a sleep call: {call_log[sc_pos - 2]}"
                )
                self.assertEqual(
                    call_log[sc_pos - 3],
                    "swipe",
                    f"Entry three before screencap (index {sc_pos - 3}) is not "
                    f"a swipe call: {call_log[sc_pos - 3]}"
                )
        finally:
            os.unlink(path)

    def test_single_shot_mode_without_adb_flag(self):
        """Without --adb, main calls check_image directly and returns its result."""
        path = _write_png(255, 0, 0)  # red => fail
        try:
            result = self._run_main([path])
            self.assertEqual(result, 1)
        finally:
            os.unlink(path)

    def test_usage_error_with_no_args(self):
        result = self._run_main([])
        self.assertEqual(result, 2)

    def test_usage_error_with_adb_but_no_image(self):
        result = self._run_main(["--adb", "/fake/adb"])
        self.assertEqual(result, 2)


class TestInputServiceFailure(unittest.TestCase):
    """Tests for the input-service-unavailability handling in the retry loop."""

    def _run_main(self, extra_argv: list[str]) -> int:
        with patch.object(sys, "argv", ["check_green_feed.py"] + extra_argv):
            return cgf.main()

    def _make_run_result(self, returncode=0, stderr=""):
        """Build a mock CompletedProcess-like object."""
        result = MagicMock()
        result.returncode = returncode
        result.stderr = stderr
        return result

    def test_wakeup_failure_causes_extra_sleep_and_warning(self):
        """When KEYCODE_WAKEUP fails (non-zero exit), an extra sleep is inserted
        and a warning is printed to stderr; the screencap still runs."""
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            # KEYCODE_WAKEUP fails; swipe succeeds; dumpsys window (ANR check)
            # succeeds; screencap succeeds.
            wakeup_fail = self._make_run_result(returncode=1, stderr="")
            swipe_ok = self._make_run_result(returncode=0, stderr="")
            dumpsys_ok = self._make_run_result(returncode=0, stderr="")
            screencap_ok = self._make_run_result(returncode=0, stderr="")

            run_side_effects = [wakeup_fail, swipe_ok, dumpsys_ok, screencap_ok]

            sleep_calls: list[float] = []

            def fake_sleep(s):
                sleep_calls.append(s)

            with patch("check_green_feed.subprocess.run",
                       side_effect=run_side_effects) as mock_run, \
                 patch("check_green_feed.time.sleep", side_effect=fake_sleep), \
                 patch("builtins.open", unittest.mock.mock_open()), \
                 patch("check_green_feed.check_image", return_value=0), \
                 patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
                result = self._run_main(["--adb", "/fake/adb", path])

            self.assertEqual(result, 0)
            # Extra sleep must have been inserted (more than the standard 2 per attempt).
            self.assertGreater(len(sleep_calls), 2,
                "Expected extra sleep when wakeup fails, but sleep count was not > 2")
            # A warning must have been written to stderr.
            warning_output = mock_stderr.getvalue()
            self.assertIn("WARNING", warning_output)
            self.assertIn("input service unavailable", warning_output)
            # Screencap must still have been called.
            arg_lists = [c.args[0] for c in mock_run.call_args_list if c.args]
            screencap_calls = [a for a in arg_lists if "screencap" in a]
            self.assertEqual(len(screencap_calls), 1, "screencap must still run despite wakeup failure")
        finally:
            os.unlink(path)

    def test_swipe_failure_causes_extra_sleep_and_warning(self):
        """When the swipe command stderr contains 'Can't find service', an extra
        sleep is inserted and a warning is printed; the screencap still runs."""
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            wakeup_ok = self._make_run_result(returncode=0, stderr="")
            swipe_fail = self._make_run_result(
                returncode=1, stderr="cmd: Can't find service: input"
            )
            dumpsys_ok = self._make_run_result(returncode=0, stderr="")
            screencap_ok = self._make_run_result(returncode=0, stderr="")

            run_side_effects = [wakeup_ok, swipe_fail, dumpsys_ok, screencap_ok]

            sleep_calls: list[float] = []

            def fake_sleep(s):
                sleep_calls.append(s)

            with patch("check_green_feed.subprocess.run",
                       side_effect=run_side_effects) as mock_run, \
                 patch("check_green_feed.time.sleep", side_effect=fake_sleep), \
                 patch("builtins.open", unittest.mock.mock_open()), \
                 patch("check_green_feed.check_image", return_value=0), \
                 patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
                result = self._run_main(["--adb", "/fake/adb", path])

            self.assertEqual(result, 0)
            self.assertGreater(len(sleep_calls), 2,
                "Expected extra sleep when swipe fails, but sleep count was not > 2")
            warning_output = mock_stderr.getvalue()
            self.assertIn("WARNING", warning_output)
            self.assertIn("input service unavailable", warning_output)
            arg_lists = [c.args[0] for c in mock_run.call_args_list if c.args]
            screencap_calls = [a for a in arg_lists if "screencap" in a]
            self.assertEqual(len(screencap_calls), 1, "screencap must still run despite swipe failure")
        finally:
            os.unlink(path)

    def test_input_service_failure_does_not_abort_retry_loop(self):
        """Input service failure on every attempt does not prevent the retry loop
        from continuing; main eventually returns 1 after MAX_ATTEMPTS."""
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            # Every wakeup and swipe fails; screencap always runs.
            def make_run_result_for(args, **kwargs):
                cmd = args
                if "keyevent" in cmd or "swipe" in cmd:
                    r = MagicMock()
                    r.returncode = 1
                    r.stderr = "cmd: Can't find service: input"
                    return r
                r = MagicMock()
                r.returncode = 0
                r.stderr = ""
                return r

            screencap_count = [0]

            def counting_run(args, **kwargs):
                if "screencap" in args:
                    screencap_count[0] += 1
                return make_run_result_for(args, **kwargs)

            with patch("check_green_feed.subprocess.run",
                       side_effect=counting_run), \
                 patch("check_green_feed.time.sleep"), \
                 patch("builtins.open", unittest.mock.mock_open()), \
                 patch("check_green_feed.check_image", return_value=1), \
                 patch("sys.stderr", new_callable=io.StringIO):
                result = self._run_main(["--adb", "/fake/adb", path])

            self.assertEqual(result, 1,
                "main should return 1 when all attempts fail, not abort early")
            self.assertEqual(screencap_count[0], cgf.MAX_ATTEMPTS,
                f"screencap should run on every attempt; expected {cgf.MAX_ATTEMPTS}, got {screencap_count[0]}")
        finally:
            os.unlink(path)

    def test_no_extra_sleep_when_input_service_succeeds(self):
        """When both input calls succeed, exactly 2 sleeps occur per attempt
        (the standard behavior is not regressed)."""
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            ok = self._make_run_result(returncode=0, stderr="")

            with patch("check_green_feed.subprocess.run", return_value=ok), \
                 patch("check_green_feed.time.sleep") as mock_sleep, \
                 patch("builtins.open", unittest.mock.mock_open()), \
                 patch("check_green_feed.check_image", return_value=0):
                result = self._run_main(["--adb", "/fake/adb", path])

            self.assertEqual(result, 0)
            # Exactly 2 sleeps on the first (successful) attempt: one at the top
            # of the loop and one between swipe and screencap.
            self.assertEqual(mock_sleep.call_count, 2,
                f"Expected 2 sleeps when input service succeeds, got {mock_sleep.call_count}")
        finally:
            os.unlink(path)


class TestPerRetryAnrDismiss(unittest.TestCase):
    """Tests for the per-retry ANR dialog check + dismiss in the retry loop."""

    def _run_main(self, extra_argv: list[str]) -> int:
        with patch.object(sys, "argv", ["check_green_feed.py"] + extra_argv):
            return cgf.main()

    def _make_run_result(self, returncode=0, stderr="", stdout=""):
        result = MagicMock()
        result.returncode = returncode
        result.stderr = stderr
        result.stdout = stdout
        return result

    def test_anr_dialog_on_first_attempt_is_dismissed_before_screencap(self):
        """When dumpsys window reports 'Application Not Responding' before the
        first screencap, KEYCODE_ENTER is sent and an extra sleep follows.  After
        sending KEYCODE_ENTER the dismiss function polls dumpsys window again to
        confirm the dialog is gone before returning.  The screencap still runs
        and the retry loop continues normally.

        _dump_first_failure_diagnostics is patched out so that its own subprocess
        calls do not interfere with the accounting of ANR-check calls.
        """
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            anr_window = self._make_run_result(
                returncode=0,
                stdout="  mCurrentFocus=Window{... u0 Application Not Responding: com.google.android.apps.nexuslauncher}",
            )
            clear_window = self._make_run_result(returncode=0, stdout="WindowState idle")
            keyevent_ok = self._make_run_result(returncode=0)
            screencap_ok = self._make_run_result(returncode=0)

            # Attempt 1:
            #   wakeup → swipe → dumpsys-window(ANR present) →
            #   KEYCODE_ENTER → [sleep] → dumpsys-window(confirm: clear) →
            #   screencap(fails)
            # Attempt 2:
            #   wakeup → swipe → dumpsys-window(detect: clear) → screencap(passes)
            run_side_effects = [
                # attempt 1
                self._make_run_result(),   # wakeup keyevent 224
                self._make_run_result(),   # swipe
                anr_window,                # dumpsys window → ANR detected
                keyevent_ok,               # KEYCODE_ENTER dismiss
                clear_window,              # dumpsys window → confirm dismissed
                screencap_ok,              # screencap (written to file)
                # attempt 2
                self._make_run_result(),   # wakeup keyevent 224
                self._make_run_result(),   # swipe
                clear_window,              # dumpsys window → detect: clear
                screencap_ok,              # screencap (written to file)
            ]

            keyback_calls: list[list[str]] = []
            dumpsys_calls: list[list[str]] = []

            def tracking_run(args, **kwargs):
                if "KEYCODE_ENTER" in args:
                    keyback_calls.append(list(args))
                if "dumpsys" in args and "window" in args:
                    dumpsys_calls.append(list(args))
                return run_side_effects.pop(0)

            sleep_calls: list[float] = []

            with patch("check_green_feed.subprocess.run",
                       side_effect=tracking_run), \
                 patch("check_green_feed.time.sleep",
                       side_effect=lambda s: sleep_calls.append(s)), \
                 patch("builtins.open", unittest.mock.mock_open()), \
                 patch("check_green_feed._dump_first_failure_diagnostics"), \
                 patch("check_green_feed.check_image", side_effect=[1, 0]):
                result = self._run_main(["--adb", "/fake/adb", path])

            self.assertEqual(result, 0, "main should succeed on the second attempt")

            # Attempt 1 has 2 dumpsys calls (detect + confirm-after-dismiss);
            # attempt 2 has 1 (detect only, no ANR found).  Total = 3.
            self.assertEqual(
                len(dumpsys_calls), 3,
                f"Expected 3 dumpsys window calls (2 for attempt-1 ANR dismiss + 1 for attempt-2 detect), "
                f"got {len(dumpsys_calls)}"
            )

            # KEYCODE_ENTER must be sent exactly once (for the first-attempt ANR).
            keyback_back_calls = [c for c in keyback_calls if "KEYCODE_ENTER" in c]
            self.assertEqual(
                len(keyback_back_calls), 1,
                f"Expected 1 KEYCODE_ENTER for ANR dismiss, got {len(keyback_back_calls)}"
            )

            # An extra sleep must follow the KEYCODE_ENTER dismiss.
            # Standard sleeps per attempt: 2 (top-of-loop + swipe-settle).
            # Attempt 1 also has the ANR dismiss poll sleep → total > 2 * 1 = 2 for
            # the first attempt, i.e. total sleep count across both attempts > 4.
            self.assertGreater(
                len(sleep_calls), 4,
                f"Expected extra sleep after ANR dismiss; got {len(sleep_calls)} total sleeps"
            )
        finally:
            os.unlink(path)

    def test_no_anr_dialog_means_no_keycode_back(self):
        """When dumpsys window reports no ANR dialog, KEYCODE_ENTER is not sent
        and the screencap proceeds immediately (no extra sleep)."""
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            clear_window = self._make_run_result(returncode=0, stdout="WindowState idle")

            keyback_calls: list = []

            def tracking_run(args, **kwargs):
                if "KEYCODE_ENTER" in args:
                    keyback_calls.append(args)
                r = MagicMock()
                r.returncode = 0
                r.stderr = ""
                r.stdout = clear_window.stdout
                return r

            with patch("check_green_feed.subprocess.run",
                       side_effect=tracking_run), \
                 patch("check_green_feed.time.sleep"), \
                 patch("builtins.open", unittest.mock.mock_open()), \
                 patch("check_green_feed.check_image", return_value=0):
                result = self._run_main(["--adb", "/fake/adb", path])

            self.assertEqual(result, 0)
            self.assertEqual(
                len(keyback_calls), 0,
                f"KEYCODE_ENTER must not be sent when there is no ANR dialog; "
                f"got {len(keyback_calls)} call(s)"
            )
        finally:
            os.unlink(path)

    def test_anr_check_runs_before_every_screencap(self):
        """dumpsys window is called before every screencap attempt, not just the
        first, so the retry loop remains self-defending throughout.

        _dump_first_failure_diagnostics is patched out so that its own dumpsys
        window call does not inflate the count for the per-retry ANR check.
        """
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            screencap_count = [0]
            dumpsys_count = [0]

            def tracking_run(args, **kwargs):
                if "dumpsys" in args and "window" in args:
                    dumpsys_count[0] += 1
                elif "screencap" in args:
                    screencap_count[0] += 1
                r = MagicMock()
                r.returncode = 0
                r.stderr = ""
                r.stdout = ""
                return r

            attempts = 3
            check_results = [1] * (attempts - 1) + [0]

            with patch("check_green_feed.subprocess.run",
                       side_effect=tracking_run), \
                 patch("check_green_feed.time.sleep"), \
                 patch("builtins.open", unittest.mock.mock_open()), \
                 patch("check_green_feed._dump_first_failure_diagnostics"), \
                 patch("check_green_feed.check_image", side_effect=check_results):
                result = self._run_main(["--adb", "/fake/adb", path])

            self.assertEqual(result, 0)
            self.assertEqual(
                screencap_count[0], attempts,
                f"Expected {attempts} screencaps, got {screencap_count[0]}"
            )
            self.assertEqual(
                dumpsys_count[0], attempts,
                f"Expected {attempts} dumpsys window calls (one per attempt, "
                f"from the per-retry ANR check), got {dumpsys_count[0]}"
            )
        finally:
            os.unlink(path)


class TestDismissAnrIfPresent(unittest.TestCase):
    """Unit tests for _dismiss_anr_if_present() called directly."""

    def _make_run_result(self, returncode=0, stderr="", stdout=""):
        result = MagicMock()
        result.returncode = returncode
        result.stderr = stderr
        result.stdout = stdout
        return result

    def test_all_retries_exhausted_returns_without_raising(self):
        """When dumpsys window always reports 'Application Not Responding' the
        function sends KEYCODE_ENTER exactly _ANR_DISMISS_MAX_RETRIES times and
        then returns (does not raise) so the outer retry loop can keep going.
        The final log message must mention that the dialog persisted after N retries.
        """
        anr_window = self._make_run_result(
            stdout="Application Not Responding: com.google.android.apps.nexuslauncher"
        )

        keyback_calls: list[list] = []
        run_side_effects: list = []
        # Initial detect: ANR present.
        run_side_effects.append(anr_window)
        # For each retry: KEYCODE_ENTER + confirm dumpsys (always shows ANR).
        for _ in range(cgf._ANR_DISMISS_MAX_RETRIES):
            run_side_effects.append(self._make_run_result())  # KEYCODE_ENTER
            run_side_effects.append(anr_window)               # confirm dumpsys

        def tracking_run(args, **kwargs):
            if "KEYCODE_ENTER" in args:
                keyback_calls.append(list(args))
            return run_side_effects.pop(0)

        stderr_buf = io.StringIO()
        with patch("check_green_feed.subprocess.run", side_effect=tracking_run), \
             patch("check_green_feed.time.sleep"), \
             patch("sys.stderr", stderr_buf):
            # Must return, not raise.
            cgf._dismiss_anr_if_present("/fake/adb")

        # Exactly _ANR_DISMISS_MAX_RETRIES KEYCODE_ENTER calls.
        self.assertEqual(
            len(keyback_calls),
            cgf._ANR_DISMISS_MAX_RETRIES,
            f"Expected {cgf._ANR_DISMISS_MAX_RETRIES} KEYCODE_ENTER sends when all retries "
            f"are exhausted, got {len(keyback_calls)}"
        )

        # The exhaustion log line must be present.
        log_output = stderr_buf.getvalue()
        self.assertIn(
            "persisted after",
            log_output,
            f"Expected 'persisted after' in stderr log when all retries exhausted; got: {log_output!r}"
        )
        self.assertIn(
            str(cgf._ANR_DISMISS_MAX_RETRIES),
            log_output,
            f"Expected retry count {cgf._ANR_DISMISS_MAX_RETRIES} in stderr log; got: {log_output!r}"
        )


class TestDismissAnrUsesKeycodeEnter(unittest.TestCase):
    """Verify that _dismiss_anr_if_present sends KEYCODE_ENTER, not KEYCODE_BACK."""

    def _make_run_result(self, returncode=0, stderr="", stdout=""):
        result = MagicMock()
        result.returncode = returncode
        result.stderr = stderr
        result.stdout = stdout
        return result

    def test_keycode_enter_sent_on_anr_detection(self):
        """When an ANR dialog is detected, KEYCODE_ENTER is sent (not KEYCODE_BACK)."""
        anr_window = self._make_run_result(
            stdout="Application Not Responding: com.google.android.apps.nexuslauncher"
        )
        clear_window = self._make_run_result(stdout="WindowState idle")

        enter_calls: list[list] = []
        back_calls: list[list] = []
        run_side_effects = [
            anr_window,    # initial dumpsys window detect
            self._make_run_result(),  # KEYCODE_ENTER
            clear_window,  # confirm dumpsys window
        ]

        def tracking_run(args, **kwargs):
            if "KEYCODE_ENTER" in args:
                enter_calls.append(list(args))
            if "KEYCODE_BACK" in args:
                back_calls.append(list(args))
            return run_side_effects.pop(0)

        with patch("check_green_feed.subprocess.run", side_effect=tracking_run), \
             patch("check_green_feed.time.sleep"):
            cgf._dismiss_anr_if_present("/fake/adb")

        self.assertEqual(
            len(enter_calls), 1,
            f"Expected exactly 1 KEYCODE_ENTER send, got {len(enter_calls)}"
        )
        self.assertEqual(
            len(back_calls), 0,
            f"KEYCODE_BACK must never be sent; got {len(back_calls)} call(s)"
        )

    def test_keycode_back_never_sent(self):
        """KEYCODE_BACK is never sent even across multiple retries."""
        anr_window = self._make_run_result(
            stdout="Application Not Responding: com.google.android.apps.nexuslauncher"
        )
        clear_window = self._make_run_result(stdout="WindowState idle")

        back_calls: list[list] = []
        # Detect → one retry cycle → dismissed.
        run_side_effects = [
            anr_window,
            self._make_run_result(),  # KEYCODE_ENTER attempt 1
            clear_window,
        ]

        def tracking_run(args, **kwargs):
            if "KEYCODE_BACK" in args:
                back_calls.append(list(args))
            return run_side_effects.pop(0)

        with patch("check_green_feed.subprocess.run", side_effect=tracking_run), \
             patch("check_green_feed.time.sleep"):
            cgf._dismiss_anr_if_present("/fake/adb")

        self.assertEqual(
            len(back_calls), 0,
            f"KEYCODE_BACK must never be sent; got {len(back_calls)} call(s): {back_calls}"
        )


if __name__ == "__main__":
    unittest.main()
