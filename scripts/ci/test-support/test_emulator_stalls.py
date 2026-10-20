#!/usr/bin/env python3
"""Unit tests for emulator_stalls.py."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import emulator_stalls as es

# Lines in the shape the CI recorder writes (`adb logcat -v threadtime`). The
# mock-camera ANR is copied from run 39037527808; the launcher ANR has the same
# shape with the Pixel Launcher's process name.
MOCK_CAMERA_ANR = (
    "10-17 13:47:41.900   558  5485 E ActivityManager: ANR in com.google.android.GoogleCamera "
    "(com.google.android.GoogleCamera/com.gb4pc.mockcamera.MockCameraActivity)"
)
LAUNCHER_ANR = (
    "10-17 13:46:05.120   558  5100 E ActivityManager: ANR in com.google.android.apps.nexuslauncher"
)
ANR_DETAIL = "10-17 13:46:05.120   558  5100 E ActivityManager: PID: 1234"


def _runner(event: str, method: str, cls: str = "com.gb4pc.ui.MainSettingsScreenTest") -> str:
    level = "E" if event == "failed" else "I"
    return f"10-17 13:46:00.000  4982  4998 {level} TestRunner: {event}: {method}({cls})"


class IsStallProcessTests(unittest.TestCase):
    def test_system_and_launcher_processes_qualify(self):
        for process in (
            "system",
            "com.android.systemui",
            "com.google.android.apps.nexuslauncher",
            "com.android.launcher3",
        ):
            with self.subTest(process=process):
                self.assertTrue(es.is_stall_process(process))

    def test_secondary_process_of_a_qualifying_package_qualifies(self):
        self.assertTrue(es.is_stall_process("com.android.systemui:screenshot"))

    def test_own_packages_do_not_qualify(self):
        for process in (
            "com.gb4pc",
            "com.gb4pc.test",
            "com.gb4pc.mockgallery",
            "com.google.android.GoogleCamera",
        ):
            with self.subTest(process=process):
                self.assertFalse(es.is_stall_process(process))

    def test_other_system_apps_do_not_qualify(self):
        self.assertFalse(es.is_stall_process("com.google.android.gms.persistent"))

    def test_name_sharing_a_prefix_does_not_qualify(self):
        self.assertFalse(es.is_stall_process("com.android.systemuix"))


class FindStallTests(unittest.TestCase):
    def test_no_anr_means_no_stall(self):
        lines = [_runner("started", "a"), _runner("failed", "a"), _runner("finished", "a")]
        self.assertIsNone(es.find_stall(lines))

    def test_mock_camera_anr_alone_is_not_a_stall(self):
        # The guard from issue #1334: a GB4PC overlay window that took focus would
        # produce this same record, so it must not reclassify anything.
        lines = [MOCK_CAMERA_ANR, _runner("started", "a"), _runner("failed", "a")]
        self.assertIsNone(es.find_stall(lines))

    def test_names_tests_running_after_a_launcher_anr(self):
        lines = [
            _runner("started", "before"),
            _runner("finished", "before"),
            _runner("started", "during"),
            LAUNCHER_ANR,
            ANR_DETAIL,
            _runner("failed", "during"),
            _runner("finished", "during"),
            _runner("started", "after", cls="com.gb4pc.e2e.GalleryButtonVisualE2ETest"),
            _runner("finished", "after", cls="com.gb4pc.e2e.GalleryButtonVisualE2ETest"),
        ]
        evidence = es.find_stall(lines)
        self.assertIsNotNone(evidence)
        self.assertEqual(evidence.anr_record, LAUNCHER_ANR)
        self.assertEqual(
            evidence.stalled_tests,
            {
                "com.gb4pc.ui.MainSettingsScreenTest#during",
                "com.gb4pc.e2e.GalleryButtonVisualE2ETest#after",
            },
        )

    def test_test_that_ended_before_the_anr_is_not_named(self):
        lines = [_runner("started", "a"), _runner("failed", "a"), _runner("finished", "a")]
        evidence = es.find_stall([*lines, LAUNCHER_ANR])
        self.assertIsNotNone(evidence)
        self.assertEqual(evidence.stalled_tests, frozenset())

    def test_first_qualifying_record_is_kept_after_a_non_qualifying_one(self):
        evidence = es.find_stall(
            [MOCK_CAMERA_ANR, _runner("started", "a"), LAUNCHER_ANR, _runner("finished", "a")]
        )
        self.assertEqual(evidence.anr_record, LAUNCHER_ANR)
        self.assertEqual(evidence.stalled_tests, {"com.gb4pc.ui.MainSettingsScreenTest#a"})

    def test_run_summary_lines_are_not_tests(self):
        lines = [
            LAUNCHER_ANR,
            "10-17 13:46:10.000  4982  4998 I TestRunner: run started: 4 tests",
            "10-17 13:46:50.000  4982  4998 I TestRunner: run finished: 4 tests, 1 failed, 0 ignored",
        ]
        self.assertEqual(es.find_stall(lines).stalled_tests, frozenset())

    def test_parameterized_method_name_is_kept_whole(self):
        lines = [LAUNCHER_ANR, _runner("failed", "check[1: dark](x)", cls="com.example.FooTest")]
        self.assertEqual(
            es.find_stall(lines).stalled_tests,
            {"com.example.FooTest#check[1: dark](x)"},
        )


FIXTURES = Path(__file__).parent / "fixtures" / "emulator_stalls"
GALLERY = "com.gb4pc.e2e.GalleryButtonVisualE2ETest"
SETTINGS = "com.gb4pc.ui.MainSettingsScreenTest"
STALL_RUN_FAILURES = {
    f"{SETTINGS}#mainScreen_tappingMediaMissingBanner_whenPermanentlyDenied_opensAppDetailsSettings",
    f"{SETTINGS}#mainScreen_tappingNotificationBanner_whenPermanentlyDenied_opensAppDetailsSettings",
    f"{GALLERY}#test0_smokeGreenFeedVisible",
    f"{GALLERY}#test2a_emptyGalleryNoGreenAfterTap",
    f"{GALLERY}#test3a_populatedGalleryShowsGreenAfterTap",
    f"{GALLERY}#test4a_secureCameraLockedEmptyGalleryNoGreen",
}


class RecordedRunTests(unittest.TestCase):
    """Real excerpts from CI runs, saved before their artifacts expired (issue #1334)."""

    def _lines(self, name: str) -> list[str]:
        return (FIXTURES / name).read_text(encoding="utf-8").splitlines()

    def test_stall_fixture_holds_every_failure_of_the_stall_run(self):
        failed = {
            f"{m['cls']}#{m['method']}"
            for line in self._lines("run-39037527808-stall.txt")
            if "TestRunner: failed:" in line and (m := es._TEST.search(line))
        }
        self.assertEqual(failed, STALL_RUN_FAILURES)

    def test_stall_run_as_kept_holds_no_qualifying_anr(self):
        # Seven ANR records, every one in the mock camera or the mock gallery: the
        # guard leaves all six failures to be filed as test failures.
        lines = self._lines("run-39037527808-stall.txt")
        self.assertEqual(sum("ActivityManager: ANR in" in line for line in lines), 7)
        self.assertIsNone(es.find_stall(lines))

    def test_passing_run_holds_no_qualifying_anr(self):
        self.assertIsNone(es.find_stall(self._lines("run-39273048305-passing.txt")))

    def test_launcher_anr_before_test4a_covers_only_test4a_of_the_failures(self):
        # The launcher dialog first shows in test4a's screenshots, so its ANR record
        # was written before then; where it fell is not in the kept logs. Placed
        # after test3a ended, a recording would cover test4a alone of the six
        # failures, because the other five ended earlier.
        lines = self._lines("run-39037527808-stall.txt")
        at = next(
            i
            for i, line in enumerate(lines)
            if "started: test4a_secureCameraLockedEmptyGalleryNoGreen" in line
        )
        lines.insert(at, LAUNCHER_ANR.replace("13:46:05.120", "13:50:16.255"))
        evidence = es.find_stall(lines)
        self.assertEqual(
            evidence.stalled_tests & STALL_RUN_FAILURES,
            {f"{GALLERY}#test4a_secureCameraLockedEmptyGalleryNoGreen"},
        )


class ReadStallEvidenceTests(unittest.TestCase):
    def test_missing_recording_is_no_stall(self):
        self.assertIsNone(es.read_stall_evidence(Path("/no/such/recording.txt")))

    def test_reads_a_recording_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "stall-logcat.txt"
            path.write_text(
                "\n".join(["--------- beginning of system", LAUNCHER_ANR, _runner("failed", "a")])
                + "\n",
                encoding="utf-8",
            )
            evidence = es.read_stall_evidence(path)
        self.assertEqual(evidence.stalled_tests, {"com.gb4pc.ui.MainSettingsScreenTest#a"})


if __name__ == "__main__":
    unittest.main()
