package com.gb4pc.ui.settings

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
    fun `initial state reflects prefs - service disabled, no gallery`() {
        val state = MainSettingsState.from(prefs)
        assertFalse(state.isServiceEnabled)
        assertNull(state.galleryPackage)
        assertTrue(state.isSetupCompleted)
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

    @Test
    fun `state reflects setup not completed`() {
        whenever(prefs.isSetupCompleted).thenReturn(false)
        val state = MainSettingsState.from(prefs)
        assertFalse(state.isSetupCompleted)
    }

    @Test
    fun `state is a data class with correct equality`() {
        whenever(prefs.isServiceEnabled).thenReturn(true)
        whenever(prefs.galleryPackage).thenReturn("com.example.gallery")
        val state1 = MainSettingsState.from(prefs)
        val state2 = MainSettingsState.from(prefs)
        assertEquals(state1, state2)
    }
}
