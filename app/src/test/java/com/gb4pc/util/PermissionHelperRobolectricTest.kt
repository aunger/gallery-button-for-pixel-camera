package com.gb4pc.util

import android.Manifest
import android.app.Application
import android.content.Context
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf

/**
 * Robolectric-backed tests for [PermissionHelper.hasMediaPermission]'s API 34+ partial-access
 * (H2) handling.
 *
 * [PermissionHelperTest] (plain Mockito unit tests) cannot exercise this: `Build.VERSION.SDK_INT`
 * is always 0 there (no real Android framework backing the SDK stub jar), so `hasMediaPermission`
 * always takes the pre-API-33 `READ_EXTERNAL_STORAGE` branch (see that file's own comment).
 * Testing the `READ_MEDIA_IMAGES` vs. partial-access distinction needs a real (simulated) SDK
 * level and a real permission-check implementation behind `context.checkSelfPermission`, which
 * Robolectric provides. This mirrors the existing split in this codebase between plain-mock unit
 * tests and Robolectric-backed ones (see `OverlayManagerRobolectricTest` alongside
 * `OverlayManagerTest`).
 *
 * No `@Config(sdk = ...)` override: this runs at the project's default simulated SDK (targetSdk
 * 35, matching `OverlayManagerRobolectricTest`'s convention), which is comfortably above the
 * API 34 floor `READ_MEDIA_VISUAL_USER_SELECTED` requires.
 */
@RunWith(RobolectricTestRunner::class)
class PermissionHelperRobolectricTest {
    /**
     * Regression guard for H2 (issue #509 root-cause analysis): on API 34+, a user can choose
     * "Select photos" instead of "Allow all", which grants only `READ_MEDIA_VISUAL_USER_SELECTED`.
     * That subset can never include a photo taken seconds ago, so `hasMediaPermission` must keep
     * treating this state as NOT granted (it requires `READ_MEDIA_IMAGES` specifically), so the
     * setup step and main-screen banner keep prompting until the user allows all photos.
     */
    @Test
    fun `hasMediaPermission returns false when only partial access is granted`() {
        val context: Context = ApplicationProvider.getApplicationContext()
        shadowOf(context as Application)
            .grantPermissions(Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED)
        // READ_MEDIA_IMAGES is deliberately left ungranted here.

        assertFalse(
            "Partial access (READ_MEDIA_VISUAL_USER_SELECTED only) must not satisfy " +
                "hasMediaPermission; only full access (READ_MEDIA_IMAGES) should",
            PermissionHelper.hasMediaPermission(context),
        )
    }

    /** Sanity check: full access on this same simulated API level does satisfy the check. */
    @Test
    fun `hasMediaPermission returns true when full access is granted`() {
        val context: Context = ApplicationProvider.getApplicationContext()
        shadowOf(context as Application)
            .grantPermissions(Manifest.permission.READ_MEDIA_IMAGES)

        assertTrue(
            "Full access (READ_MEDIA_IMAGES) should satisfy hasMediaPermission",
            PermissionHelper.hasMediaPermission(context),
        )
    }
}
