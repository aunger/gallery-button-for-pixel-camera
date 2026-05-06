package com.gb4pc.e2e

import android.app.UiAutomation
import android.content.Context
import android.content.pm.PackageManager
import com.gb4pc.Constants
import com.gb4pc.data.PrefsManager
import com.gb4pc.service.OverlayService
import org.junit.Assert.fail

/**
 * Shared setup, teardown, and Pixel-Camera shell-command helpers for E2E tests.
 *
 * E2E tests run against a real OverlayService and the mock-camera APK installed under the
 * Pixel Camera package name (see [Constants.PIXEL_CAMERA_PACKAGE]). Every E2E test needs
 * the same boilerplate:
 *
 *   1. Verify the mock-camera APK is installed; fail loudly otherwise. (Silent skip would
 *      let an unconfigured emulator hide real regressions.)
 *   2. Mark setup as completed and enable the service in PrefsManager.
 *   3. Start OverlayService and wait for camera-availability callbacks to register.
 *   4. Force-stop Pixel Camera so each test starts from a known state.
 *
 * This fixture centralises that boilerplate so adding a new E2E test class is roughly
 *   `private val fixture = E2EFixture(...)`
 *   `@Before fun setUp() = fixture.setUp()`
 *
 * The shell-command helpers (`launchPixelCamera`, `goHome`, `stopPixelCamera`) and the
 * polling helper (`waitForCondition`) live here too — they are used by every E2E test
 * and have no other natural home.
 */
class E2EFixture(
    private val context: Context,
    private val uiAutomation: UiAutomation,
) {
    private val pcPackage = Constants.PIXEL_CAMERA_PACKAGE

    /**
     * Run from `@Before`. Performs all preconditions; tests that pass `setUp()` are
     * guaranteed: Pixel Camera installed, OverlayService running and registered, PC
     * not currently running.
     */
    fun setUp() {
        ensurePixelCameraInstalled()
        startServiceWithSetupCompleted()
        // Allow the service time to register camera callbacks.
        Thread.sleep(1000)
        // Ensure PC is not running at test start.
        stopPixelCamera()
        Thread.sleep(500)
    }

    fun launchPixelCamera() {
        uiAutomation.executeShellCommand(
            "am start -a android.media.action.STILL_IMAGE_CAMERA -p $pcPackage"
        ).close()
    }

    fun goHome() {
        uiAutomation.executeShellCommand(
            "am start -a android.intent.action.MAIN -c android.intent.category.HOME"
        ).close()
    }

    fun stopPixelCamera() {
        uiAutomation.executeShellCommand("am force-stop $pcPackage").close()
    }

    /**
     * Polls [condition] every 100 ms until it returns true or [timeoutMs] elapses. Returns
     * the final value of [condition].
     */
    fun waitForCondition(timeoutMs: Long, condition: () -> Boolean): Boolean {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            if (condition()) return true
            Thread.sleep(100)
        }
        return condition()
    }

    private fun ensurePixelCameraInstalled() {
        try {
            context.packageManager.getPackageInfo(pcPackage, 0)
        } catch (e: PackageManager.NameNotFoundException) {
            fail(
                "Pixel Camera ($pcPackage) is not installed. " +
                    "Run 'scripts/setup-e2e-emulator.sh' (or 'adb install e2e/pixel-camera.apk') " +
                    "before executing the E2E suite."
            )
        }
    }

    private fun startServiceWithSetupCompleted() {
        val prefs = PrefsManager(context)
        prefs.isSetupCompleted = true
        prefs.isServiceEnabled = true
        OverlayService.start(context)
    }
}
