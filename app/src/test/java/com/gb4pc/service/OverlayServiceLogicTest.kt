package com.gb4pc.service

import android.os.Handler
import com.gb4pc.Constants
import com.gb4pc.overlay.OverlayManager
import com.gb4pc.viewer.SessionTracker
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.mockito.kotlin.*

/**
 * Unit tests for [OverlayServiceLogic] covering the five scenarios originally exercised by
 * the now-deleted OverlayServiceLogicTest and called out in issue #12.
 *
 * All Android-framework dependencies are replaced by mocks or simple lambda flags, so these
 * tests run on the plain JVM without Robolectric.
 */
class OverlayServiceLogicTest {

    // ── Mocked collaborators ────────────────────────────────────────────────
    private lateinit var overlayManager: OverlayManager
    private lateinit var foregroundDetector: ForegroundDetectorPort
    private lateinit var sessionTracker: SessionTracker
    private lateinit var handler: Handler

    // CameraState is used as a real object — it has no Android deps
    private lateinit var cameraState: CameraState

    // ── Lambda state flags ──────────────────────────────────────────────────
    private var usageStatsPermission = true
    private var overlayPermission = true
    private var keyguardLocked = false

    private var usageAccessLostCount = 0
    private var overlayLostCount = 0
    private var mediaObserverRegistered = false

    // ── Subject under test ──────────────────────────────────────────────────
    private lateinit var logic: OverlayServiceLogic

    @Before
    fun setUp() {
        overlayManager = mock()
        foregroundDetector = mock()
        sessionTracker = mock()
        handler = mock()
        cameraState = CameraState()

        usageStatsPermission = true
        overlayPermission = true
        keyguardLocked = false
        usageAccessLostCount = 0
        overlayLostCount = 0
        mediaObserverRegistered = false

        logic = OverlayServiceLogic(
            hasUsageStatsPermission = { usageStatsPermission },
            hasOverlayPermission = { overlayPermission },
            overlayManager = overlayManager,
            cameraState = cameraState,
            foregroundDetector = foregroundDetector,
            sessionTracker = sessionTracker,
            handler = handler,
            debounceMs = 0L,          // keep handler calls synchronous-looking in tests
            activationRetryMs = 0L,   // same — retry fires immediately
            onUsageAccessLost = { usageAccessLostCount++ },
            onOverlayPermissionLost = { overlayLostCount++ },
            isKeyguardLocked = { keyguardLocked },
            onRegisterMediaObserver = { mediaObserverRegistered = true },
            onUnregisterMediaObserver = { mediaObserverRegistered = false },
        )
    }

    // ── DT-05: multi-camera ─────────────────────────────────────────────────

    /**
     * A second onCameraUnavailable for a different camera while the overlay is active must
     * not prematurely deactivate it.  Only once the LAST tracked camera becomes available
     * should a deactivation be scheduled.
     */
    @Test
    fun `DT-05 second camera unavailable does not schedule deactivation`() {
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)

        // Camera 0 in use → overlay activates
        logic.onCameraUnavailable("0")
        assertTrue("Overlay should be active after camera 0 unavailable", logic.isOverlayActive)

        // Camera 1 also in use — no deactivation should be scheduled
        logic.onCameraUnavailable("1")
        assertTrue("Overlay should remain active while camera 1 is also in use", logic.isOverlayActive)
        verify(handler, never()).postDelayed(any(), any())

        // Camera 0 released but camera 1 still in use → still no deactivation
        logic.onCameraAvailable("0")
        verify(handler, never()).postDelayed(any(), any())
        assertTrue("Overlay should still be active while camera 1 is in use", logic.isOverlayActive)

