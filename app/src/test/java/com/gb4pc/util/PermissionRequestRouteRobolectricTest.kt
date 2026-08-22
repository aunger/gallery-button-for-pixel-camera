package com.gb4pc.util

import android.Manifest
import android.app.Activity
import android.provider.Settings
import com.gb4pc.data.PrefsManager
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf

/**
 * Tests for [PermissionHelper.permissionRequestRoute] and
 * [PermissionHelper.requestRuntimePermission] (issue #572): choosing the system dialog or the
 * app's Settings page from the *denial history* of the permission, instead of hardcoding one
 * route per screen.
 *
 * Robolectric rather than plain Mockito, for two reasons the existing
 * [PermissionHelperRobolectricTest] shares: `shouldShowRequestPermissionRationale()` is a real
 * `Activity`/`PackageManager` interaction that needs a (simulated) framework behind it
 * (`ShadowPackageManager.setShouldShowRequestPermissionRationale` drives it), and the "have we
 * ever asked" flag is a real `SharedPreferences` round-trip through [PrefsManager] rather than a
 * mock's canned answer, so these tests also cover the persistence the routing depends on.
 */
@RunWith(RobolectricTestRunner::class)
class PermissionRequestRouteRobolectricTest {
    private val permission = Manifest.permission.READ_MEDIA_IMAGES

    private lateinit var activity: Activity
    private lateinit var prefsManager: PrefsManager

    @Before
    fun setUp() {
        activity = Robolectric.buildActivity(Activity::class.java).setup().get()
        prefsManager = PrefsManager(activity)
    }

    private fun setRationale(shouldShow: Boolean) =
        shadowOf(activity.packageManager).setShouldShowRequestPermissionRationale(permission, shouldShow)

    private fun route() =
        PermissionHelper.permissionRequestRoute(
            activity = activity,
            permission = permission,
            hasBeenRequestedBefore = prefsManager.hasRequestedRuntimePermission(permission),
        )

    /**
     * The first-ever ask. `shouldShowRequestPermissionRationale()` is `false` here, exactly as it
     * is after a permanent denial, so routing on that signal alone would send a brand new user to
     * Settings for a grant the dialog can collect in one tap. The recorded ask is what tells the
     * two apart.
     */
    @Test
    fun `never asked routes to the dialog even though rationale is false`() {
        setRationale(false)
        assertFalse(prefsManager.hasRequestedRuntimePermission(permission))

        assertEquals(PermissionRequestRoute.DIALOG, route())
    }

    /** Denied once, but Android will still show the dialog: keep using it. */
    @Test
    fun `asked before with rationale routes to the dialog`() {
        prefsManager.setRuntimePermissionRequested(permission, true)
        setRationale(true)

        assertEquals(PermissionRequestRoute.DIALOG, route())
    }

    /**
     * The case the hardcoded-dialog screens got wrong: `requestPermissions()` would return DENIED
     * synchronously without showing anything, so the button silently did nothing.
     */
    @Test
    fun `asked before without rationale routes to settings`() {
        prefsManager.setRuntimePermissionRequested(permission, true)
        setRationale(false)

        assertEquals(PermissionRequestRoute.SETTINGS, route())
    }

    @Test
    fun `requestRuntimePermission launches the dialog and records the ask`() {
        setRationale(false)
        var launched = false

        PermissionHelper.requestRuntimePermission(
            activity = activity,
            permission = permission,
            prefsManager = prefsManager,
        ) { launched = true }

        assertTrue("The dialog route must invoke the caller's launcher", launched)
        assertTrue(
            "The ask must be recorded, so a later permanent denial is distinguishable from never asking",
            prefsManager.hasRequestedRuntimePermission(permission),
        )
        assertNull("The dialog route must not start any activity", shadowOf(activity).nextStartedActivity)
    }

    @Test
    fun `requestRuntimePermission opens the app details page when permanently denied`() {
        prefsManager.setRuntimePermissionRequested(permission, true)
        setRationale(false)
        var launched = false

        PermissionHelper.requestRuntimePermission(
            activity = activity,
            permission = permission,
            prefsManager = prefsManager,
        ) { launched = true }

        assertFalse("A permanently denied permission must not fire a dialog that cannot appear", launched)
        val started = shadowOf(activity).nextStartedActivity
        assertEquals(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, started.action)
        assertEquals("package:${activity.packageName}", started.data.toString())
    }

    /**
     * The whole point of persisting the ask: a user who denies the dialog into the permanently
     * denied state gets the Settings fallback on their *next* tap, from whichever screen it comes,
     * rather than a button that does nothing.
     */
    @Test
    fun `a denied first ask makes the next request fall back to settings`() {
        setRationale(false)

        PermissionHelper.requestRuntimePermission(
            activity = activity,
            permission = permission,
            prefsManager = prefsManager,
        ) { /* the user denies, permanently */ }
        shadowOf(activity).nextStartedActivity // drain (there should be none)

        var relaunched = false
        PermissionHelper.requestRuntimePermission(
            activity = activity,
            permission = permission,
            prefsManager = prefsManager,
        ) { relaunched = true }

        assertFalse(relaunched)
        assertEquals(
            Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
            shadowOf(activity).nextStartedActivity.action,
        )
    }
}
