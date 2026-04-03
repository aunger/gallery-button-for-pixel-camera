package com.gb4pc.service

import com.gb4pc.Constants
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.mockito.kotlin.*

class OverlayServiceLogicTest {

    private lateinit var cameraState: CameraState
    private lateinit var foregroundDetector: ForegroundDetector
    private var hasUsageStats = true
    private var hasOverlay = true
    private var isLocked = false

    private lateinit var logic: OverlayServiceLogic

    @Before
    fun setUp() {
        cameraState = CameraState()
        foregroundDetector = mock()
        hasUsageStats = true
        hasOverlay = true
        isLocked = false

        logic = OverlayServiceLogic(
            cameraState = cameraState,
            foregroundDetector = foregroundDetector,
            hasUsageStatsPermission = { hasUsageStats },
            hasOverlayPermission = { hasOverlay },
            isKeyguardLocked = { isLocked },
        )
    }

    // --- Camera unavailable / foreground evaluation ---

    @Test
    fun `onCameraUnavailable marks camera and evaluates foreground`() {
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)

        val action = logic.onCameraUnavailable("0")

        assertTrue(cameraState.anyCameraUnavailable())
        assertTrue(action is ServiceAction.ShowOverlay)
    }

    @Test
    fun `onCameraUnavailable does not show overlay if not Pixel Camera`() {
        whenever(foregroundDetector.getForegroundPackage()).thenReturn("com.example.other")

        val action = logic.onCameraUnavailable("0")

        assertEquals(ServiceAction.None, action)
        assertFalse(logic.isOverlayActive)
    }

    @Test
    fun `onCameraUnavailable shows overlay only once`() {
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)

        val first = logic.onCameraUnavailable("0")
        assertTrue(first is ServiceAction.ShowOverlay)

        val second = logic.onCameraUnavailable("1")
        assertEquals(ServiceAction.None, second) // already active
    }

    // --- Camera available / deactivation ---

    @Test
    fun `onCameraAvailable schedules deactivation when all cameras available`() {
        cameraState.setCameraUnavailable("0")

        val action = logic.onCameraAvailable("0")

        assertTrue(action is ServiceAction.ScheduleDeactivation)
        assertEquals(Constants.CAMERA_DEBOUNCE_MS, (action as ServiceAction.ScheduleDeactivation).delayMs)
    }

    @Test
    fun `onCameraAvailable does not schedule if other cameras still unavailable`() {
        cameraState.setCameraUnavailable("0")
        cameraState.setCameraUnavailable("1")

        val action = logic.onCameraAvailable("0")

        assertEquals(ServiceAction.None, action)
    }

    // --- Debounce timer ---

    @Test
    fun `onDeactivationTimerFired deactivates when all cameras available`() {
        // Activate first
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)
        logic.onCameraUnavailable("0")
        assertTrue(logic.isOverlayActive)

        // Release camera
        cameraState.setCameraAvailable("0")

        val action = logic.onDeactivationTimerFired()
        assertEquals(ServiceAction.Deactivate, action)
        assertFalse(logic.isOverlayActive)
    }

    @Test
    fun `onDeactivationTimerFired does nothing if camera re-acquired`() {
        // Camera goes unavailable again before timer fires
        cameraState.setCameraUnavailable("0")

        val action = logic.onDeactivationTimerFired()
        assertEquals(ServiceAction.None, action)
    }

    // --- Permission checks ---

    @Test
    fun `evaluateForeground hides overlay if usage stats lost`() {
        // Activate overlay first
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)
        logic.onCameraUnavailable("0")
        assertTrue(logic.isOverlayActive)

        // Revoke permission
        hasUsageStats = false
        val action = logic.evaluateForeground()

        assertEquals(ServiceAction.HideAndNotifyPermissionLost, action)
        assertFalse(logic.isOverlayActive)
    }

    @Test
    fun `evaluateForeground reports overlay permission lost`() {
        hasOverlay = false
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)

        val action = logic.evaluateForeground()

        assertEquals(ServiceAction.NotifyOverlayPermissionLost, action)
        assertFalse(logic.isOverlayActive)
    }

    // --- Session start ---

    @Test
    fun `showOverlay starts session when device is locked`() {
        isLocked = true
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)

        val action = logic.onCameraUnavailable("0")

        assertTrue(action is ServiceAction.ShowOverlay)
        assertTrue((action as ServiceAction.ShowOverlay).startSession)
    }

    @Test
    fun `showOverlay does not start session when device is unlocked`() {
        isLocked = false
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)

        val action = logic.onCameraUnavailable("0")

        assertTrue(action is ServiceAction.ShowOverlay)
        assertFalse((action as ServiceAction.ShowOverlay).startSession)
    }

    // --- Multi-camera DT-05 ---

    @Test
    fun `switching cameras does not deactivate overlay`() {
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)

        // Front camera acquired
        logic.onCameraUnavailable("0")
        assertTrue(logic.isOverlayActive)

        // Switch: back camera acquired, front released
        logic.onCameraUnavailable("1")
        val releaseAction = logic.onCameraAvailable("0")

        // Should not schedule deactivation because camera "1" is still unavailable
        assertEquals(ServiceAction.None, releaseAction)
    }
}
