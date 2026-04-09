package com.gb4pc.e2e

import android.content.pm.PackageManager
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.gb4pc.Constants
import com.gb4pc.data.PrefsManager
import com.gb4pc.service.OverlayService
import org.junit.Assert.assertTrue
import org.junit.Assume
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * End-to-end tests for the overlay lifecycle against a real Pixel Camera process.
 *
 * Prerequisites:
 *   - Run on an emulator or device set up via scripts/setup-e2e-emulator.sh
 *   - Pixel Camera (com.google.android.GoogleCamera) must be installed
 *   - PACKAGE_USAGE_STATS and SYSTEM_ALERT_WINDOW must be granted (done by setup script)
 *
 * These tests exercise the real OverlayService, real ForegroundDetector (UsageStatsManager),
 * and real CameraManager.AvailabilityCallback — not OverlayServiceLogic wired by hand.
 *
 * Run with: ./gradlew connectedE2EAndroidTest
 */
@E2ETest
@RunWith(AndroidJUnit4::class)
class PixelCameraOverlayE2ETest {

    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context = instrumentation.targetContext
    private val uiAutomation = instrumentation.uiAutomation

    @Before
    fun preconditionCheck() {
        // Verify Pixel Camera is installed; skip gracefully if not.
        try {
            context.packageManager.getPackageInfo(PC_PACKAGE, 0)
        } catch (e: PackageManager.NameNotFoundException) {
            Assume.assumeTrue(
                "Pixel Camera ($PC_PACKAGE) is not installed. " +
                    "Install it via 'adb install e2e/pixel-camera.apk' before running E2E tests.",
                false
            )
        }

        // Ensure OverlayService is running with setup completed.
        val prefs = PrefsManager(context)
        prefs.isSetupCompleted = true
        prefs.isServiceEnabled = true
        OverlayService.start(context)

        // Allow the service time to register camera callbacks.
        Thread.sleep(1000)

        // Ensure PC is not running at test start.
        stopPC()
        Thread.sleep(500)
    }

    /**
     * Launching Pixel Camera's viewfinder triggers the CameraManager callback, which (after
     * UsageStats catches up) causes the overlay to appear.
     */
    @Test
    fun overlayAppearsWhenViewfinderOpens() {
        uiAutomation.executeShellCommand("am start -n $PC_ACTIVITY").close()

        val appeared = waitForCondition(timeoutMs = 5000L) { OverlayService.isOverlayActive }
        assertTrue("Overlay should appear within 5 s of launching Pixel Camera viewfinder", appeared)
    }

    /**
     * Sending Pixel Camera to the background releases the camera hardware. After the debounce
     * delay (CAMERA_DEBOUNCE_MS = 500 ms) the overlay should be hidden.
     */
    @Test
    fun overlayDisappearsWhenViewfinderCloses() {
        // Pre-condition: bring overlay up.
        uiAutomation.executeShellCommand("am start -n $PC_ACTIVITY").close()
        val appeared = waitForCondition(timeoutMs = 5000L) { OverlayService.isOverlayActive }
        Assume.assumeTrue("Pre-condition: overlay must be active with PC in foreground", appeared)

        // Send PC to background; camera is released.
        uiAutomation.executeShellCommand(
            "am start -a android.intent.action.MAIN -c android.intent.category.HOME"
        ).close()

        val disappeared = waitForCondition(timeoutMs = 5000L) { !OverlayService.isOverlayActive }
        assertTrue(
            "Overlay should disappear within 5 s after Pixel Camera viewfinder closes",
            disappeared
        )
    }

    /**
     * Regression test for the UsageStats-lag retry (DT-06a / Constants.ACTIVATION_RETRY_MS).
     *
     * When Pixel Camera starts, the CameraManager fires onCameraUnavailable almost immediately,
     * but UsageStatsManager may not reflect Pixel Camera as the foreground app for ~800 ms.
     * Without the retry in OverlayServiceLogic, the overlay never appears if the initial
     * evaluateForeground() call finds no foreground package.
     *
     * This test verifies the overlay still appears within ACTIVATION_RETRY_MS + 1 s, which is
     * only reliably achievable if the retry mechanism is in place.
     */
    @Test
    fun overlayAppearsAfterUsageStatsLag() {
        // Launch PC — camera unavailable fires quickly; UsageStats may lag behind.
        uiAutomation.executeShellCommand("am start -n $PC_ACTIVITY").close()

        // Generous window: ACTIVATION_RETRY_MS (1 s) + 1 s headroom for scheduling overhead.
        val timeoutMs = Constants.ACTIVATION_RETRY_MS + 1000L
        val appeared = waitForCondition(timeoutMs) { OverlayService.isOverlayActive }
        assertTrue(
            "Overlay should appear within ${timeoutMs} ms even when UsageStats lags behind " +
                "the camera callback (requires the ACTIVATION_RETRY_MS retry in OverlayServiceLogic)",
            appeared
        )
    }

    // ── Helpers ──────────────────────────────────────────────────────────────

    private fun stopPC() {
        uiAutomation.executeShellCommand("am force-stop $PC_PACKAGE").close()
    }

    private fun waitForCondition(timeoutMs: Long, condition: () -> Boolean): Boolean {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            if (condition()) return true
            Thread.sleep(100)
        }
        return condition()
    }

    companion object {
        private const val PC_PACKAGE = Constants.PIXEL_CAMERA_PACKAGE
        private const val PC_ACTIVITY = "$PC_PACKAGE/.CameraActivity"
    }
}
