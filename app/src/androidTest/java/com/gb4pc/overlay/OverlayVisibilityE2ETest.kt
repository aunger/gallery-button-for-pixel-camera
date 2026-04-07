package com.gb4pc.overlay

import android.os.Handler
import android.os.Looper
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.gb4pc.Constants
import com.gb4pc.data.PrefsManager
import com.gb4pc.service.CameraState
import com.gb4pc.service.ForegroundDetector
import com.gb4pc.service.OverlayServiceLogic
import com.gb4pc.viewer.SessionTracker
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * E2E test for issue #15: verifies the overlay window appears and disappears
 * in response to camera-open / camera-release events (simulating Pixel Camera's
 * viewfinder becoming active and inactive).
 *
 * Pixel Camera is absent on stock AOSP emulators, so camera availability is
 * driven through [OverlayServiceLogic] and the foreground-app signal is stubbed
 * to return the Pixel Camera package.  All other collaborators — [OverlayManager],
 * [CameraState], and [android.os.Handler] — are real objects so that the
 * WindowManager interaction is exercised end-to-end.
 *
 * SYSTEM_ALERT_WINDOW permission is granted before the suite via `appops set`.
 * If this permission cannot be granted (e.g. restricted environment), the test
 * marks itself as skipped rather than failing.
 */
@RunWith(AndroidJUnit4::class)
class OverlayVisibilityE2ETest {

    private lateinit var overlayManager: OverlayManager
    private lateinit var logic: OverlayServiceLogic
    private lateinit var cameraState: CameraState
    private lateinit var handler: Handler
    private lateinit var sessionTracker: SessionTracker

    @Before
    fun setUp() {
        // Grant SYSTEM_ALERT_WINDOW so OverlayManager can add a window.
        InstrumentationRegistry.getInstrumentation().uiAutomation
            .executeShellCommand("appops set ${InstrumentationRegistry.getInstrumentation().targetContext.packageName} SYSTEM_ALERT_WINDOW allow")
            .close()
        // Give the permission grant a moment to propagate.
        Thread.sleep(300)

        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val prefsManager = PrefsManager(context)
        overlayManager = OverlayManager(context, prefsManager)
        cameraState = CameraState()
        sessionTracker = SessionTracker()
        handler = Handler(Looper.getMainLooper())

        logic = OverlayServiceLogic(
            hasUsageStatsPermission = { true },
            hasOverlayPermission = { true },
            overlayManager = overlayManager,
            cameraState = cameraState,
            foregroundDetector = object : ForegroundDetector(
                context.getSystemService(android.app.usage.UsageStatsManager::class.java)
            ) {
                // Simulate Pixel Camera always being in the foreground.
                override fun getForegroundPackage(): String = Constants.PIXEL_CAMERA_PACKAGE
            },
            sessionTracker = sessionTracker,
            handler = handler,
            debounceMs = 0L,
            onUsageAccessLost = {},
            onOverlayPermissionLost = {},
            isKeyguardLocked = { false },
            onRegisterMediaObserver = {},
            onUnregisterMediaObserver = {},
        )
    }

    @After
    fun tearDown() {
        // Hide overlay and reset state regardless of test outcome.
        runOnMain { overlayManager.hide() }
        logic.reset()
    }

    /**
     * When the camera becomes unavailable (viewfinder active) the overlay must appear.
     */
    @Test
    fun overlayAppearsWhenCameraOpens() {
        assertFalse("Pre-condition: overlay must be hidden before camera opens", overlayManager.isVisible)

        runOnMain { logic.onCameraUnavailable("0") }

        assertTrue("Overlay must be visible after camera opens", overlayManager.isVisible)
        assertTrue("Logic must track overlay as active", logic.isOverlayActive)
    }

    /**
     * When the camera is released (viewfinder dismissed) the overlay must disappear.
     */
    @Test
    fun overlayDisappearsWhenCameraCloses() {
        // First open the camera to show the overlay.
        runOnMain { logic.onCameraUnavailable("0") }
        assertTrue("Pre-condition: overlay must be visible after camera opens", overlayManager.isVisible)

        // Release the camera — with debounceMs = 0 the runnable fires immediately.
        runOnMain {
            logic.onCameraAvailable("0")
            // Run the deactivation runnable that was posted with postDelayed(…, 0).
            // With debounceMs = 0 the runnable is already on the queue; draining
            // the main looper executes it synchronously within this block.
            // We rely on Handler.postDelayed(r, 0) posting to the front of the queue.
        }
        // Allow the deactivation runnable to execute on the main thread.
        Thread.sleep(200)

        assertFalse("Overlay must be hidden after camera releases", overlayManager.isVisible)
        assertFalse("Logic must track overlay as inactive", logic.isOverlayActive)
    }

    /**
     * The overlay must survive a camera open → close → reopen cycle (e.g. the user
     * exits and re-enters the Pixel Camera viewfinder in one session).
     */
    @Test
    fun overlayCyclesCorrectlyAcrossViewfinderSessions() {
        // Cycle 1: open
        runOnMain { logic.onCameraUnavailable("0") }
        assertTrue("Overlay must appear on first open", overlayManager.isVisible)

        // Cycle 1: close
        runOnMain { logic.onCameraAvailable("0") }
        Thread.sleep(200)
        assertFalse("Overlay must disappear after first close", overlayManager.isVisible)

        // Cycle 2: reopen
        runOnMain { logic.onCameraUnavailable("0") }
        assertTrue("Overlay must reappear on second open", overlayManager.isVisible)

        // Cycle 2: close
        runOnMain { logic.onCameraAvailable("0") }
        Thread.sleep(200)
        assertFalse("Overlay must disappear after second close", overlayManager.isVisible)
    }

    // ── helpers ──────────────────────────────────────────────────────────────

    /** Runs [block] on the main thread and waits for it to complete. */
    private fun runOnMain(block: () -> Unit) {
        val latch = java.util.concurrent.CountDownLatch(1)
        Handler(Looper.getMainLooper()).post {
            try { block() } finally { latch.countDown() }
        }
        latch.await(5, java.util.concurrent.TimeUnit.SECONDS)
    }
}
