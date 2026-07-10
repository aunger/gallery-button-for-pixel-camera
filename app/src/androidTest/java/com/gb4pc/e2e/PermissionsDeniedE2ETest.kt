package com.gb4pc.e2e

import android.app.NotificationManager
import android.content.Context
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.gb4pc.Constants
import com.gb4pc.util.DebugLog
import com.gb4pc.util.PermissionHelper
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * E2E regression coverage for issue #509 (root cause H1), denied-permission half.
 *
 * PR #564 fixed the *declaration and reaction* to the runtime media-read permission (setup step,
 * PermissionHelper check, main-screen banner, service-level gate). This class exercises the
 * denied-permission reaction against a real permission grant state, closing #568.
 *
 * ### Why this class exists separately from [PermissionsGrantedE2ETest], and why neither toggles
 * ### the permission from inside a running test
 *
 * An earlier version of this coverage (PR #564, commit 3eb3f84) had a single self-contained test
 * class that called `pm revoke` / `pm grant` on `com.gb4pc` from *within* a running test, on the
 * theory that this is the same PackageManager-level grant tapping Allow/Deny on the real system
 * dialog produces. That is true, but changing a storage-group runtime permission (`READ_MEDIA_IMAGES`
 * / `READ_EXTERNAL_STORAGE`) for a process that is *already running* triggers Android's scoped-storage
 * subsystem to kill that process, so its FUSE-based per-UID storage mount can be re-established for
 * the new grant state. `am instrument` runs the instrumented test code inside the target app's own
 * process (`com.gb4pc`, not `com.gb4pc.test`'s), so that kill takes down the very process the test
 * harness depends on. Review on PR #564 confirmed this from the CI artifact: the added
 * `SetupActivityTest` case that called `pm grant` on the live process reported "Process crashed" and
 * aborted the rest of that `connectedDebugAndroidTest` shard (only 4 of 21 scheduled tests ran).
 *
 * The fix is to never change this permission group on an already-running `com.gb4pc` process.
 * Instead, each precondition (granted vs. denied) is fixed *before* the process starts, exactly
 * like every other E2E suite's permissions already are: `app/build.gradle.kts`'s
 * `connectedE2EAndroidTest` task grants `READ_MEDIA_IMAGES` in `doLast`, before `am instrument`
 * launches the process, and now accepts a `-PmediaPermissionGranted=false` override so a dedicated
 * CI step (`-Pe2eClass=com.gb4pc.e2e.PermissionsDeniedE2ETest -PmediaPermissionGranted=false`) can
 * launch this class with the permission revoked from the start instead. See `.github/workflows/build.yml`.
 *
 * Tracks: #568 (missing/denied permission surfaces banner + notification). See
 * [PermissionsGrantedE2ETest] for #567.
 *
 * ### A second, unrelated CI precondition this class exposed
 *
 * The `overlaySkipsThumbnailPollingAndNotifiesWhenMediaPermissionMissing` notification assertion
 * initially failed in CI even though `OverlayService.postPermissionNotification()` is correct: on
 * API 33+, `NotificationManager.notify()` is a silent no-op (no exception) when `POST_NOTIFICATIONS`
 * has not been granted, and nothing in `connectedE2EAndroidTest` granted it (no prior E2E test ever
 * asserted on notification state, so the gap was invisible until this one). Fixed by granting
 * `POST_NOTIFICATIONS` alongside the other pre-launch grants in `app/build.gradle.kts`'s
 * `connectedE2EAndroidTest` `doLast` block, before the process starts.
 */
@E2ETest
@RunWith(AndroidJUnit4::class)
class PermissionsDeniedE2ETest {
    @get:Rule
    val testNameToastRule = TestNameToastRule()

    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context: Context = instrumentation.targetContext
    private val fixture =
        E2EFixture(
            context = context,
            uiAutomation = instrumentation.uiAutomation,
        )

    @Before
    fun setUp() {
        fixture.setUp()
    }

    /** Sanity check that this class's assumed precondition actually holds. */
    @Test
    fun hasMediaPermissionIsFalseWhenNotGranted() {
        assertFalse(
            "This class assumes READ_MEDIA_IMAGES is NOT granted before the process starts " +
                "(via -PmediaPermissionGranted=false); it was granted",
            PermissionHelper.hasMediaPermission(context),
        )
    }

    /**
     * Regression guard for #509/#568: without the media permission, `registerThumbnailObserver`
     * must not poll MediaStore at all (every query would silently return only this app's own
     * rows), and must instead log the reason and post a tap-to-fix notification once.
     *
     * Requires `POST_NOTIFICATIONS` granted (see the class doc); `connectedE2EAndroidTest` grants
     * it unconditionally, independent of `-PmediaPermissionGranted`.
     */
    @Test
    fun overlaySkipsThumbnailPollingAndNotifiesWhenMediaPermissionMissing() {
        DebugLog.clear()

        fixture.launchPixelCamera()

        val logged =
            fixture.waitForCondition(10_000L) {
                DebugLog.getEntries().any { it.message.contains("Media read permission not granted") }
            }
        assertTrue(
            "OverlayService should log that the media permission is missing instead of " +
                "silently polling MediaStore for the thumbnail",
            logged,
        )

        val notificationManager = context.getSystemService(NotificationManager::class.java)
        val notified =
            fixture.waitForCondition(5_000L) {
                notificationManager.activeNotifications.any {
                    it.id == Constants.NOTIFICATION_MEDIA_PERMISSION_ID
                }
            }
        assertTrue(
            "A tap-to-fix notification should be posted when the media permission is missing",
            notified,
        )
    }
}
