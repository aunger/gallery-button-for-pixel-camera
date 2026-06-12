package com.gb4pc.e2e

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.gb4pc.Constants
import com.gb4pc.service.OverlayService
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
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

    @get:Rule
    val screenshotRule = ScreenshotTestRule()

    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val fixture = E2EFixture(
        context = instrumentation.targetContext,
        uiAutomation = instrumentation.uiAutomation,
    )

    @Before
    fun setUp() = fixture.setUp()

    /**
     * Launching Pixel Camera's viewfinder triggers the CameraManager callback, which (after
     * UsageStats catches up) causes the overlay to appear.
     */
    @Test
    fun overlayAppearsWhenViewfinderOpens() {
        fixture.launchPixelCamera()

        val appeared = fixture.waitForCondition(timeoutMs = 30000L) { OverlayService.isOverlayActive }
        assertTrue("Overlay should appear within 30 s of launching Pixel Camera viewfinder", appeared)
    }

    /**
     * Sending Pixel Camera to the background releases the camera hardware. After the debounce
     * delay (CAMERA_DEBOUNCE_MS = 500 ms) the overlay should be hidden.
     */
    @Test
    fun overlayDisappearsWhenViewfinderCloses() {
        // Pre-condition: bring overlay up. If it doesn't appear, that is itself a failure.
        fixture.launchPixelCamera()
        val appeared = fixture.waitForCondition(timeoutMs = 10000L) { OverlayService.isOverlayActive }
        assertTrue("Pre-condition: overlay must appear within 10 s after launching PC", appeared)

        // Send PC to background; camera is released.
        fixture.goHome()

        val disappeared = fixture.waitForCondition(timeoutMs = 10000L) { !OverlayService.isOverlayActive }
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
     * This test verifies the overlay still appears within camera-open latency +
     * ACTIVATION_RETRY_MS + headroom, which is only reliably achievable if the retry
     * mechanism is in place.
     */
    @Test
    fun overlayAppearsAfterUsageStatsLag() {
        // Launch PC — camera unavailable fires quickly; UsageStats may lag behind.
        fixture.launchPixelCamera()

        // Generous window: camera-open latency + ACTIVATION_RETRY_MS (1 s) + headroom for
        // scheduling overhead on loaded CI runners.
        //
        // waitForOverlayActive()'s 20 s default budgets ~9 s for camera open plus up to 1 s for
        // UsageStats detection on CI emulators. This test additionally needs the
        // ACTIVATION_RETRY_MS (1 s) retry delay to elapse *after* that initial detection window,
        // since the retry only fires once the first evaluateForeground() finds no foreground
        // package. Using only ACTIVATION_RETRY_MS + 6 s (7 s total) was tighter than the 9 s
        // camera-open figure documented elsewhere and flaked under CI load (issue #249).
        //
        // 9 s camera-open + ACTIVATION_RETRY_MS (1 s) + 5 s headroom = 15 s. The extra slack
        // avoids flakiness without defeating the test's purpose (proving the retry fires at all).
        val timeoutMs = 9000L + Constants.ACTIVATION_RETRY_MS + 5000L
        val appeared = fixture.waitForCondition(timeoutMs) { OverlayService.isOverlayActive }
        assertTrue(
            "Overlay should appear within ${timeoutMs} ms even when UsageStats lags behind " +
                "the camera callback (requires the ACTIVATION_RETRY_MS retry in OverlayServiceLogic)",
            appeared
        )
    }
}
