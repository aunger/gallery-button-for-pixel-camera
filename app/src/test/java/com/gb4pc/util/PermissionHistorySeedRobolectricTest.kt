package com.gb4pc.util

import android.Manifest
import android.app.Activity
import android.app.Application
import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.gb4pc.data.PrefsManager
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf

/**
 * Tests for [PermissionHelper.seedPermissionRequestHistoryForUpgrade] (issue #572).
 *
 * The routing added by #572 reads an app-side record of the first ask, and that record is empty
 * for every install upgraded from a build that predates it. Left unseeded, the exact user the
 * issue describes, someone already stuck at a permanently denied permission, would be read as
 * "never asked" and routed to a dialog Android refuses to show: the same silent no-op, on the
 * first tap after upgrading. These tests pin the backfill that closes that window, and pin that
 * it stays out of the way of a fresh install.
 */
@RunWith(RobolectricTestRunner::class)
class PermissionHistorySeedRobolectricTest {
    private lateinit var context: Context
    private lateinit var prefsManager: PrefsManager

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
        prefsManager = PrefsManager(context)
        // GB4PCApplication.onCreate already ran the real backfill against Robolectric's default
        // package timestamps before this test body starts. Clear its traces so each test states
        // its own starting point.
        prefsManager.isPermissionHistorySeeded = false
        prefsManager.setRuntimePermissionRequested(PermissionHelper.mediaPermission, false)
        prefsManager.setRuntimePermissionRequested(Manifest.permission.POST_NOTIFICATIONS, false)
    }

    /** `firstInstallTime != lastUpdateTime` is the platform saying "this install was upgraded". */
    private fun simulateInstall(upgraded: Boolean) {
        val info = shadowOf(context.packageManager).getInternalMutablePackageInfo(context.packageName)
        info.firstInstallTime = 1_000L
        info.lastUpdateTime = if (upgraded) 2_000L else 1_000L
    }

    @Test
    fun `an upgraded install records the ungranted permissions as already asked`() {
        simulateInstall(upgraded = true)

        PermissionHelper.seedPermissionRequestHistoryForUpgrade(context, prefsManager)

        assertTrue(
            "An upgraded install has no recorded history, so an ungranted permission must be " +
                "assumed asked; otherwise a permanently denied one routes to a dialog that " +
                "cannot appear",
            prefsManager.hasRequestedRuntimePermission(PermissionHelper.mediaPermission),
        )
        assertTrue(prefsManager.hasRequestedRuntimePermission(Manifest.permission.POST_NOTIFICATIONS))
    }

    /**
     * The regression PR #934's review walked through: on an upgraded install, the first tap at a
     * permanently denied permission must already reach Settings, not spend itself on a dialog
     * Android will not show.
     */
    @Test
    fun `after seeding an upgraded install the first request already routes to settings`() {
        simulateInstall(upgraded = true)
        PermissionHelper.seedPermissionRequestHistoryForUpgrade(context, prefsManager)

        val activity = Robolectric.buildActivity(Activity::class.java).setup().get()
        shadowOf(activity.packageManager)
            .setShouldShowRequestPermissionRationale(PermissionHelper.mediaPermission, false)

        assertEquals(
            PermissionRequestRoute.SETTINGS,
            PermissionHelper.permissionRequestRoute(
                activity = activity,
                permission = PermissionHelper.mediaPermission,
                hasBeenRequestedBefore =
                    prefsManager.hasRequestedRuntimePermission(PermissionHelper.mediaPermission),
            ),
        )
    }

    /** A fresh install has an accurate (empty) history already; seeding it would only mislead. */
    @Test
    fun `a fresh install is left alone`() {
        simulateInstall(upgraded = false)

        PermissionHelper.seedPermissionRequestHistoryForUpgrade(context, prefsManager)

        assertFalse(
            "A fresh install must keep its accurate empty history, so the first ask still opens " +
                "the dialog rather than detouring through Settings",
            prefsManager.hasRequestedRuntimePermission(PermissionHelper.mediaPermission),
        )
        assertFalse(prefsManager.hasRequestedRuntimePermission(Manifest.permission.POST_NOTIFICATIONS))
    }

    /**
     * A granted permission has no route to choose, and if the user later revokes it from Settings
     * Android shows a dialog again, which an unseeded flag is what routes to.
     */
    @Test
    fun `a granted permission is not seeded`() {
        simulateInstall(upgraded = true)
        shadowOf(context as Application).grantPermissions(Manifest.permission.READ_MEDIA_IMAGES)

        PermissionHelper.seedPermissionRequestHistoryForUpgrade(context, prefsManager)

        assertFalse(prefsManager.hasRequestedRuntimePermission(PermissionHelper.mediaPermission))
    }

    /**
     * The backfill must not fire a second time: by then the flags are this build's own accurate
     * record, and re-seeding would overwrite a genuine "never asked" with "asked".
     */
    @Test
    fun `seeding runs only once`() {
        simulateInstall(upgraded = true)
        PermissionHelper.seedPermissionRequestHistoryForUpgrade(context, prefsManager)
        assertTrue(prefsManager.isPermissionHistorySeeded)

        prefsManager.setRuntimePermissionRequested(PermissionHelper.mediaPermission, false)
        PermissionHelper.seedPermissionRequestHistoryForUpgrade(context, prefsManager)

        assertFalse(
            "The second call must be a no-op, leaving this build's own record untouched",
            prefsManager.hasRequestedRuntimePermission(PermissionHelper.mediaPermission),
        )
    }

    /** A fresh install still marks itself seeded, so a later upgrade cannot re-trigger it. */
    @Test
    fun `a fresh install marks itself seeded so a later upgrade does not backfill`() {
        simulateInstall(upgraded = false)
        PermissionHelper.seedPermissionRequestHistoryForUpgrade(context, prefsManager)
        assertTrue(prefsManager.isPermissionHistorySeeded)

        simulateInstall(upgraded = true)
        PermissionHelper.seedPermissionRequestHistoryForUpgrade(context, prefsManager)

        assertFalse(
            "Once this build has been running, its own record is accurate and must not be " +
                "overwritten by a later update's backfill",
            prefsManager.hasRequestedRuntimePermission(PermissionHelper.mediaPermission),
        )
    }
}
