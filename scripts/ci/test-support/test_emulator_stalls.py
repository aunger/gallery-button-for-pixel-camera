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


class FindStallsTests(unittest.TestCase):
    def test_no_anr_means_no_stall(self):
        lines = [_runner("started", "a"), _runner("failed", "a"), _runner("finished", "a")]
        self.assertEqual(es.find_stalls(lines), {})

    def test_mock_camera_anr_alone_is_not_a_stall(self):
        # The guard from issue #1334: a GB4PC overlay window that took focus would
        # produce this same record, so it must not reclassify anything.
        lines = [_runner("started", "a"), MOCK_CAMERA_ANR, _runner("failed", "a")]
        self.assertEqual(es.find_stalls(lines), {})

    def test_covers_only_the_test_in_progress(self):
        lines = [
            _runner("started", "before"),
            _runner("finished", "before"),
            _runner("started", "during"),
            LAUNCHER_ANR,
            ANR_DETAIL,
            _runner("failed", "during"),
            _runner("finished", "during"),
            _runner("started", "after"),
            _runner("failed", "after"),
            _runner("finished", "after"),
        ]
        self.assertEqual(
            es.find_stalls(lines), {"com.gb4pc.ui.MainSettingsScreenTest#during": LAUNCHER_ANR}
        )

    def test_record_between_failed_and_finished_still_covers_that_test(self):
        lines = [_runner("started", "a"), _runner("failed", "a"), LAUNCHER_ANR]
        lines += [_runner("finished", "a"), _runner("started", "b")]
        self.assertEqual(set(es.find_stalls(lines)), {"com.gb4pc.ui.MainSettingsScreenTest#a"})

    def test_record_while_no_test_runs_covers_only_the_next_test(self):
        # An install between test steps (the #194 trigger) writes its ANR record
        # while no test runs; its dialog can only land on the next one.
        lines = [
            _runner("started", "a"),
            _runner("finished", "a"),
            LAUNCHER_ANR,
            _runner("started", "b"),
            _runner("failed", "b"),
            _runner("finished", "b"),
            _runner("started", "c"),
            _runner("failed", "c"),
        ]
        self.assertEqual(set(es.find_stalls(lines)), {"com.gb4pc.ui.MainSettingsScreenTest#b"})

    def test_record_after_a_test_that_died_covers_the_next_test(self):
        # Review of PR #1337: a test whose process dies writes no `finished:` line.
        # A record written after it (the next step's install, the #194 trigger)
        # belongs to the next test, and the dead test keeps its ticket.
        lines = [
            _runner("started", "crashes"),
            LAUNCHER_ANR,
            "10-17 13:46:20.000  5799  5815 I TestRunner: run started: 3 tests",
            _runner("started", "next"),
            _runner("finished", "next"),
        ]
        self.assertEqual(
            es.find_stalls(lines), {"com.gb4pc.ui.MainSettingsScreenTest#next": LAUNCHER_ANR}
        )

    def test_record_held_for_a_test_that_writes_nothing_more_covers_nothing(self):
        lines = [_runner("started", "a"), LAUNCHER_ANR]
        self.assertEqual(es.find_stalls(lines), {})

    def test_record_after_the_last_test_covers_nothing(self):
        lines = [_runner("started", "a"), _runner("failed", "a"), _runner("finished", "a")]
        self.assertEqual(es.find_stalls([*lines, LAUNCHER_ANR]), {})

    def test_each_qualifying_record_covers_its_own_test(self):
        second = LAUNCHER_ANR.replace("13:46:05.120", "13:48:00.000")
        lines = [
            _runner("started", "a"),
            LAUNCHER_ANR,
            _runner("finished", "a"),
            _runner("started", "b"),
            _runner("finished", "b"),
            _runner("started", "c"),
            second,
            _runner("finished", "c"),
        ]
        self.assertEqual(
            es.find_stalls(lines),
            {
                "com.gb4pc.ui.MainSettingsScreenTest#a": LAUNCHER_ANR,
                "com.gb4pc.ui.MainSettingsScreenTest#c": second,
            },
        )

    def test_run_summary_lines_are_not_tests(self):
        lines = [
            LAUNCHER_ANR,
            "10-17 13:46:10.000  4982  4998 I TestRunner: run started: 4 tests",
            "10-17 13:46:50.000  4982  4998 I TestRunner: run finished: 4 tests, 1 failed, 0 ignored",
        ]
        self.assertEqual(es.find_stalls(lines), {})

    def test_parameterized_method_name_is_kept_whole(self):
        lines = [LAUNCHER_ANR, _runner("started", "check[1: dark](x)", cls="com.example.FooTest")]
        self.assertEqual(set(es.find_stalls(lines)), {"com.example.FooTest#check[1: dark](x)"})


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

    def _with_launcher_anr_at(self, needle: str, timestamp: str) -> list[str]:
        lines = self._lines("run-39037527808-stall.txt")
        at = next(i for i, line in enumerate(lines) if needle in line)
        lines.insert(at, LAUNCHER_ANR.replace("13:46:05.120", timestamp))
        return lines

    def test_stall_fixture_holds_every_failure_of_the_stall_run(self):
        failed = {
            f"{m['cls']}#{m['method']}"
            for line in self._lines("run-39037527808-stall.txt")
            if "TestRunner: failed:" in line and (m := es._TEST.search(line))
        }
        self.assertEqual(failed, STALL_RUN_FAILURES)

    def test_test5a_passed_after_the_launcher_dialog_showed_in_test4a(self):
        # The evidence for a one-test window: test5a checks the green coverage the
        # dialog's dim breaks, starts right after test4a, and has no `failed:` line.
        lines = self._lines("run-39037527808-stall.txt")
        test5a = [line for line in lines if "test5a_secureCameraLockedPopulatedGallery" in line]
        self.assertEqual(
            [es._TEST.search(line)["event"] for line in test5a], ["started", "finished"]
        )

    def test_stall_run_as_kept_holds_no_qualifying_anr(self):
        # Seven ANR records, every one in the mock camera or the mock gallery: the
        # guard leaves all six failures to be filed as test failures.
        lines = self._lines("run-39037527808-stall.txt")
        self.assertEqual(sum("ActivityManager: ANR in" in line for line in lines), 7)
        self.assertEqual(es.find_stalls(lines), {})

    def test_passing_run_holds_no_qualifying_anr(self):
        self.assertEqual(es.find_stalls(self._lines("run-39273048305-passing.txt")), {})

    def test_launcher_anr_during_test4a_covers_test4a_and_not_test5a(self):
        # Where the launcher record fell is not in the kept logs; test4a's
        # screenshots show its dialog, so place it in test4a.
        lines = self._with_launcher_anr_at("failed: test4a_secureCamera", "13:50:30.000")
        self.assertEqual(
            set(es.find_stalls(lines)), {f"{GALLERY}#test4a_secureCameraLockedEmptyGalleryNoGreen"}
        )

    def test_launcher_anr_before_the_first_test_covers_only_the_first_test(self):
        lines = self._with_launcher_anr_at("started: ", "13:45:40.000")
        self.assertEqual(
            set(es.find_stalls(lines)),
            {"com.gb4pc.ui.MainActivityRedirectTest#whenSetupNotCompleted_launchesSetupActivity"},
        )


class ReadStallsTests(unittest.TestCase):
    def test_missing_recording_is_no_stall(self):
        self.assertEqual(es.read_stalls(Path("/no/such/recording.txt")), {})

    def test_reads_a_recording_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "stall-logcat.txt"
            path.write_text(
                "\n".join(["--------- beginning of system", LAUNCHER_ANR, _runner("started", "a")])
                + "\n",
                encoding="utf-8",
            )
            stalls = es.read_stalls(path)
        self.assertEqual(stalls, {"com.gb4pc.ui.MainSettingsScreenTest#a": LAUNCHER_ANR})


if __name__ == "__main__":
    unittest.main()
