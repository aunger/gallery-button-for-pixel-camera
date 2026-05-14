package com.gb4pc.e2e.visual

import android.graphics.Bitmap
import androidx.test.platform.app.InstrumentationRegistry
import java.io.File
import java.io.FileOutputStream

/**
 * Screen-capture helpers used by E2E visual tests.
 */
object Screenshot {

    /**
     * Captures the current screen via UiAutomation and returns it as a [Bitmap].
     */
    fun captureScreen(): Bitmap =
        InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot()

    /**
     * Saves [bmp] as a lossless PNG to the app's external files directory under
     * a "screenshots" subdirectory. The path is resolved at runtime via the
     * instrumentation context, which correctly targets the scoped storage directory
     * owned by the target app's package — avoiding Permission denied errors on
     * Android 11+ (API 30+) that occur when using a hardcoded /sdcard path.
     *
     * The directory is created if it does not exist. Intended for CI artifact pickup
     * on test failure so failures ship reviewable screenshots.
     */
    fun saveForArtifact(bmp: Bitmap, name: String) {
        val ctx = InstrumentationRegistry.getInstrumentation().targetContext
        val dir = File(ctx.getExternalFilesDir(null), "screenshots")
        dir.mkdirs()
        FileOutputStream(File(dir, name)).use { out ->
            bmp.compress(Bitmap.CompressFormat.PNG, 100, out)
        }
    }
}
