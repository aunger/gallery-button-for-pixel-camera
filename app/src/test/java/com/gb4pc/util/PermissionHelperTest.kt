package com.gb4pc.util

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
        context = mock {
            on { packageName } doReturn "com.gb4pc"
        }
    }

    @Test
    fun `isPixelCameraInstalled returns true when package exists`() {
        val pm: PackageManager = mock {
            on { getPackageInfo(eq("com.google.android.GoogleCamera"), any<Int>()) } doReturn mock()
        }
        whenever(context.packageManager).thenReturn(pm)
        assertTrue(PermissionHelper.isPixelCameraInstalled(context))
    }

    @Test
    fun `isPixelCameraInstalled returns false when package missing`() {
        val pm: PackageManager = mock {
            on { getPackageInfo(eq("com.google.android.GoogleCamera"), any<Int>()) } doThrow
                PackageManager.NameNotFoundException()
        }
        whenever(context.packageManager).thenReturn(pm)
        assertFalse(PermissionHelper.isPixelCameraInstalled(context))
    }

    @Test
    fun `isAppInstalled returns false for missing package`() {
        val pm: PackageManager = mock {
            on { getLaunchIntentForPackage(eq("com.example.missing")) } doReturn null
        }
        whenever(context.packageManager).thenReturn(pm)
        assertFalse(PermissionHelper.isAppInstalled(context, "com.example.missing"))
    }
}
