package com.gb4pc.e2e

import android.content.pm.PackageManager
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.gb4pc.Constants
import com.gb4pc.data.PrefsManager
import com.gb4pc.service.OverlayService
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
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
 *
 * future-work: emulator tests use a mock-camera stub and cannot cover:
 *   - UsageStats lag (stub returns synthetic data; real app accumulates
 *     foreground-time across reboots and lifecycle transitions)
 *   - Camera-mode switching (Photo/Video/Portrait intent/result codes
 *     not exercised; transition-state UI bugs invisible)
 *   - PairIP / hardware-feature compatibility gating (stub bypasses
 *     device-compatibility checks that gate UI surfaces on real hardware)
 *   - Package-signature validation (stub installed under same package ID
 *     but not signed the same way as real Pixel Camera)
 * True E2E on a physical Pixel device is tracked in issue #15.
 */
@E2ETest
@RunWith(AndroidJUnit4::class)
class PixelCameraOverlayE2ETest {

    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context = instrumentation.targetContext
    private val uiAutomation = instrumentation.uiAutomation

    @Before
    fun preconditionCheck() {
        // Pixel Camera must be installed. Failing here (not skipping) is intentional:
        // the E2E suite should not silently pass on an emulator that lacks PC.
        // Set up the emulator with scripts/setup-e2e-emulator.sh before running these tests.
        try {
            context.packageManager.getPackageInfo(PC_PACKAGE, 0)
        } catch (e: PackageManager.NameNotFoundException) {
            fail(
                "Pixel Camera ($PC_PACKAGE) is not installed. " +
                    "Run 'scripts/setup-e2e-emulator.sh' (or 'adb install e2e/pixel-camera.apk') " +
                    "before executing the E2E suite."
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
        uiAutomation.executeShellCommand(
            "am start -a android.media.action.STILL_IMAGE_CAMERA -p $PC_PACKAGE"
        ).close()

        val appeared = waitForCondition(timeoutMs = 10000L) { OverlayService.isOverlayActive }
        assertTrue("Overlay should appear within 10 s of launching Pixel Camera viewfinder", appeared)
    }

    /**
     * Sending Pixel Camera to the background releases the camera hardware. After the debounce
     * delay (CAMERA_DEBOUNCE_MS = 500 ms) the overlay should be hidden.
     */
    @Test
    fun overlayDisappearsWhenViewfinderCloses() {
        // Pre-condition: bring overlay up. If it doesn't appear, that is itself a failure.
        uiAutomation.executeShellCommand(
            "am start -a android.media.action.STILL_IMAGE_CAMERA -p $PC_PACKAGE"
        ).close()
        val appeared = waitForCondition(timeoutMs = 10000L) { OverlayService.isOverlayActive }
        assertTrue("Pre-condition: overlay must appear within 10 s after launching PC", appeared)

        // Send PC to background; camera is released.
        uiAutomation.executeShellCommand(
            "am start -a android.intent.action.MAIN -c android.intent.category.HOME"
        ).close()

        val disappeared = waitForCondition(timeoutMs = 10000L) { !OverlayService.isOverlayActive }
        assertTrue(
            "Overlay should disappear within 10 s after Pixel Camera viewfinder closes",
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
        uiAutomation.executeShellCommand(
            "am start -a android.media.action.STILL_IMAGE_CAMERA -p $PC_PACKAGE"
        ).close()

        // Generous window: ACTIVATION_RETRY_MS (1 s) + 3 s headroom for scheduling overhead on
        // loaded CI runners. The retry fires at ~1 s; the extra slack avoids flakiness without
        // defeating the test's purpose (proving the retry fires at all).
        val timeoutMs = Constants.ACTIVATION_RETRY_MS + 3000L
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
    }
}
