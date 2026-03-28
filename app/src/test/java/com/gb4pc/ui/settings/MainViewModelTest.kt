package com.gb4pc.ui.settings

import com.gb4pc.data.OverlayPosition
import com.gb4pc.data.PrefsManager
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.mockito.kotlin.*

class MainViewModelTest {

    private lateinit var prefs: PrefsManager

    @Before
    fun setUp() {
        prefs = mock {
            on { isServiceEnabled } doReturn false
            on { galleryPackage } doReturn null
            on { isSetupCompleted } doReturn true
        }
    }

    @Test
    fun `initial state reflects prefs`() {
        val state = MainSettingsState.from(prefs)
        assertFalse(state.isServiceEnabled)
        assertNull(state.galleryPackage)
    }

    @Test
    fun `state reflects enabled service`() {
        whenever(prefs.isServiceEnabled).thenReturn(true)
        val state = MainSettingsState.from(prefs)
        assertTrue(state.isServiceEnabled)
    }

    @Test
    fun `state reflects configured gallery app`() {
        whenever(prefs.galleryPackage).thenReturn("com.example.gallery")
        val state = MainSettingsState.from(prefs)
        assertEquals("com.example.gallery", state.galleryPackage)
    }
}

/**
 * State class for testing - mirrors what the Compose screen will use.
 */
data class MainSettingsState(
    val isServiceEnabled: Boolean,
    val galleryPackage: String?,
    val isSetupCompleted: Boolean
) {
    companion object {
        fun from(prefs: PrefsManager): MainSettingsState {
            return MainSettingsState(
                isServiceEnabled = prefs.isServiceEnabled,
                galleryPackage = prefs.galleryPackage,
                isSetupCompleted = prefs.isSetupCompleted
            )
        }
    }
}
