package com.gb4pc.service

import android.app.ActivityManager
import android.content.Context
import com.gb4pc.data.PrefsManager
import com.gb4pc.util.DebugLog
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.mockito.kotlin.*
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class ServiceCheckerTest {

    private lateinit var context: Context
    private lateinit var prefs: PrefsManager
    private lateinit var activityManager: ActivityManager

    @Before
    fun setUp() {
        activityManager = mock()
        context = mock {
            on { getSystemService(Context.ACTIVITY_SERVICE) } doReturn activityManager
        }
        prefs = mock {
            on { isServiceEnabled } doReturn false
        }
        DebugLog.clear()
    }

    // ---------------------------------------------------------------------------
    // isServiceRunning
    // ---------------------------------------------------------------------------

    @Test
    fun `isServiceRunning returns true when service is in running list`() {
        val serviceInfo = ActivityManager.RunningServiceInfo().apply {
            service = android.content.ComponentName("com.gb4pc", OverlayService::class.java.name)
        }
        whenever(activityManager.getRunningServices(Int.MAX_VALUE)).thenReturn(listOf(serviceInfo))
        assertTrue(ServiceChecker.isServiceRunning(context))
    }

    @Test
    fun `isServiceRunning returns false when service is not in running list`() {
        whenever(activityManager.getRunningServices(Int.MAX_VALUE)).thenReturn(emptyList())
        assertFalse(ServiceChecker.isServiceRunning(context))
    }

    @Test
    fun `isServiceRunning returns false when list contains a different service`() {
        val serviceInfo = ActivityManager.RunningServiceInfo().apply {
            service = android.content.ComponentName("com.other", "com.other.SomeService")
        }
        whenever(activityManager.getRunningServices(Int.MAX_VALUE)).thenReturn(listOf(serviceInfo))
        assertFalse(ServiceChecker.isServiceRunning(context))
    }

    // ---------------------------------------------------------------------------
    // ensureServiceRunningIfEnabled
    // ---------------------------------------------------------------------------

    @Test
    fun `ensureServiceRunningIfEnabled does nothing when service is disabled`() {
        whenever(prefs.isServiceEnabled).thenReturn(false)
        // Should not query ActivityManager at all
        ServiceChecker.ensureServiceRunningIfEnabled(context, prefs)
        verify(activityManager, never()).getRunningServices(any())
        assertTrue(DebugLog.getEntries().isEmpty())
    }

    @Test
    fun `ensureServiceRunningIfEnabled does nothing when service is enabled and already running`() {
        whenever(prefs.isServiceEnabled).thenReturn(true)
        val serviceInfo = ActivityManager.RunningServiceInfo().apply {
            service = android.content.ComponentName("com.gb4pc", OverlayService::class.java.name)
        }
        whenever(activityManager.getRunningServices(Int.MAX_VALUE)).thenReturn(listOf(serviceInfo))

        ServiceChecker.ensureServiceRunningIfEnabled(context, prefs)

        verify(context, never()).startForegroundService(any())
    }

    @Test
    fun `ensureServiceRunningIfEnabled logs and starts service when enabled but not running`() {
        whenever(prefs.isServiceEnabled).thenReturn(true)
        whenever(activityManager.getRunningServices(Int.MAX_VALUE)).thenReturn(emptyList())
        // startForegroundService requires a real Context, so we accept any invocation
        whenever(context.startForegroundService(any())).thenReturn(null)

        ServiceChecker.ensureServiceRunningIfEnabled(context, prefs)

        val logEntry = DebugLog.getEntries().firstOrNull()
        assertNotNull("Expected a log entry", logEntry)
        assertTrue(
            "Log should mention service not running",
            logEntry!!.message.contains("should be running but isn't")
        )
        verify(context).startForegroundService(any())
    }
}
