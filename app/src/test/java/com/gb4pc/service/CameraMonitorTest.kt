package com.gb4pc.service

import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

class CameraMonitorTest {

    private lateinit var state: CameraState

    @Before
    fun setUp() {
        state = CameraState()
    }

    @Test
    fun `initially all cameras are available`() {
        assertTrue(state.areAllCamerasAvailable())
    }

    @Test
    fun `marking camera unavailable makes areAllCamerasAvailable false`() {
        state.setCameraUnavailable("0")
        assertFalse(state.areAllCamerasAvailable())
    }

    @Test
    fun `marking camera available after unavailable restores state`() {
        state.setCameraUnavailable("0")
        state.setCameraAvailable("0")
        assertTrue(state.areAllCamerasAvailable())
    }

    @Test
    fun `multiple cameras - not all available until all released`() {
        state.setCameraUnavailable("0")
        state.setCameraUnavailable("1")
        assertFalse(state.areAllCamerasAvailable())

        state.setCameraAvailable("0")
        assertFalse(state.areAllCamerasAvailable()) // "1" still unavailable

        state.setCameraAvailable("1")
        assertTrue(state.areAllCamerasAvailable())
    }

    @Test
    fun `anyCameraUnavailable tracks correctly`() {
        assertFalse(state.anyCameraUnavailable())
        state.setCameraUnavailable("0")
        assertTrue(state.anyCameraUnavailable())
    }

    @Test
    fun `getUnavailableCameraIds returns correct set`() {
        state.setCameraUnavailable("0")
        state.setCameraUnavailable("2")
        val ids = state.getUnavailableCameraIds()
        assertEquals(setOf("0", "2"), ids)
    }

    @Test
    fun `reset clears all state`() {
        state.setCameraUnavailable("0")
        state.reset()
        assertTrue(state.areAllCamerasAvailable())
    }
}
