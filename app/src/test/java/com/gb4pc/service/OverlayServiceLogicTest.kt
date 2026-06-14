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
    private var thumbnailObserverRegistered = false

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
        thumbnailObserverRegistered = false

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
            onRegisterThumbnailObserver = { thumbnailObserverRegistered = true },
            onUnregisterThumbnailObserver = { thumbnailObserverRegistered = false },
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
     * When the retry fires but UsageStats still has not caught up, the retry re-schedules itself
     * (UsageStats can lag the camera callback by more than one ACTIVATION_RETRY_MS interval). Each
     * fired retry that still finds no foreground schedules at most one follow-up, so the loop runs
     * at exactly 1 Hz (one pending postDelayed at a time) rather than fanning out.
     */
    @Test
    fun `DT-06a retry re-schedules once per firing when foreground still not detected`() {
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(null)
        cameraState.setCameraUnavailable("0")
        logic.evaluateForeground()

        // Fire the retry runnable
        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(Constants.ACTIVATION_RETRY_MS))
        runnableCaptor.firstValue.run()

        // Foreground still not detected -- exactly one follow-up retry is scheduled (2 total).
        verify(handler, times(2)).postDelayed(any(), eq(Constants.ACTIVATION_RETRY_MS))
        assertFalse("Overlay must not be active when foreground was never detected", logic.isOverlayActive)
    }

    /**
     * The self-rescheduling retry is bounded: across a single camera-open event it fires at most
     * ACTIVATION_RETRY_MAX_ATTEMPTS times when UsageStats never catches up, then stops. This proves
     * the loop cannot poll forever (e.g. if onCameraAvailable's cancel path is somehow missed).
     */
    @Test
    fun `DT-06a retry stops after ACTIVATION_RETRY_MAX_ATTEMPTS when foreground never detected`() {
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(null)
        cameraState.setCameraUnavailable("0")
        logic.evaluateForeground()

        val scheduled = drainActivationRetries()

        // The chain self-terminates after exactly ACTIVATION_RETRY_MAX_ATTEMPTS schedules.
        assertEquals(Constants.ACTIVATION_RETRY_MAX_ATTEMPTS, scheduled)
        assertFalse("Overlay must not be active when foreground was never detected", logic.isOverlayActive)
    }

    /**
     * A fresh camera-open event resets the retry attempt budget: after a sequence that exhausts the
     * retries (foreground never detected), releasing and re-opening the camera lets the retry run
     * the full ACTIVATION_RETRY_MAX_ATTEMPTS again.
     */
    @Test
    fun `DT-06a attempt budget resets on a new camera-open event`() {
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(null)

        // First sequence: exhaust the budget.
        cameraState.setCameraUnavailable("0")
        logic.evaluateForeground()
        val first = drainActivationRetries()
        assertEquals(Constants.ACTIVATION_RETRY_MAX_ATTEMPTS, first)

        // Camera released, then re-opened: this resets the budget via cancelActivationRetry().
        logic.onCameraAvailable("0")
        cameraState.setCameraUnavailable("0")
        logic.evaluateForeground()

        // The full budget is available again for the new camera-open event. Baseline past the
        // posts already fired by the first drain so only the second sequence's retries are counted.
        val second = drainActivationRetries(baseline = first)
        assertEquals(Constants.ACTIVATION_RETRY_MAX_ATTEMPTS, second)
    }

    /**
     * Runs the activation-retry chain to completion and returns the number of retries scheduled in
     * this drain (i.e. since [baseline] cumulative posts).
     *
     * Each fired retry that still finds no foreground package schedules the next one via a new
     * handler.postDelayed(). This drains that chain by, on each step, capturing every posted
     * runnable and running the latest one, until a step posts nothing new. The cumulative captor
     * count grows monotonically, so [baseline] lets a second drain in the same test ignore the
     * runnables already fired by an earlier drain. Bounded so a non-terminating loop fails the test.
     */
    private fun drainActivationRetries(baseline: Int = 0): Int {
        var total = baseline
        while (true) {
            val captor = argumentCaptor<Runnable>()
            verify(handler, atLeastOnce())
                .postDelayed(captor.capture(), eq(Constants.ACTIVATION_RETRY_MS))
            if (captor.allValues.size <= total) break  // no new retry was posted -> chain terminated
            captor.allValues.last().run()
            total = captor.allValues.size
            assertTrue(
                "Activation retry did not terminate within a bounded number of attempts",
                total - baseline <= Constants.ACTIVATION_RETRY_MAX_ATTEMPTS * 4
            )
        }
        return total - baseline
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

    /**
     * If the camera is released while a retry is pending (e.g. a non-Pixel-Camera app
     * briefly opened the camera), the retry should be cancelled so it doesn't fire stale.
     * Issue #89: the early-exit guard also ensures no deactivation is scheduled in this case
     * (overlay was never active, so there is nothing to deactivate).
     */
    @Test
    fun `DT-06a pending retry cancelled when camera released before retry fires`() {
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(null)
        logic.onCameraUnavailable("0")

        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(Constants.ACTIVATION_RETRY_MS))

        // Camera released before activation completed — overlay still inactive
        assertFalse("Pre-condition: overlay should not be active", logic.isOverlayActive)
        logic.onCameraAvailable("0")

        verify(handler).removeCallbacks(runnableCaptor.firstValue)
        // Issue #89: no deactivation should be scheduled — overlay was never active
        verify(handler, never()).postDelayed(any(), eq(0L))
        verify(handler, never()).postDelayed(any(), eq(Constants.CAMERA_DEBOUNCE_MS))
    }

    // ── Issue #89: skip deactivation when overlay is already inactive ────────

    /**
     * When a camera-now-available event arrives and the overlay was never activated,
     * no deactivation should be scheduled.
     */
    @Test
    fun `issue-89 onCameraAvailable does not schedule deactivation when overlay is inactive`() {
        // Overlay was never activated — simulate the initial-startup priming case
        assertFalse("Pre-condition: overlay should not be active", logic.isOverlayActive)

        logic.onCameraAvailable("0")

        verify(handler, never()).postDelayed(any(), any())
        assertFalse("Overlay should still be inactive", logic.isOverlayActive)
    }

    /**
     * When the last camera is released but the overlay is inactive, no deactivation runnable
     * fires, and overlayManager.hide() is never called.
     */
    @Test
    fun `issue-89 onCameraAvailable when all cameras released and overlay inactive skips deactivation`() {
        assertFalse("Pre-condition: overlay should not be active", logic.isOverlayActive)

        cameraState.setCameraUnavailable("0")
        logic.onCameraAvailable("0") // all cameras now available, but overlay was never shown

        verify(handler, never()).postDelayed(any(), any())
        verify(overlayManager, never()).hide()
    }

    // ── Issue #46: foreground-aware deactivation delay ──────────────────────

    /**
     * When all cameras become available and Pixel Camera is no longer the foreground app
     * (the user closed the camera app), deactivation should be scheduled with 0 ms delay —
     * no debounce needed for a true app-close.
     */
    @Test
    fun `issue-46 deactivation uses 0ms delay when Pixel Camera is not foreground on camera release`() {
        // Activate the overlay while Pixel Camera is in the foreground
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)
        logic.onCameraUnavailable("0")
        assertTrue("Pre-condition: overlay should be active", logic.isOverlayActive)

        // User closed Pixel Camera; UsageStats now shows a different app
        whenever(foregroundDetector.getForegroundPackage()).thenReturn("com.example.otherapp")

        logic.onCameraAvailable("0")

        verify(handler).postDelayed(any(), eq(0L))
    }

    /**
     * When all cameras become available and Pixel Camera is still the foreground app
     * (transient camera switch between lenses), deactivation should be scheduled with the
     * configured debounceMs so the overlay is not prematurely hidden.
     */
    @Test
    fun `issue-46 deactivation uses debounceMs delay when Pixel Camera is still foreground on camera switch`() {
        val debounce = 50L
        val logicWithDebounce = OverlayServiceLogic(
            hasUsageStatsPermission = { true },
            hasOverlayPermission = { true },
            overlayManager = overlayManager,
            cameraState = CameraState(),
            foregroundDetector = foregroundDetector,
            sessionTracker = sessionTracker,
            handler = handler,
            debounceMs = debounce,
            onUsageAccessLost = {},
            onOverlayPermissionLost = {},
            isKeyguardLocked = { false },
            onRegisterMediaObserver = {},
            onUnregisterMediaObserver = {},
        )

        // Activate the overlay
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)
        logicWithDebounce.onCameraUnavailable("0")
        assertTrue("Pre-condition: overlay should be active", logicWithDebounce.isOverlayActive)

        // Camera becomes available while Pixel Camera is still in the foreground (camera switch)
        logicWithDebounce.onCameraAvailable("0")

        verify(handler).postDelayed(any(), eq(debounce))
    }

    // ── Issue #55: focusable overlay focus-change behaviour ─────────────────

    /**
     * When the overlay window loses focus (task-switcher or notification shade is shown),
     * the overlay is hidden and isOverlayActive becomes false.
     */
    @Test
    fun `onOverlayFocusLost hides overlay and marks it inactive`() {
        // Activate the overlay first
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)
        logic.showOverlay()
        assertTrue("Pre-condition: overlay should be active", logic.isOverlayActive)

        logic.onOverlayFocusLost()

        verify(overlayManager).hide()
        assertFalse("Overlay should be inactive after focus loss", logic.isOverlayActive)
    }

    /**
     * onOverlayFocusLost is idempotent: a second call while already inactive is a no-op.
     */
    @Test
    fun `onOverlayFocusLost is a no-op when overlay is not active`() {
        assertFalse("Pre-condition: overlay should not be active", logic.isOverlayActive)

        logic.onOverlayFocusLost()

        verify(overlayManager, never()).hide()
        assertFalse(logic.isOverlayActive)
    }

    /**
     * When the overlay window regains focus (camera app is back in front),
     * the overlay is re-shown and isOverlayActive becomes true.
     */
    @Test
    fun `onOverlayFocusGained shows overlay and marks it active`() {
        assertFalse("Pre-condition: overlay should not be active", logic.isOverlayActive)

        logic.onOverlayFocusGained()

        verify(overlayManager).show()
        assertTrue("Overlay should be active after focus gained", logic.isOverlayActive)
    }

    /**
     * onOverlayFocusGained is idempotent: a second call while already active is a no-op.
     */
    @Test
    fun `onOverlayFocusGained is a no-op when overlay is already active`() {
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)
        logic.showOverlay()
        assertTrue("Pre-condition: overlay should be active", logic.isOverlayActive)

        logic.onOverlayFocusGained()

        // show() called once (by showOverlay), not again by onOverlayFocusGained
        verify(overlayManager, times(1)).show()
    }

    /**
     * A full focus-lost → focus-gained cycle hides and then re-shows the overlay.
     */
    @Test
    fun `focus lost then gained cycle hides and re-shows overlay`() {
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)
        logic.showOverlay()
        assertTrue("Pre-condition: overlay should be active", logic.isOverlayActive)

        logic.onOverlayFocusLost()
        assertFalse("Overlay should be hidden after focus loss", logic.isOverlayActive)
        verify(overlayManager).hide()

        logic.onOverlayFocusGained()
        assertTrue("Overlay should be visible after focus regained", logic.isOverlayActive)
        verify(overlayManager, times(2)).show() // once for showOverlay(), once for onOverlayFocusGained()
    }

    /**
     * onOverlayFocusGained does not show the overlay when overlay permission has been revoked.
     */
    @Test
    fun `onOverlayFocusGained does not show overlay when overlay permission is missing`() {
        assertFalse("Pre-condition: overlay should not be active", logic.isOverlayActive)
        overlayPermission = false

        logic.onOverlayFocusGained()

        verify(overlayManager, never()).show()
        assertFalse(logic.isOverlayActive)
        assertEquals("Overlay-permission-lost notification should fire", 1, overlayLostCount)
    }

    // ── Issue #92: focusable-overlay teardown correctness ────────────────────

    /**
     * Issue #92: onOverlayFocusLost must cancel any pending camera-available deactivation
     * runnable before hiding the overlay. Without this, the deactivation runnable fires after
     * focus-loss has already hidden the overlay, calling hideOverlayAndCleanup() a second time
     * — double-firing onOverlayStateChanged and incorrectly unregistering the thumbnail
     * observer again.
     */
    @Test
    fun `issue-92 onOverlayFocusLost cancels pending deactivation runnable`() {
        // Activate the overlay
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)
        logic.showOverlay()
        assertTrue("Pre-condition: overlay should be active", logic.isOverlayActive)

        // Schedule a deactivation (simulates onCameraAvailable firing just before focus loss)
        logic.scheduleDeactivation(50L)
        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(50L))

        // Focus lost — must cancel the pending deactivation
        logic.onOverlayFocusLost()

        verify(handler).removeCallbacks(runnableCaptor.firstValue)
        assertFalse("Overlay should be inactive after focus loss", logic.isOverlayActive)
    }

    /**
     * Issue #92: onOverlayFocusLost must call hideOverlayAndCleanup() (not a bare hide()) so
     * the thumbnail observer is unregistered. Without this, when the task switcher covers
     * Pixel Camera the thumbnail observer leaks until the camera-available deactivation fires.
     */
    @Test
    fun `issue-92 onOverlayFocusLost unregisters thumbnail observer`() {
        // Activate the overlay — this registers the thumbnail observer
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)
        logic.showOverlay()
        assertTrue("Pre-condition: thumbnail observer should be registered", thumbnailObserverRegistered)

        logic.onOverlayFocusLost()

        assertFalse("Thumbnail observer must be unregistered on focus loss (Issue #92)", thumbnailObserverRegistered)
    }

    /**
     * Issue #92: onOverlayFocusLost must end an active secure session (via hideOverlayAndCleanup).
     * If the overlay was activated while the device was locked and the session is still active
     * when focus is lost, the session and media observer must be torn down.
     */
    @Test
    fun `issue-92 onOverlayFocusLost ends active secure session`() {
        // Activate via lock-screen bypass — starts a session immediately
        keyguardLocked = true
        cameraState.setCameraUnavailable("0")
        logic.evaluateForeground()
        assertTrue("Pre-condition: overlay should be active", logic.isOverlayActive)
        assertTrue("Pre-condition: media observer should be registered", mediaObserverRegistered)
        whenever(sessionTracker.isSessionActive).thenReturn(true)

        logic.onOverlayFocusLost()

        assertFalse("Overlay should be inactive after focus loss", logic.isOverlayActive)
        verify(sessionTracker).endSession()
        assertFalse("Media observer must be unregistered when session ends (Issue #92)", mediaObserverRegistered)
    }

    /**
     * Issue #92: onOverlayFocusGained must re-register the thumbnail observer so that
     * subsequent photo thumbnails are displayed in the overlay button after focus is regained.
     * The original implementation omitted this call, leaving the observer unregistered until
     * the next full showOverlay() activation.
     */
    @Test
    fun `issue-92 onOverlayFocusGained re-registers thumbnail observer`() {
        // Show the overlay (registers thumbnail observer), then lose focus (unregisters it)
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)
        logic.showOverlay()
        assertTrue("Pre-condition: thumbnail observer should be registered", thumbnailObserverRegistered)

        logic.onOverlayFocusLost()
        assertFalse("Thumbnail observer should be unregistered after focus loss", thumbnailObserverRegistered)

        // Regain focus — thumbnail observer must be re-registered
        logic.onOverlayFocusGained()
        assertTrue("Thumbnail observer must be re-registered on focus gained (Issue #92)", thumbnailObserverRegistered)
    }

    /**
     * Issue #92 (lock-screen): when the device is locked at focus-gained time,
     * onOverlayFocusGained must mirror showOverlay()'s lock-screen behaviour — start the
     * secure session and re-register the media observer so newly captured photos update the
     * thumbnail button.  Without this, a focus-lost/focus-gained cycle on a locked device
     * would re-show the overlay but leave the media observer absent.
     */
    @Test
    fun `issue-92 onOverlayFocusGained on locked device starts session and registers media observer`() {
        // Activate via lock-screen bypass, then lose focus (tears down session + observers)
        keyguardLocked = true
        cameraState.setCameraUnavailable("0")
        logic.evaluateForeground()
        assertTrue("Pre-condition: overlay should be active", logic.isOverlayActive)
        verify(sessionTracker).startSession()
        assertTrue("Pre-condition: media observer should be registered", mediaObserverRegistered)
        whenever(sessionTracker.isSessionActive).thenReturn(true)

        logic.onOverlayFocusLost()
        assertFalse("Pre-condition: overlay should be hidden after focus loss", logic.isOverlayActive)
        assertFalse("Pre-condition: media observer should be unregistered after focus loss", mediaObserverRegistered)

        // Regain focus on a still-locked device — session and media observer must be restored
        logic.onOverlayFocusGained()

        assertTrue("Overlay should be active after focus regained", logic.isOverlayActive)
        verify(sessionTracker, times(2)).startSession()  // once on activation, once on focus regained
        assertTrue("Media observer must be re-registered on focus gained while locked (Issue #92)", mediaObserverRegistered)
    }

    // ── Issue #81: lock-screen bypass ───────────────────────────────────────

    /**
     * When the device is locked and a camera is in use, evaluateForeground() must activate
     * the overlay without consulting UsageStats (which does not emit MOVE_TO_FOREGROUND
     * events while the device is locked).
     */
    @Test
    fun `issue-81 lock-screen bypass activates overlay when device is locked and camera in use`() {
        keyguardLocked = true
        cameraState.setCameraUnavailable("0")

        logic.evaluateForeground()

        assertTrue("Overlay should be active via lock-screen bypass", logic.isOverlayActive)
        verify(overlayManager).show()
        // UsageStats should NOT be consulted — foregroundDetector must not be called
        verify(foregroundDetector, never()).getForegroundPackage()
    }

    /**
     * The lock-screen bypass starts a secure session immediately, since the device is already
     * locked when the overlay activates.
     */
    @Test
    fun `issue-81 lock-screen bypass starts secure session immediately`() {
        keyguardLocked = true
        cameraState.setCameraUnavailable("0")

        logic.evaluateForeground()

        verify(sessionTracker).startSession()
        assertTrue("Media observer should be registered", mediaObserverRegistered)
    }

    /**
     * The lock-screen bypass is idempotent: a second evaluateForeground() while the overlay
     * is already active must not activate it a second time, and must not consult UsageStats
     * (which returns null on a locked device and could cause incorrect side-effects).
     */
    @Test
    fun `issue-81 lock-screen bypass is idempotent when overlay is already active`() {
        keyguardLocked = true
        cameraState.setCameraUnavailable("0")

        logic.evaluateForeground()
        assertTrue("Pre-condition: overlay should be active", logic.isOverlayActive)

        logic.evaluateForeground()

        // show() must be called exactly once (not twice)
        verify(overlayManager, times(1)).show()
        // UsageStats must never be consulted on a locked device — getForegroundPackage() returns
        // null on lock screen and calling it would be both wrong and misleading.
        verify(foregroundDetector, never()).getForegroundPackage()
    }

    /**
     * The lock-screen bypass does NOT fire when the device is unlocked — normal UsageStats
     * detection applies instead.
     */
    @Test
    fun `issue-81 lock-screen bypass does not fire when device is unlocked`() {
        keyguardLocked = false
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)
        cameraState.setCameraUnavailable("0")

        logic.evaluateForeground()

        // Normal path: UsageStats IS consulted
        verify(foregroundDetector, atLeastOnce()).getForegroundPackage()
    }

    /**
     * The lock-screen bypass does NOT fire when the keyguard is locked but no camera is in use.
     * Without this guard, locking the screen while no camera is active could incorrectly show
     * the overlay.
     */
    @Test
    fun `issue-81 lock-screen bypass does not fire when no camera is in use`() {
        keyguardLocked = true
        // No camera is unavailable — cameraState.anyCameraUnavailable() == false

        logic.evaluateForeground()

        assertFalse("Overlay should not be shown when no camera is in use", logic.isOverlayActive)
        verify(overlayManager, never()).show()
    }

    /**
     * When the lock-screen bypass activates the overlay and the camera is later released,
     * the deactivation path cleans up correctly (session ended, observer unregistered).
     */
    @Test
    fun `issue-81 deactivation after lock-screen activation ends session and unregisters observer`() {
        keyguardLocked = true
        cameraState.setCameraUnavailable("0")
        logic.evaluateForeground()
        assertTrue("Pre-condition: overlay should be active", logic.isOverlayActive)
        whenever(sessionTracker.isSessionActive).thenReturn(true)

        // Camera released
        logic.onCameraAvailable("0")

        // Capture and run the deactivation runnable
        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), any())
        runnableCaptor.firstValue.run()

        verify(overlayManager).hide()
        assertFalse("Overlay should be inactive after deactivation", logic.isOverlayActive)
        verify(sessionTracker).endSession()
        assertFalse("Media observer should be unregistered", mediaObserverRegistered)
    }

    /**
     * On the lock screen, when the camera is released, deactivation must use debounceMs
     * (not 0 ms). UsageStats returns null on a locked device, so without this guard the
     * deactivation code would see isPixelCameraPackage=false and use 0 ms — prematurely
     * hiding the overlay during a transient lens switch.
     *
     * This test uses a non-zero debounceMs to distinguish the two delay values.
     */
    @Test
    fun `issue-81 deactivation uses debounceMs on lock screen, not 0ms`() {
        val debounce = 50L
        val logicWithDebounce = OverlayServiceLogic(
            hasUsageStatsPermission = { true },
            hasOverlayPermission = { true },
            overlayManager = overlayManager,
            cameraState = CameraState(),
            foregroundDetector = foregroundDetector,
            sessionTracker = sessionTracker,
            handler = handler,
            debounceMs = debounce,
            onUsageAccessLost = {},
            onOverlayPermissionLost = {},
            isKeyguardLocked = { true },  // device is locked
            onRegisterMediaObserver = {},
            onUnregisterMediaObserver = {},
        )

        // Activate via lock-screen bypass
        logicWithDebounce.onCameraUnavailable("0")
        assertTrue("Pre-condition: overlay should be active via lock-screen bypass", logicWithDebounce.isOverlayActive)

        // Camera released — on lock screen, getForegroundPackage() returns null, so without
        // the fix the delay would be 0 ms; with the fix it must be debounceMs.
        logicWithDebounce.onCameraAvailable("0")

        verify(handler).postDelayed(any(), eq(debounce))
        // UsageStats must not be consulted during deactivation on a locked device
        verify(foregroundDetector, never()).getForegroundPackage()
    }

    // ── Issue #91: hide overlay early on gallery launch ─────────────────────

    /**
     * When the gallery is launched and PC is no longer in the foreground, the overlay is
     * hidden immediately — without waiting for the camera-available event.
     */
    @Test
    fun `issue-91 overlay hidden immediately when PC not in foreground after gallery launch`() {
        // Activate the overlay
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)
        logic.showOverlay()
        assertTrue("Pre-condition: overlay should be active", logic.isOverlayActive)

        // Gallery launched; PC is gone (gallery took over the foreground)
        whenever(foregroundDetector.getForegroundPackage()).thenReturn("com.google.android.apps.photos")

        logic.onGalleryLaunched()

        verify(overlayManager).hide()
        assertFalse("Overlay should be hidden immediately when PC left the foreground", logic.isOverlayActive)
        // No deferred runnable should be scheduled
        verify(handler, never()).postDelayed(any(), any())
    }

    /**
     * When the gallery is launched but PC is still in the foreground (e.g. split-screen),
     * a re-check is scheduled after debounceMs — no immediate hide.
     */
    @Test
    fun `issue-91 recheck scheduled when PC still in foreground after gallery launch`() {
        val debounce = 50L
        val logicWithDebounce = OverlayServiceLogic(
            hasUsageStatsPermission = { true },
            hasOverlayPermission = { true },
            overlayManager = overlayManager,
            cameraState = CameraState(),
            foregroundDetector = foregroundDetector,
            sessionTracker = sessionTracker,
            handler = handler,
            debounceMs = debounce,
            onUsageAccessLost = {},
            onOverlayPermissionLost = {},
            isKeyguardLocked = { false },
            onRegisterMediaObserver = {},
            onUnregisterMediaObserver = {},
        )

        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)
        logicWithDebounce.showOverlay()
        assertTrue("Pre-condition: overlay should be active", logicWithDebounce.isOverlayActive)

        // Gallery launched; PC is still in foreground (split-screen)
        logicWithDebounce.onGalleryLaunched()

        // Overlay must NOT be hidden yet
        verify(overlayManager, never()).hide()
        assertTrue("Overlay should still be active while PC is in foreground", logicWithDebounce.isOverlayActive)
        // Re-check runnable must be scheduled with debounceMs
        verify(handler).postDelayed(any(), eq(debounce))
    }

    /**
     * When the re-check fires and PC has left the foreground, the overlay is hidden.
     */
    @Test
    fun `issue-91 recheck hides overlay when PC left foreground by recheck time`() {
        val debounce = 50L
        val logicWithDebounce = OverlayServiceLogic(
            hasUsageStatsPermission = { true },
            hasOverlayPermission = { true },
            overlayManager = overlayManager,
            cameraState = CameraState(),
            foregroundDetector = foregroundDetector,
            sessionTracker = sessionTracker,
            handler = handler,
            debounceMs = debounce,
            onUsageAccessLost = {},
            onOverlayPermissionLost = {},
            isKeyguardLocked = { false },
            onRegisterMediaObserver = {},
            onUnregisterMediaObserver = {},
        )

        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)
        logicWithDebounce.showOverlay()

        // Gallery launched; PC still in FG → re-check scheduled
        logicWithDebounce.onGalleryLaunched()

        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(debounce))

        // By re-check time, PC has left the foreground
        whenever(foregroundDetector.getForegroundPackage()).thenReturn("com.google.android.apps.photos")
        runnableCaptor.firstValue.run()

        verify(overlayManager).hide()
        assertFalse("Overlay should be hidden after re-check confirms PC is gone", logicWithDebounce.isOverlayActive)
    }

    /**
     * When the re-check fires and PC is still in the foreground (it stayed there), the
     * overlay is NOT hidden — the user is using split-screen or dual-screen.
     */
    @Test
    fun `issue-91 recheck does not hide overlay when PC still in foreground at recheck time`() {
        val debounce = 50L
        val logicWithDebounce = OverlayServiceLogic(
            hasUsageStatsPermission = { true },
            hasOverlayPermission = { true },
            overlayManager = overlayManager,
            cameraState = CameraState(),
            foregroundDetector = foregroundDetector,
            sessionTracker = sessionTracker,
            handler = handler,
            debounceMs = debounce,
            onUsageAccessLost = {},
            onOverlayPermissionLost = {},
            isKeyguardLocked = { false },
            onRegisterMediaObserver = {},
            onUnregisterMediaObserver = {},
        )

        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)
        logicWithDebounce.showOverlay()

        // Gallery launched; PC still in FG → re-check scheduled
        logicWithDebounce.onGalleryLaunched()

        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(debounce))

        // PC is still in foreground at re-check time
        runnableCaptor.firstValue.run()

        verify(overlayManager, never()).hide()
        assertTrue("Overlay should remain active when PC is still in foreground", logicWithDebounce.isOverlayActive)
    }

    /**
     * Immediate hide on gallery launch must fully tear down: end the secure session and
     * unregister both the thumbnail and media ContentObservers.  Without this, a session
     * opened on the lock screen would stay alive after the gallery becomes visible.
     */
    @Test
    fun `issue-91 immediate hide on gallery launch ends session and unregisters observers`() {
        // Activate the overlay with a live session (device was locked at activation time)
        keyguardLocked = true
        cameraState.setCameraUnavailable("0")
        logic.evaluateForeground()
        assertTrue("Pre-condition: overlay should be active", logic.isOverlayActive)
        assertTrue("Pre-condition: thumbnail observer should be registered", thumbnailObserverRegistered)
        assertTrue("Pre-condition: media observer should be registered", mediaObserverRegistered)
        whenever(sessionTracker.isSessionActive).thenReturn(true)

        // Gallery launched; PC is gone
        keyguardLocked = false
        whenever(foregroundDetector.getForegroundPackage()).thenReturn("com.google.android.apps.photos")
        logic.onGalleryLaunched()

        assertFalse("Overlay should be hidden", logic.isOverlayActive)
        assertFalse("Thumbnail observer should be unregistered after gallery-launch hide", thumbnailObserverRegistered)
        verify(sessionTracker).endSession()
        assertFalse("Media observer should be unregistered after session ends", mediaObserverRegistered)
    }

    /**
     * Deferred recheck hide on gallery launch must also fully tear down: end the secure session
     * and unregister both ContentObservers — same requirement as the immediate-hide path.
     */
    @Test
    fun `issue-91 deferred recheck hide ends session and unregisters observers`() {
        var thumbnailRegistered = false
        var mediaRegistered = false
        val debounce = 50L
        val logicWithSession = OverlayServiceLogic(
            hasUsageStatsPermission = { true },
            hasOverlayPermission = { true },
            overlayManager = overlayManager,
            cameraState = CameraState(),
            foregroundDetector = foregroundDetector,
            sessionTracker = sessionTracker,
            handler = handler,
            debounceMs = debounce,
            onUsageAccessLost = {},
            onOverlayPermissionLost = {},
            isKeyguardLocked = { true },   // locked so session starts on activation
            onRegisterMediaObserver = { mediaRegistered = true },
            onUnregisterMediaObserver = { mediaRegistered = false },
            onRegisterThumbnailObserver = { thumbnailRegistered = true },
            onUnregisterThumbnailObserver = { thumbnailRegistered = false },
        )

        // Activate via lock-screen bypass — starts a session immediately
        logicWithSession.onCameraUnavailable("0")
        assertTrue("Pre-condition: overlay active", logicWithSession.isOverlayActive)
        assertTrue("Pre-condition: thumbnail observer registered", thumbnailRegistered)
        assertTrue("Pre-condition: media observer registered", mediaRegistered)
        whenever(sessionTracker.isSessionActive).thenReturn(true)

        // PC is still in foreground at gallery launch → recheck scheduled
        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)
        logicWithSession.onGalleryLaunched()
        verify(overlayManager, never()).hide()  // not hidden yet

        // Capture the recheck runnable; PC has left by the time it fires
        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler, atLeastOnce()).postDelayed(runnableCaptor.capture(), eq(debounce))
        whenever(foregroundDetector.getForegroundPackage()).thenReturn("com.google.android.apps.photos")
        runnableCaptor.lastValue.run()

        assertFalse("Overlay should be hidden after recheck", logicWithSession.isOverlayActive)
        assertFalse("Thumbnail observer should be unregistered after recheck hide", thumbnailRegistered)
        verify(sessionTracker).endSession()
        assertFalse("Media observer should be unregistered after session ends", mediaRegistered)
    }

    /**
     * onGalleryLaunched() is a no-op when the overlay is not active.
     */
    @Test
    fun `issue-91 onGalleryLaunched is no-op when overlay is not active`() {
        assertFalse("Pre-condition: overlay should not be active", logic.isOverlayActive)

        logic.onGalleryLaunched()

        verify(overlayManager, never()).hide()
        verify(foregroundDetector, never()).getForegroundPackage()
        verify(handler, never()).postDelayed(any(), any())
    }

    /**
     * Pending gallery-launch re-check is cancelled on reset() so it cannot fire stale.
     */
    @Test
    fun `issue-91 gallery-launch recheck cancelled on reset`() {
        val debounce = 50L
        val logicWithDebounce = OverlayServiceLogic(
            hasUsageStatsPermission = { true },
            hasOverlayPermission = { true },
            overlayManager = overlayManager,
            cameraState = CameraState(),
            foregroundDetector = foregroundDetector,
            sessionTracker = sessionTracker,
            handler = handler,
            debounceMs = debounce,
            onUsageAccessLost = {},
            onOverlayPermissionLost = {},
            isKeyguardLocked = { false },
            onRegisterMediaObserver = {},
            onUnregisterMediaObserver = {},
        )

        whenever(foregroundDetector.getForegroundPackage()).thenReturn(Constants.PIXEL_CAMERA_PACKAGE)
        logicWithDebounce.showOverlay()

        // Gallery launched; PC still in FG → re-check scheduled
        logicWithDebounce.onGalleryLaunched()

        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(debounce))

        logicWithDebounce.reset()

        verify(handler).removeCallbacks(runnableCaptor.firstValue)
    }
}
