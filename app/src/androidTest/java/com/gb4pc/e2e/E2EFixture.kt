package com.gb4pc.e2e

import android.app.KeyguardManager
import android.app.UiAutomation
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.media.MediaScannerConnection
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.UiDevice
import com.gb4pc.Constants
import com.gb4pc.data.AspectRatioUtil
import com.gb4pc.data.PrefsManager
import com.gb4pc.service.OverlayService
import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

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

    // ── Phase 4 extensions ────────────────────────────────────────────────────

    /**
     * Seeds PrefsManager with a gallery package selection and marks setup complete.
     *
     * Sets [PrefsManager.galleryPackage] = [pkg], [PrefsManager.isSetupCompleted] = true,
     * and [PrefsManager.isServiceEnabled] = true. All other prefs are left untouched.
     */
    fun seedGalleryPrefs(pkg: String) {
        val prefs = PrefsManager(context)
        prefs.galleryPackage = pkg
        prefs.isSetupCompleted = true
        prefs.isServiceEnabled = true
    }

    /**
     * Deletes all images from the external MediaStore and asserts the roll is empty.
     *
     * Uses [ContentResolver.delete] with a null predicate to remove every row from
     * [MediaStore.Images.Media.EXTERNAL_CONTENT_URI]. On API 33+ this would throw
     * [android.app.RecoverableSecurityException] for rows inserted by other packages;
     * since the test suite controls all insertions in the E2E environment this should
     * not occur. If it does, the exception propagates and fails the test loudly.
     *
     * After deletion a query is performed to assert 0 rows remain.
     */
    fun clearCameraRoll() {
        context.contentResolver.delete(
            MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
            null,
            null
        )
        val cursor = context.contentResolver.query(
            MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
            arrayOf(MediaStore.Images.Media._ID),
            null, null, null
        )
        val count = cursor?.count ?: 0
        cursor?.close()
        assertEquals("MediaStore should be empty after clearCameraRoll()", 0, count)
    }

    /**
     * Triggers the mock camera's shutter path and waits for the new image to appear in
     * MediaStore.
     *
     * Records the current row count, sends the `ACTION_SHUTTER` broadcast to the mock
     * camera process, and polls MediaStore until the count increments or 10 seconds elapse.
     * Falls back to [MediaScannerConnection.scanFile] if the row does not appear promptly
     * (e.g. if the MediaStore indexer has not picked up the new file from the filesystem).
     *
     * The `ACTION_SHUTTER` / `ACTION_SHUTTER_DONE` action strings are defined in
     * `MockCameraActivity` in the `:e2e-mock-camera` module. They are referenced here as
     * string literals because `:e2e-mock-camera` is a separate application module (not a
     * library) and its classes are not available at test compile time.
     *
     * @throws AssertionError if no new image row appears within 15 seconds.
     */
    fun captureOnePhoto() {
        val rowsBefore = countMediaStoreImages()

        // These constants must match MockCameraActivity.ACTION_SHUTTER / ACTION_SHUTTER_DONE.
        val actionShutter = "com.gb4pc.mockcamera.ACTION_SHUTTER"
        val actionShutterDone = "com.gb4pc.mockcamera.ACTION_SHUTTER_DONE"

        // Listen for ACTION_SHUTTER_DONE so we know the mock camera finished writing.
        val latch = CountDownLatch(1)
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(ctx: Context, intent: Intent) {
                if (intent.action == actionShutterDone) {
                    latch.countDown()
                }
            }
        }
        val filter = IntentFilter(actionShutterDone)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            context.registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            context.registerReceiver(receiver, filter)
        }

        try {
            // Send the shutter trigger to the mock camera.
            val shutterIntent = Intent(actionShutter).apply { setPackage(pcPackage) }
            context.sendBroadcast(shutterIntent)

            // Wait up to 10 s for the shutter-done broadcast.
            latch.await(10, TimeUnit.SECONDS)
        } finally {
            context.unregisterReceiver(receiver)
        }

        // Poll until the row count increases or 10 s pass.
        val appeared = waitForCondition(10_000L) { countMediaStoreImages() > rowsBefore }

        if (!appeared) {
            // Fall back: trigger a media scan in case the indexer hasn't picked up the file.
            val dcimPath = Environment.getExternalStoragePublicDirectory(
                Environment.DIRECTORY_DCIM
            ).absolutePath
            val scanLatch = CountDownLatch(1)
            MediaScannerConnection.scanFile(
                context,
                arrayOf(dcimPath),
                arrayOf("image/jpeg")
            ) { _, _ -> scanLatch.countDown() }
            scanLatch.await(5, TimeUnit.SECONDS)

            val appearedAfterScan = waitForCondition(5_000L) { countMediaStoreImages() > rowsBefore }
            if (!appearedAfterScan) {
                fail(
                    "captureOnePhoto(): new image row did not appear in MediaStore within 15 s " +
                        "(even after MediaScannerConnection.scanFile fallback)"
                )
            }
        }
    }

    /**
     * Taps the overlay button at its configured screen position.
     *
     * Reads the active [com.gb4pc.data.OverlayPosition] from [PrefsManager], computes pixel
     * coordinates from [android.view.WindowMetrics] (API 30+) or [android.util.DisplayMetrics]
     * (API 26–29), then dispatches a tap via [UiDevice].
     *
     * If the overlay is not rendered (e.g. blocked in secure-camera mode) this call taps empty
     * screen space and has no visible effect — the plan requires this silent behaviour for
     * Tests 4a/5a's baseline-failure scenario.
     */
    fun tapOverlay() {
        val (displayWidth, displayHeight) = displaySize()

        val aspectRatio = AspectRatioUtil.quantize(displayWidth, displayHeight)
        val pos = PrefsManager(context).getOverlayPosition(aspectRatio)

        val x = (pos.xPercent / 100f * displayWidth).toInt()
        val y = (pos.yPercent / 100f * displayHeight).toInt()

        UiDevice.getInstance(InstrumentationRegistry.getInstrumentation()).click(x, y)
    }

    /**
     * Locks the screen by sending the KEYCODE_POWER key event and then waits (up to 5 s) for
     * [KeyguardManager.isKeyguardLocked] to return true.
     *
     * @throws AssertionError if the keyguard does not engage within 5 seconds.
     */
    fun lockScreen() {
        uiAutomation.executeShellCommand("input keyevent 26").close()
        val locked = waitForCondition(5_000L) {
            (context.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager).isKeyguardLocked
        }
        if (!locked) {
            fail("lockScreen(): KeyguardManager.isKeyguardLocked did not become true within 5 s")
        }
    }

    /**
     * Launches the mock camera via the secure-camera action and asserts the keyguard is locked.
     *
     * Sends `am start -a android.media.action.STILL_IMAGE_CAMERA_SECURE` then immediately
     * checks [KeyguardManager.isKeyguardLocked]. If the keyguard is not locked the fixture
     * fails before any screenshot is taken — a silent keyguard dismissal would make Tests
     * 4a/5a pass for the wrong reason.
     *
     * Call [lockScreen] before this method to guarantee the device is locked first.
     */
    fun launchSecureCamera() {
        uiAutomation.executeShellCommand(
            "am start -a android.media.action.STILL_IMAGE_CAMERA_SECURE"
        ).close()

        val keyguard = context.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
        if (!keyguard.isKeyguardLocked) {
            fail(
                "launchSecureCamera(): KeyguardManager.isKeyguardLocked == false after launching " +
                    "STILL_IMAGE_CAMERA_SECURE — the adb path dismissed the keyguard. " +
                    "Call lockScreen() before launchSecureCamera() and confirm the emulator " +
                    "honours the secure-camera launch path."
            )
        }
    }

    /**
     * Pauses the current thread for [ms] milliseconds.
     *
     * Explicit helper for the spec's "pause N ms" steps — wraps [Thread.sleep] so test code
     * stays readable without raw sleep calls.
     */
    fun pause(ms: Long) {
        Thread.sleep(ms)
    }

    // ── Private helpers ───────────────────────────────────────────────────────

    private fun countMediaStoreImages(): Int {
        val cursor = context.contentResolver.query(
            MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
            arrayOf(MediaStore.Images.Media._ID),
            null, null, null
        )
        val count = cursor?.count ?: 0
        cursor?.close()
        return count
    }

    private fun displaySize(): Pair<Int, Int> {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val wm = context.getSystemService(Context.WINDOW_SERVICE) as android.view.WindowManager
            val bounds = wm.currentWindowMetrics.bounds
            bounds.width() to bounds.height()
        } else {
            val wm = context.getSystemService(Context.WINDOW_SERVICE) as android.view.WindowManager
            val dm = android.util.DisplayMetrics()
            @Suppress("DEPRECATION")
            wm.defaultDisplay.getMetrics(dm)
            dm.widthPixels to dm.heightPixels
        }
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
