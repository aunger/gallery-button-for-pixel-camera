package com.gb4pc.e2e.visual

import android.graphics.Bitmap
import androidx.test.platform.app.InstrumentationRegistry
import com.gb4pc.e2e.ScreenshotNaming
import java.io.File
import java.io.FileOutputStream

/**
 * Screen-capture helpers used by E2E visual tests.
 */
object Screenshot {

    /**
     * Thread-local holding the current test's name prefix (e.g.
     * `"PixelCameraOverlayE2ETest_overlayAppearsWhenViewfinderOpens"`).
     *
     * Set by [com.gb4pc.e2e.ScreenshotTestRule] at the start of each test and cleared at the
     * end. When non-null, [saveForArtifact] prepends this prefix to every filename so
     * saved images are unambiguously associated with the producing test case.
     */
    internal val currentTestPrefix = ThreadLocal<String?>()

    /**
     * Captures the current screen via UiAutomation and returns it as a [Bitmap].
     */
    fun captureScreen(): Bitmap =
        InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot()

    /**
     * Returns the resolved screenshot output directory (creating it if necessary).
     *
     * Extracted so [com.gb4pc.e2e.ScreenshotTestRule]'s straggler-detection logic can use the
     * same path without duplicating the directory-resolution logic.
     */
    internal fun screenshotDir(): File {
        val ctx = InstrumentationRegistry.getInstrumentation().targetContext
        val externalFilesDir = requireNotNull(ctx.getExternalFilesDir(null)) {
            "External storage is not available — cannot access screenshot directory"
        }
        return File(externalFilesDir, "screenshots").also { it.mkdirs() }
    }

    /**
     * Saves [bmp] as a lossless PNG to the app's external files directory under
     * a "screenshots" subdirectory. The path is resolved at runtime via the
     * instrumentation context, which correctly targets the scoped storage directory
     * owned by the target app's package — avoiding Permission denied errors on
     * Android 11+ (API 30+) that occur when using a hardcoded /sdcard path.
     *
     * The directory is created if it does not exist. Intended for CI artifact pickup
     * on test failure so failures ship reviewable screenshots.
     *
     * If [currentTestPrefix] is set (by [com.gb4pc.e2e.ScreenshotTestRule]), the prefix is
     * automatically prepended to [name] so the saved file is self-describing in the
     * artifact browser — e.g. `"0-screen.png"` becomes
     * `"GalleryButtonVisualE2ETest_test0_smokeGreenFeedVisible_0-screen.png"`.
     */
    fun saveForArtifact(bmp: Bitmap, name: String) {
        val resolvedName = ScreenshotNaming.resolvedName(currentTestPrefix.get(), name)
        val dir = screenshotDir()
        FileOutputStream(File(dir, resolvedName)).use { out ->
            bmp.compress(Bitmap.CompressFormat.PNG, 100, out)
        }
    }
}
