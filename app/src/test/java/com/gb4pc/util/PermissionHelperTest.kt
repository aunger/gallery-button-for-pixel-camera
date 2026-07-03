package com.gb4pc.util

import android.Manifest
import android.app.AppOpsManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.mockito.kotlin.*

class PermissionHelperTest {
    private lateinit var context: Context

    @Before
    fun setUp() {
        context =
            mock {
                on { packageName } doReturn "com.gb4pc"
            }
    }

    @Test
    fun `isPixelCameraInstalled returns true when package exists`() {
        val pm: PackageManager =
            mock {
                on { getPackageInfo(eq("com.google.android.GoogleCamera"), any<Int>()) } doReturn mock()
            }
        whenever(context.packageManager).thenReturn(pm)
        assertTrue(PermissionHelper.isPixelCameraInstalled(context))
    }

    @Test
    fun `isPixelCameraInstalled returns false when package missing`() {
        val pm: PackageManager =
            mock {
                on { getPackageInfo(eq("com.google.android.GoogleCamera"), any<Int>()) } doThrow
                    PackageManager.NameNotFoundException()
            }
        whenever(context.packageManager).thenReturn(pm)
        assertFalse(PermissionHelper.isPixelCameraInstalled(context))
    }

    // In plain JVM unit tests Build.VERSION.SDK_INT is 0, so hasMediaPermission takes the
    // pre-API-33 branch and checks READ_EXTERNAL_STORAGE via context.checkSelfPermission.
    @Test
    fun `hasMediaPermission returns true when read permission granted`() {
        whenever(context.checkSelfPermission(Manifest.permission.READ_EXTERNAL_STORAGE))
            .thenReturn(PackageManager.PERMISSION_GRANTED)
        assertTrue(PermissionHelper.hasMediaPermission(context))
    }

    @Test
    fun `hasMediaPermission returns false when read permission denied`() {
        whenever(context.checkSelfPermission(Manifest.permission.READ_EXTERNAL_STORAGE))
            .thenReturn(PackageManager.PERMISSION_DENIED)
        assertFalse(PermissionHelper.hasMediaPermission(context))
    }

    @Test
    fun `isAppInstalled returns false for missing package`() {
        val pm: PackageManager =
            mock {
                on { getLaunchIntentForPackage(eq("com.example.missing")) } doReturn null
            }
        whenever(context.packageManager).thenReturn(pm)
        assertFalse(PermissionHelper.isAppInstalled(context, "com.example.missing"))
    }
}
