package com.gb4pc.data

import android.content.Context
import android.content.SharedPreferences
import com.gb4pc.Constants
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.mockito.kotlin.*

class PrefsManagerTest {

    private lateinit var prefs: SharedPreferences
    private lateinit var editor: SharedPreferences.Editor
    private lateinit var context: Context
    private lateinit var prefsManager: PrefsManager

    @Before
    fun setUp() {
        editor = mock {
            on { putString(any(), any()) } doReturn it
            on { putBoolean(any(), anyOrNull<Boolean>() ?: false) } doReturn it
        }
        prefs = mock {
            on { edit() } doReturn editor
            on { getString(eq(Constants.PREF_GALLERY_PACKAGE), anyOrNull()) } doReturn null
            on { getBoolean(eq(Constants.PREF_SERVICE_ENABLED), any()) } doReturn false
            on { getBoolean(eq(Constants.PREF_SETUP_COMPLETED), any()) } doReturn false
            on { getString(eq(Constants.PREF_OVERLAY_POSITIONS), anyOrNull()) } doReturn ""
        }
        context = mock {
            on { getSharedPreferences(eq(Constants.PREFS_NAME), eq(Context.MODE_PRIVATE)) } doReturn prefs
        }
        prefsManager = PrefsManager(context)
    }

    @Test
    fun `isServiceEnabled returns false by default`() {
        assertFalse(prefsManager.isServiceEnabled)
    }

    @Test
    fun `setServiceEnabled persists to prefs`() {
        prefsManager.isServiceEnabled = true
        verify(editor).putBoolean(Constants.PREF_SERVICE_ENABLED, true)
        verify(editor).apply()
    }

    @Test
    fun `galleryPackage returns null by default`() {
        assertNull(prefsManager.galleryPackage)
    }

    @Test
    fun `setGalleryPackage persists to prefs`() {
        prefsManager.galleryPackage = "com.example.gallery"
        verify(editor).putString(Constants.PREF_GALLERY_PACKAGE, "com.example.gallery")
        verify(editor).apply()
    }

    @Test
    fun `isSetupCompleted returns false by default`() {
        assertFalse(prefsManager.isSetupCompleted)
    }

    @Test
    fun `getOverlayPosition returns default when no stored positions`() {
        val pos = prefsManager.getOverlayPosition("0.45")
        assertEquals(Constants.DEFAULT_X_PERCENT, pos.xPercent, 0.001f)
        assertEquals(Constants.DEFAULT_Y_PERCENT, pos.yPercent, 0.001f)
        assertEquals(Constants.DEFAULT_SIZE_PERCENT, pos.sizePercent, 0.001f)
    }

    @Test
    fun `getOverlayPosition returns stored position for exact ratio`() {
        val json = """{"0.45":{"x":20.0,"y":80.0,"size":10.0}}"""
        whenever(prefs.getString(eq(Constants.PREF_OVERLAY_POSITIONS), anyOrNull())).thenReturn(json)
        prefsManager = PrefsManager(context)

        val pos = prefsManager.getOverlayPosition("0.45")
        assertEquals(20.0f, pos.xPercent, 0.001f)
        assertEquals(80.0f, pos.yPercent, 0.001f)
        assertEquals(10.0f, pos.sizePercent, 0.001f)
    }

    @Test
    fun `getOverlayPosition falls back to closest ratio per PS-04`() {
        val json = """{"0.45":{"x":20.0,"y":80.0,"size":10.0},"0.56":{"x":30.0,"y":70.0,"size":12.0}}"""
        whenever(prefs.getString(eq(Constants.PREF_OVERLAY_POSITIONS), anyOrNull())).thenReturn(json)
        prefsManager = PrefsManager(context)

        // 0.50 is closer to 0.45 than 0.56
        val pos = prefsManager.getOverlayPosition("0.50")
        assertEquals(20.0f, pos.xPercent, 0.001f)
    }

    @Test
    fun `saveOverlayPosition persists json`() {
        prefsManager.saveOverlayPosition("0.45", OverlayPosition(25f, 85f, 12f))
        verify(editor).putString(eq(Constants.PREF_OVERLAY_POSITIONS), any())
        verify(editor).apply()
    }
}
