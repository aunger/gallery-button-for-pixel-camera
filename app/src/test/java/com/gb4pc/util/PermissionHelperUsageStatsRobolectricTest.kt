package com.gb4pc.util

import android.app.AppOpsManager
import android.content.Context
import android.os.Build
import android.os.Process
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * Robolectric tests for [PermissionHelper.hasUsageStatsPermission] on both sides of API 29, where
 * `AppOpsManager.unsafeCheckOpNoThrow` first appears (issue #985).
 *
 * The API 26 cases are the regression guard. Robolectric runs them against the API 26 framework,
 * which has no `unsafeCheckOpNoThrow`, so a call to it there throws `NoSuchMethodError` exactly as
 * it would on an Android 8.0 device. The cases at the project's default simulated SDK (targetSdk 35)
 * cover the branch that does call it.
 */
@RunWith(RobolectricTestRunner::class)
class PermissionHelperUsageStatsRobolectricTest {
    private val context: Context get() = ApplicationProvider.getApplicationContext()

    private fun setUsageStatsMode(mode: Int) {
        val appOps = context.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
        shadowOf(appOps).setMode(AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), context.packageName, mode)
    }

    @Test
    @Config(sdk = [Build.VERSION_CODES.O])
    fun `on API 26 an allowed usage-stats op reads as granted`() {
        setUsageStatsMode(AppOpsManager.MODE_ALLOWED)

        assertTrue(PermissionHelper.hasUsageStatsPermission(context))
    }

    @Test
    @Config(sdk = [Build.VERSION_CODES.O])
    fun `on API 26 an ignored usage-stats op reads as not granted`() {
        setUsageStatsMode(AppOpsManager.MODE_IGNORED)

        assertFalse(PermissionHelper.hasUsageStatsPermission(context))
    }

    @Test
    fun `on the default SDK an allowed usage-stats op reads as granted`() {
        setUsageStatsMode(AppOpsManager.MODE_ALLOWED)

        assertTrue(PermissionHelper.hasUsageStatsPermission(context))
    }

    @Test
    fun `on the default SDK an ignored usage-stats op reads as not granted`() {
        setUsageStatsMode(AppOpsManager.MODE_IGNORED)

        assertFalse(PermissionHelper.hasUsageStatsPermission(context))
    }
}