        // Camera 1 released — all cameras available → deactivation scheduled
        logic.onCameraAvailable("1")
        verify(handler, times(1)).postDelayed(any(), eq(0L))
    }

    @Test
    fun `DT-05 deactivation runnable hides overlay only when all cameras available`() {
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)

        logic.onCameraUnavailable("0")
        assertTrue(logic.isOverlayActive)

        logic.onCameraAvailable("0")

        // Capture and run the deactivation runnable
        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(0L))
        runnableCaptor.firstValue.run()

        assertFalse("Overlay should be hidden after deactivation runnable executes", logic.isOverlayActive)
        verify(overlayManager).hide()
    }

    // ── Idempotent activation ───────────────────────────────────────────────

    /**
     * Calling evaluateForeground() multiple times while Pixel Camera is already the foreground
     * app must not show the overlay more than once.
     */
    @Test
    fun `idempotent - evaluateForeground multiple times shows overlay exactly once`() {
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)

        logic.evaluateForeground()
        logic.evaluateForeground()
        logic.evaluateForeground()

        verify(overlayManager, times(1)).show()
        assertTrue(logic.isOverlayActive)
    }

    /**
     * Calling evaluateForeground() again while the overlay is already active must not
     * register a duplicate media observer.
     */
    @Test
    fun `idempotent - evaluateForeground does not re-register media observer when already active`() {
        keyguardLocked = true
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)

        logic.evaluateForeground()
        assertTrue(mediaObserverRegistered)

        // Call again — observer must not be registered a second time
        var registrationCount = 0
        val logic2 = OverlayServiceLogic(
            hasUsageStatsPermission = { true },
            hasOverlayPermission = { true },
            overlayManager = overlayManager,
            cameraState = cameraState,
            foregroundDetector = foregroundDetector,
            sessionTracker = sessionTracker,
            handler = handler,
            debounceMs = 0L,
            onUsageAccessLost = {},
            onOverlayPermissionLost = {},
            isKeyguardLocked = { keyguardLocked },
            onRegisterMediaObserver = { registrationCount++ },
            onUnregisterMediaObserver = {},
        )
        logic2.evaluateForeground()
        logic2.evaluateForeground()
        logic2.evaluateForeground()

        assertEquals("Media observer should be registered exactly once", 1, registrationCount)
    }

    // ── Usage-stats permission revocation ───────────────────────────────────

    /**
     * If hasUsageStatsPermission returns false during evaluateForeground while the overlay is
     * active, the overlay is hidden and the usage-access-lost notification is fired.
     */
    @Test
    fun `usage stats revoked while overlay active hides overlay and fires notification`() {
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)
        logic.evaluateForeground()
        assertTrue("Pre-condition: overlay should be active", logic.isOverlayActive)

        // Revoke permission and evaluate again
        usageStatsPermission = false
        logic.evaluateForeground()

        verify(overlayManager).hide()
        assertFalse("Overlay should be inactive after permission revocation", logic.isOverlayActive)
        assertEquals("Usage-access-lost notification should fire once", 1, usageAccessLostCount)
    }

    /**
     * If the overlay is not active when usage-stats permission is missing, no notification
     * is fired and hide() is not called.
     */
    @Test
    fun `usage stats missing when overlay inactive does not fire notification`() {
        usageStatsPermission = false
        logic.evaluateForeground()

        assertEquals(0, usageAccessLostCount)
        verify(overlayManager, never()).hide()
        assertFalse(logic.isOverlayActive)
    }

    // ── Overlay permission loss ─────────────────────────────────────────────

    /**
     * If hasOverlayPermission returns false, the overlay-lost notification fires without
     * changing isOverlayActive (it remains false / unchanged).
     */
    @Test
    fun `showOverlay with missing overlay permission fires notification and leaves isOverlayActive false`() {
        overlayPermission = false
        logic.showOverlay()

        assertEquals("Overlay-lost notification should fire once", 1, overlayLostCount)
        assertFalse("isOverlayActive must remain false", logic.isOverlayActive)
        verify(overlayManager, never()).show()
    }

    @Test
    fun `showOverlay with missing overlay permission does not start session`() {
        overlayPermission = false
        keyguardLocked = true
        logic.showOverlay()

        verify(sessionTracker, never()).startSession()
        assertFalse(mediaObserverRegistered)
    }

    // ── Session gating on keyguard state ────────────────────────────────────

    /**
     * showOverlay() starts a secure session only when isKeyguardLocked is true;
     * when the screen is unlocked at activation time no session is started.
     */
    @Test
    fun `showOverlay starts session when device is locked at activation time`() {
        keyguardLocked = true
        logic.showOverlay()

        assertTrue(logic.isOverlayActive)
        verify(sessionTracker).startSession()
        assertTrue("Media observer should be registered when session starts", mediaObserverRegistered)
    }

    @Test
    fun `showOverlay does not start session when device is unlocked at activation time`() {
        keyguardLocked = false
        logic.showOverlay()

        assertTrue(logic.isOverlayActive)
        verify(sessionTracker, never()).startSession()
        assertFalse("Media observer should not be registered when device is unlocked", mediaObserverRegistered)
    }

    // ── EC-09: UsageStats activation retry ─────────────────────────────────

    /**
     * Regression test for the bug in issue #15: if UsageStatsManager hasn't delivered
     * the MOVE_TO_FOREGROUND event for Pixel Camera by the time the first
     * onCameraUnavailable callback fires, the overlay must still appear once the
     * activation-retry runnable executes and foreground detection succeeds.
     */
    @Test
    fun `EC-09 overlay activates on retry when UsageStats initially lags`() {
        // First call returns null (UsageStats hasn't delivered the event yet).
        // Second call (retry) returns the PC package.
        var callCount = 0
        val laggingDetector = ForegroundDetectorPort {
            callCount++
            if (callCount == 1) null else Constants.PIXEL_CAMERA_PACKAGE
        }
        val logicWithLaggingDetector = OverlayServiceLogic(
            hasUsageStatsPermission = { true },
            hasOverlayPermission = { true },
            overlayManager = overlayManager,
            cameraState = cameraState,
            foregroundDetector = laggingDetector,
            sessionTracker = sessionTracker,
            handler = handler,
            debounceMs = 0L,
            activationRetryMs = 0L,
            onUsageAccessLost = {},
            onOverlayPermissionLost = {},
            isKeyguardLocked = { false },
            onRegisterMediaObserver = {},
            onUnregisterMediaObserver = {},
        )

        // Camera opens — first evaluateForeground call returns null; overlay stays hidden.
        logicWithLaggingDetector.onCameraUnavailable("0")
        assertFalse("Overlay must not appear when UsageStats returns null", logicWithLaggingDetector.isOverlayActive)

        // The retry runnable was posted to handler. Capture it and execute it.
        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler, atLeastOnce()).postDelayed(runnableCaptor.capture(), eq(0L))
        // Run the retry runnable (the one posted for activation retry).
        runnableCaptor.allValues.last().run()

        assertTrue("Overlay must appear after retry when UsageStats delivers the event", logicWithLaggingDetector.isOverlayActive)
        verify(overlayManager).show()
    }

    /**
     * When the camera is released before the retry fires, the retry must be cancelled
     * and the overlay must NOT appear (EC-07 / EC-09 interaction).
     */
    @Test
    fun `EC-09 retry is cancelled when camera becomes available before retry fires`() {
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(null)

        logic.onCameraUnavailable("0")
        assertFalse("Overlay must not appear when foreground is unknown", logic.isOverlayActive)

        // Camera releases before retry fires.
        logic.onCameraAvailable("0")

        // Verify removeCallbacks was called (cancels both deactivation and retry posts).
        verify(handler, atLeastOnce()).removeCallbacks(any())
        assertFalse("Overlay must remain hidden when camera released before retry", logic.isOverlayActive)
    }
}
