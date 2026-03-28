package com.gb4pc.receiver

import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import com.gb4pc.Constants
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.mockito.kotlin.*

class BootReceiverTest {

    private lateinit var context: Context
    private lateinit var prefs: SharedPreferences

    @Before
    fun setUp() {
        prefs = mock {
            on { getBoolean(eq(Constants.PREF_SERVICE_ENABLED), any()) } doReturn false
        }
        context = mock {
            on { getSharedPreferences(eq(Constants.PREFS_NAME), eq(Context.MODE_PRIVATE)) } doReturn prefs
        }
    }

    @Test
    fun `shouldStartService returns true when service was enabled`() {
        whenever(prefs.getBoolean(Constants.PREF_SERVICE_ENABLED, false)).thenReturn(true)
        assertTrue(BootReceiverLogic.shouldStartService(context))
    }

    @Test
    fun `shouldStartService returns false when service was disabled`() {
        whenever(prefs.getBoolean(Constants.PREF_SERVICE_ENABLED, false)).thenReturn(false)
        assertFalse(BootReceiverLogic.shouldStartService(context))
    }

    @Test
    fun `shouldStartService returns false for null prefs value`() {
        // Default is false
        assertFalse(BootReceiverLogic.shouldStartService(context))
    }

    @Test
    fun `isBootIntent returns true for BOOT_COMPLETED`() {
        assertTrue(BootReceiverLogic.isBootIntent(Intent.ACTION_BOOT_COMPLETED))
    }

    @Test
    fun `isBootIntent returns false for other actions`() {
        assertFalse(BootReceiverLogic.isBootIntent("com.example.OTHER"))
        assertFalse(BootReceiverLogic.isBootIntent(null))
    }
}
