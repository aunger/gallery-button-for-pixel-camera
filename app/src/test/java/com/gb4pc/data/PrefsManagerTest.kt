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
    private lateinit var installPrefs: SharedPreferences
    private lateinit var installEditor: SharedPreferences.Editor
    private lateinit var context: Context
    private lateinit var prefsManager: PrefsManager

    @Before
    fun setUp() {
        editor =
            mock {
                on { putString(any(), any()) } doReturn it
                on { putBoolean(any(), anyOrNull<Boolean>() ?: false) } doReturn it
                on { putLong(any(), any()) } doReturn it
            }
        prefs =
            mock {
                on { edit() } doReturn editor
                on { getString(eq(Constants.PREF_GALLERY_PACKAGE), anyOrNull()) } doReturn null
                on { getBoolean(eq(Constants.PREF_FOCUSABLE_OVERLAY), any()) } doReturn false
                on { getString(eq(Constants.PREF_OVERLAY_POSITIONS), anyOrNull()) } doReturn ""
            }
        installEditor =
            mock {
                on { putBoolean(any(), anyOrNull<Boolean>() ?: false) } doReturn it
            }
        installPrefs =
            mock {
                on { edit() } doReturn installEditor
                on { getBoolean(eq(Constants.PREF_SERVICE_ENABLED), any()) } doReturn false
                on { getBoolean(eq(Constants.PREF_SETUP_COMPLETED), any()) } doReturn false
            }
        context =
            mock {
                on { getSharedPreferences(eq(Constants.PREFS_NAME), eq(Context.MODE_PRIVATE)) } doReturn prefs
                on {
                    getSharedPreferences(eq(Constants.INSTALL_PREFS_NAME), eq(Context.MODE_PRIVATE))
                } doReturn installPrefs
            }
        prefsManager = PrefsManager(context)
    }

    @Test
    fun `isServiceEnabled returns false by default`() {
        assertFalse(prefsManager.isServiceEnabled)
    }

    @Test
    fun `setServiceEnabled persists to the per-install file`() {
        prefsManager.isServiceEnabled = true
        verify(installEditor).putBoolean(Constants.PREF_SERVICE_ENABLED, true)
        verify(installEditor).apply()
        verifyNoInteractions(editor)
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
        verifyNoInteractions(installEditor)
    }

    @Test
    fun `isSetupCompleted returns false by default`() {
        assertFalse(prefsManager.isSetupCompleted)
    }

    @Test
    fun `setSetupCompleted persists to the per-install file`() {
        prefsManager.isSetupCompleted = true
        verify(installEditor).putBoolean(Constants.PREF_SETUP_COMPLETED, true)
        verify(installEditor).apply()
        verifyNoInteractions(editor)
    }

    @Test
    fun `setRuntimePermissionRequested persists to the per-install file`() {
        prefsManager.setRuntimePermissionRequested("android.permission.CAMERA", true)
        verify(installEditor).putBoolean(Constants.PREF_PERMISSION_REQUESTED_PREFIX + "android.permission.CAMERA", true)
        verify(installEditor).apply()
        verifyNoInteractions(editor)
    }

    @Test
    fun `setPermissionHistorySeeded persists to the per-install file`() {
        prefsManager.isPermissionHistorySeeded = true
        verify(installEditor).putBoolean(Constants.PREF_PERMISSION_HISTORY_SEEDED, true)
        verify(installEditor).apply()
        verifyNoInteractions(editor)
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
        verifyNoInteractions(installEditor)
    }

    @Test
    fun `saveOverlayPosition then getOverlayPosition roundtrips multiple ratios`() {
        // Capture the JSON written to prefs by saveOverlayPosition, then feed it back
        // through a fresh PrefsManager to verify the full serialize → deserialize loop.
        val captured = argumentCaptor<String>()
        prefsManager.saveOverlayPosition("0.45", OverlayPosition(13.0f, 91.5f, 11.5f))
        verify(editor).putString(eq(Constants.PREF_OVERLAY_POSITIONS), captured.capture())
        whenever(prefs.getString(eq(Constants.PREF_OVERLAY_POSITIONS), anyOrNull()))
            .thenReturn(captured.lastValue)
        prefsManager = PrefsManager(context)
        prefsManager.saveOverlayPosition("0.56", OverlayPosition(15.0f, 90.0f, 12.0f))
        verify(editor, times(2)).putString(eq(Constants.PREF_OVERLAY_POSITIONS), captured.capture())
        whenever(prefs.getString(eq(Constants.PREF_OVERLAY_POSITIONS), anyOrNull()))
            .thenReturn(captured.lastValue)
        prefsManager = PrefsManager(context)

        val pos45 = prefsManager.getOverlayPosition("0.45")
        assertEquals(13.0f, pos45.xPercent, 0.001f)
        assertEquals(91.5f, pos45.yPercent, 0.001f)
        assertEquals(11.5f, pos45.sizePercent, 0.001f)

        val pos56 = prefsManager.getOverlayPosition("0.56")
        assertEquals(15.0f, pos56.xPercent, 0.001f)
        assertEquals(90.0f, pos56.yPercent, 0.001f)
        assertEquals(12.0f, pos56.sizePercent, 0.001f)
    }

    @Test
    fun `getOverlayPosition returns default when stored json is malformed`() {
        whenever(prefs.getString(eq(Constants.PREF_OVERLAY_POSITIONS), anyOrNull()))
            .thenReturn("not json")
        prefsManager = PrefsManager(context)

        val pos = prefsManager.getOverlayPosition("0.45")
        assertEquals(Constants.DEFAULT_X_PERCENT, pos.xPercent, 0.001f)
        assertEquals(Constants.DEFAULT_Y_PERCENT, pos.yPercent, 0.001f)
        assertEquals(Constants.DEFAULT_SIZE_PERCENT, pos.sizePercent, 0.001f)
    }

    @Test
    fun `focusableOverlay returns false by default`() {
        assertFalse(prefsManager.focusableOverlay)
    }

    @Test
    fun `setFocusableOverlay persists to prefs`() {
        prefsManager.focusableOverlay = true
        verify(editor).putBoolean(Constants.PREF_FOCUSABLE_OVERLAY, true)
        verify(editor).apply()
        verifyNoInteractions(installEditor)
    }

    @Test
    fun `setCameraDebounceMs persists to prefs`() {
        prefsManager.cameraDebounceMs = 120L
        verify(editor).putLong(Constants.PREF_CAMERA_DEBOUNCE_MS, 120L)
        verify(editor).apply()
        verifyNoInteractions(installEditor)
    }

    // Read side of the split (issue #1235). Each test stubs one file only; a mock answers the
    // other file with false, null or 0, so a getter reading the wrong file fails the assertion.

    @Test
    fun `isServiceEnabled reads the per-install file`() {
        whenever(installPrefs.getBoolean(eq(Constants.PREF_SERVICE_ENABLED), any())).thenReturn(true)
        assertTrue(prefsManager.isServiceEnabled)
    }

    @Test
    fun `isSetupCompleted reads the per-install file`() {
        whenever(installPrefs.getBoolean(eq(Constants.PREF_SETUP_COMPLETED), any())).thenReturn(true)
        assertTrue(prefsManager.isSetupCompleted)
    }

    @Test
    fun `hasRequestedRuntimePermission reads the per-install file`() {
        val key = Constants.PREF_PERMISSION_REQUESTED_PREFIX + "android.permission.CAMERA"
        whenever(installPrefs.getBoolean(eq(key), any())).thenReturn(true)
        assertTrue(prefsManager.hasRequestedRuntimePermission("android.permission.CAMERA"))
    }

    @Test
    fun `isPermissionHistorySeeded reads the per-install file`() {
        whenever(installPrefs.getBoolean(eq(Constants.PREF_PERMISSION_HISTORY_SEEDED), any())).thenReturn(true)
        assertTrue(prefsManager.isPermissionHistorySeeded)
    }

    @Test
    fun `galleryPackage reads prefs`() {
        whenever(prefs.getString(eq(Constants.PREF_GALLERY_PACKAGE), anyOrNull())).thenReturn("com.example.gallery")
        assertEquals("com.example.gallery", prefsManager.galleryPackage)
    }

    @Test
    fun `cameraDebounceMs reads prefs`() {
        whenever(prefs.getLong(eq(Constants.PREF_CAMERA_DEBOUNCE_MS), any())).thenReturn(120L)
        assertEquals(120L, prefsManager.cameraDebounceMs)
    }

    @Test
    fun `focusableOverlay reads prefs`() {
        whenever(prefs.getBoolean(eq(Constants.PREF_FOCUSABLE_OVERLAY), any())).thenReturn(true)
        assertTrue(prefsManager.focusableOverlay)
    }
}
