package com.gb4pc.e2e

import android.Manifest
import android.app.NotificationManager
import android.content.Context
import android.os.Build
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.gb4pc.Constants
import com.gb4pc.util.DebugLog
import com.gb4pc.util.PermissionHelper
import org.junit.After
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * E2E regression coverage for issue #509 (root cause H1): the app never requested the runtime
 * media-read permission, so `queryLatestMedia` silently returned only rows owned by `com.gb4pc`
 * itself (scoped storage, API 29+), never Pixel Camera's, and the overlay thumbnail could never
 * update in a real install.
 *
 * PR #564 fixed the *declaration and reaction* to that permission (setup step, PermissionHelper
 * check, main-screen banner, service-level gate). Until this file, none of that was exercised by
 * an automated test against a real permission grant state: `connectedE2EAndroidTest` grants
 * `READ_MEDIA_IMAGES` for the whole `com.gb4pc.e2e` package up front (see the `pm grant` call in
 * `app/build.gradle.kts`), which is exactly the blind spot that let #509 regress silently through
 * CI (a manifest declaration was already present; only the runtime grant, and thus the
 * permission-denied code paths, went untested).
 *
 * Design note (see PR #564 review discussion): rather than reordering the CI script to run this
 * class before the task-level `pm grant`, each test here manages its own precondition directly
 * via `pm grant` / `pm revoke` (the same OS-level mechanism the system permission dialog uses
 * when the user taps Allow / Deny). This makes every test correct regardless of what state the
 * surrounding task left the permission in, so it does not depend on fragile cross-file CI
 * ordering, and every test restores full access in `@After` so later tests in the same
 * `am instrument` run (which still assume the task-level pre-grant) are unaffected.
 *
 * Tracks: #566 (setup step grants and is reflected), #567 (thumbnail updates after a real
 * photo), #568 (missing/denied permission surfaces banner + notification).
 */
@E2ETest
@RunWith(AndroidJUnit4::class)
class PermissionsE2ETest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context: Context = instrumentation.targetContext
    private val fixture =
        E2EFixture(
            context = context,
            uiAutomation = instrumentation.uiAutomation,
        )

    private val mediaPermission: String
        get() =
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                Manifest.permission.READ_MEDIA_IMAGES
            } else {
                Manifest.permission.READ_EXTERNAL_STORAGE
            }

    @Before
    fun setUp() {
        fixture.setUp()
    }

    @After
    fun restoreMediaPermission() {
        // Every other E2E test class assumes the task-level pre-grant (see class doc); leave the
        // device in that state no matter which test above ran or how it ended.
        grantMediaPermission()
    }

    private fun grantMediaPermission() {
        instrumentation.uiAutomation
            .executeShellCommand("pm grant ${context.packageName} $mediaPermission")
            .close()
    }

    private fun revokeMediaPermission() {
        instrumentation.uiAutomation
            .executeShellCommand("pm revoke ${context.packageName} $mediaPermission")
            .close()
    }

    /**
     * Confirms [PermissionHelper.hasMediaPermission] reflects the real OS grant state, both ways.
     * `pm grant` / `pm revoke` flip the same PackageManager-level grant that tapping Allow / Deny
     * (or "Allow all" vs "Don't allow" on the API 34+ photo-picker dialog) on the real system
     * permission prompt would, so this is a faithful proxy for the setup step's grant flow (#566)
     * without depending on tapping through that dialog's UI, whose resource IDs vary by API level
     * and vendor.
     */
    @Test
    fun hasMediaPermissionReflectsRealOsGrantState() {
        revokeMediaPermission()
        assertFalse(
            "hasMediaPermission should be false immediately after pm revoke",
            PermissionHelper.hasMediaPermission(context),
        )

        grantMediaPermission()
        assertTrue(
            "hasMediaPermission should be true immediately after pm grant",
            PermissionHelper.hasMediaPermission(context),
        )
    }

    /**
     * Regression guard for #509/#568: without the media permission, `registerThumbnailObserver`
     * must not poll MediaStore at all (every query would silently return only this app's own
     * rows), and must instead log the reason and post a tap-to-fix notification once.
     */
    @Test
    fun overlaySkipsThumbnailPollingAndNotifiesWhenMediaPermissionMissing() {
        revokeMediaPermission()
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

    /**
     * The core regression test for issue #509: with the permission genuinely granted (via the
     * same OS mechanism the setup step's system dialog uses), taking a real photo through the
     * mock camera must cause the overlay thumbnail to update. Before PR #564 this was entirely
     * unverified by automation; CI's blanket pre-grant only masked whether the app *itself* ever
     * requested the permission, not whether the thumbnail path worked once it had it.
     */
    @Test
    fun overlayUpdatesThumbnailAfterRealPhotoOnceMediaPermissionGranted() {
        grantMediaPermission()
        DebugLog.clear()

        fixture.launchPixelCamera()
        fixture.captureOnePhoto()

        val updated =
            fixture.waitForCondition(10_000L) {
                DebugLog.getEntries().any { it.message.startsWith("Thumbnail updated") }
            }
        assertTrue(
            "Overlay thumbnail should update to the newly captured photo within 10 s (issue #509)",
            updated,
        )
    }
}
