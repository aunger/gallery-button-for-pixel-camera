package com.gb4pc.receiver

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import com.gb4pc.Constants
import org.junit.Before
import org.junit.Test
import org.mockito.kotlin.any
import org.mockito.kotlin.doReturn
import org.mockito.kotlin.eq
import org.mockito.kotlin.mock
import org.mockito.kotlin.never
import org.mockito.kotlin.verify
import org.mockito.kotlin.whenever

class BootReceiverTest {
    private lateinit var context: Context
    private lateinit var prefs: SharedPreferences
    private val receiver = BootReceiver()

    @Before
    fun setUp() {
        prefs =
            mock {
                on { getBoolean(eq(Constants.PREF_SERVICE_ENABLED), any()) } doReturn false
            }
        context =
            mock {
                on { getSharedPreferences(eq(Constants.PREFS_NAME), eq(Context.MODE_PRIVATE)) } doReturn prefs
                on { startForegroundService(any()) } doReturn ComponentName("pkg", "cls")
            }
    }

    private fun intentWithAction(action: String?): Intent = mock { on { getAction() } doReturn action }

    @Test
    fun `BOOT_COMPLETED with service enabled starts foreground service`() {
        whenever(prefs.getBoolean(Constants.PREF_SERVICE_ENABLED, false)).thenReturn(true)

        receiver.onReceive(context, intentWithAction(Intent.ACTION_BOOT_COMPLETED))

        verify(context).startForegroundService(any())
    }

    @Test
    fun `BOOT_COMPLETED with service disabled does not start service`() {
        whenever(prefs.getBoolean(Constants.PREF_SERVICE_ENABLED, false)).thenReturn(false)

        receiver.onReceive(context, intentWithAction(Intent.ACTION_BOOT_COMPLETED))

        verify(context, never()).startForegroundService(any())
    }

    @Test
    fun `MY_PACKAGE_REPLACED with service enabled starts foreground service`() {
        whenever(prefs.getBoolean(Constants.PREF_SERVICE_ENABLED, false)).thenReturn(true)

        receiver.onReceive(context, intentWithAction(Intent.ACTION_MY_PACKAGE_REPLACED))

        verify(context).startForegroundService(any())
    }

    @Test
    fun `MY_PACKAGE_REPLACED with service disabled does not start service`() {
        whenever(prefs.getBoolean(Constants.PREF_SERVICE_ENABLED, false)).thenReturn(false)

        receiver.onReceive(context, intentWithAction(Intent.ACTION_MY_PACKAGE_REPLACED))

        verify(context, never()).startForegroundService(any())
    }

    @Test
    fun `unrecognized action is ignored regardless of pref`() {
        whenever(prefs.getBoolean(Constants.PREF_SERVICE_ENABLED, false)).thenReturn(true)

        receiver.onReceive(context, intentWithAction("com.example.OTHER"))

        verify(context, never()).startForegroundService(any())
    }

    @Test
    fun `intent with null action is ignored`() {
        whenever(prefs.getBoolean(Constants.PREF_SERVICE_ENABLED, false)).thenReturn(true)

        receiver.onReceive(context, intentWithAction(null))

        verify(context, never()).startForegroundService(any())
    }
}
