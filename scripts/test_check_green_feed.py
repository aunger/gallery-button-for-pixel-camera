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

    def test_succeeds_on_first_attempt(self):
        """When the image passes on the first try, main exits 0."""
        green_png = _write_png(cgf.TARGET_R, cgf.TARGET_G, cgf.TARGET_B)
        try:
            with patch("check_green_feed.subprocess.run") as mock_run, \
                 patch("check_green_feed.time.sleep") as mock_sleep, \
                 patch("builtins.open", create=True) as mock_open:
                # subprocess.run writes nothing; check_image reads the pre-existing file
                mock_open.return_value.__enter__ = lambda s: s
                mock_open.return_value.__exit__ = MagicMock(return_value=False)
                with patch("check_green_feed.check_image", return_value=0) as mock_check:
                    result = self._run_main(["--adb", "/fake/adb", green_png])
            self.assertEqual(result, 0)
            mock_sleep.assert_called_once_with(cgf.RETRY_DELAY_SECONDS)
            mock_check.assert_called_once_with(green_png)
        finally:
            os.unlink(green_png)

    def test_retries_until_success(self):
        """main retries when check_image returns 1 and succeeds on a later attempt."""
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            side_effects = [1, 1, 0]  # fail, fail, pass
            with patch("check_green_feed.subprocess.run"), \
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
            with patch("check_green_feed.subprocess.run"), \
                 patch("check_green_feed.time.sleep"), \
                 patch("builtins.open", unittest.mock.mock_open()), \
                 patch("check_green_feed.check_image", return_value=1):
                result = self._run_main(["--adb", "/fake/adb", path])
            self.assertEqual(result, 1)
        finally:
            os.unlink(path)

    def test_sleep_called_on_every_attempt(self):
        """Sleep is called MAX_ATTEMPTS times (once per attempt, at the top)."""
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            with patch("check_green_feed.subprocess.run"), \
                 patch("check_green_feed.time.sleep") as mock_sleep, \
                 patch("builtins.open", unittest.mock.mock_open()), \
                 patch("check_green_feed.check_image", return_value=1):
                self._run_main(["--adb", "/fake/adb", path])
            self.assertEqual(mock_sleep.call_count, cgf.MAX_ATTEMPTS)
            mock_sleep.assert_called_with(cgf.RETRY_DELAY_SECONDS)
        finally:
            os.unlink(path)

    def test_wakeup_and_menu_keyevent_sent_before_each_screencap(self):
        """KEYCODE_WAKEUP (224) and KEYCODE_MENU (82) are sent before every screencap,
        in that order, with the screencap coming after both in each retry iteration."""
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            # Run two iterations (fail once, then pass) so we can check ordering
            # across multiple retry cycles.
            side_effects = [1, 0]
            with patch("check_green_feed.subprocess.run") as mock_run, \
                 patch("check_green_feed.time.sleep"), \
                 patch("builtins.open", unittest.mock.mock_open()), \
                 patch("check_green_feed.check_image", side_effect=side_effects):
                self._run_main(["--adb", "/fake/adb", path])

            # Build a flat list of the command argument lists from each call.
            arg_lists = [c.args[0] for c in mock_run.call_args_list if c.args]

            def is_wakeup(args):
                return "keyevent" in args and "224" in args

            def is_menu(args):
                return "keyevent" in args and "82" in args

            def is_screencap(args):
                return "screencap" in args

            # Walk the call list and verify that each screencap is immediately
            # preceded by a KEYCODE_MENU (82) call, which is itself preceded by
            # a KEYCODE_WAKEUP (224) call.  This confirms the ordering within
            # every retry iteration, not just the presence of the calls somewhere
            # in the full list.
            screencap_indices = [i for i, a in enumerate(arg_lists) if is_screencap(a)]
            self.assertGreater(len(screencap_indices), 0, "No screencap call found")

            for sc_idx in screencap_indices:
                self.assertGreaterEqual(sc_idx, 2,
                    "Not enough preceding calls before screencap to fit wakeup + menu")
                menu_idx = sc_idx - 1
                wakeup_idx = sc_idx - 2
                self.assertTrue(
                    is_menu(arg_lists[menu_idx]),
                    f"Call immediately before screencap (index {menu_idx}) is not "
                    f"KEYCODE_MENU (82): {arg_lists[menu_idx]}"
                )
                self.assertTrue(
                    is_wakeup(arg_lists[wakeup_idx]),
                    f"Call two before screencap (index {wakeup_idx}) is not "
                    f"KEYCODE_WAKEUP (224): {arg_lists[wakeup_idx]}"
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


if __name__ == "__main__":
    unittest.main()
