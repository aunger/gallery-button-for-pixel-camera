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
    private lateinit var foregroundDetector: ForegroundDetector
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
            debounceMs = 0L, // keep handler calls synchronous-looking in tests
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

    // ── DT-06a: UsageStats lag retry ────────────────────────────────────────

    /**
     * When the camera becomes unavailable but UsageStats hasn't caught up yet (foreground
     * package is not Pixel Camera), evaluateForeground() should schedule a retry runnable
     * via handler.postDelayed with ACTIVATION_RETRY_MS.
     */
    @Test
    fun `DT-06a retry scheduled when camera unavailable but foreground not yet detected`() {
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(null)

        cameraState.setCameraUnavailable("0")
        logic.evaluateForeground()

        verify(handler).postDelayed(any(), eq(Constants.ACTIVATION_RETRY_MS))
        assertFalse("Overlay should not be active yet", logic.isOverlayActive)
    }

    /**
     * A second evaluateForeground() call while a retry is already pending must not schedule
     * a second handler.postDelayed — the existing retry is reused (idempotent scheduling).
     */
    @Test
    fun `DT-06a retry is not double-scheduled on repeated evaluateForeground calls`() {
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(null)

        cameraState.setCameraUnavailable("0")
        logic.evaluateForeground()
        logic.evaluateForeground()
        logic.evaluateForeground()

        verify(handler, times(1)).postDelayed(any(), eq(Constants.ACTIVATION_RETRY_MS))
    }

    /**
     * When the retry fires and UsageStats now returns Pixel Camera as the foreground app,
     * the overlay is shown.
     */
    @Test
    fun `DT-06a overlay shows when retry fires and UsageStats has caught up`() {
        // First call: foreground not yet detected → retry scheduled
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(null)
        cameraState.setCameraUnavailable("0")
        logic.evaluateForeground()

        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(Constants.ACTIVATION_RETRY_MS))

        // UsageStats has now caught up
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)

        // Execute the retry runnable
        runnableCaptor.firstValue.run()

        assertTrue("Overlay should be active after retry succeeds", logic.isOverlayActive)
        verify(overlayManager).show()
    }

    /**
     * When evaluateForeground() succeeds immediately (Pixel Camera is already in the foreground),
     * no retry should be scheduled.
     */
    @Test
    fun `DT-06a no retry scheduled when Pixel Camera is already in foreground`() {
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)

        cameraState.setCameraUnavailable("0")
        logic.evaluateForeground()

        verify(handler, never()).postDelayed(any(), eq(Constants.ACTIVATION_RETRY_MS))
        assertTrue(logic.isOverlayActive)
    }

    /**
     * If the camera becomes available again before the retry fires, the pending retry should
     * be cancelled on reset() so it cannot trigger a stale activation.
     */
    @Test
    fun `DT-06a pending retry is cancelled on reset`() {
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(null)
        cameraState.setCameraUnavailable("0")
        logic.evaluateForeground()

        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(Constants.ACTIVATION_RETRY_MS))

        logic.reset()

        verify(handler).removeCallbacks(runnableCaptor.firstValue)
        assertFalse(logic.isOverlayActive)
    }

    /**
     * When usage-stats permission is revoked, any pending retry should be cancelled so the
     * retry cannot fire and attempt to show the overlay without permission.
     */
    @Test
    fun `DT-06a pending retry cancelled when usage stats permission is revoked`() {
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(null)
        cameraState.setCameraUnavailable("0")
        logic.evaluateForeground()

        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(Constants.ACTIVATION_RETRY_MS))

        // Revoke permission; evaluateForeground should cancel the retry
        usageStatsPermission = false
        logic.evaluateForeground()

        verify(handler).removeCallbacks(runnableCaptor.firstValue)
    }
}
