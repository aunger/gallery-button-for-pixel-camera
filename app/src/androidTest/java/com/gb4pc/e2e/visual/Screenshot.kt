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
     * Saves [bmp] as a lossless PNG to:
     *   /sdcard/Android/data/com.gb4pc/files/screenshots/<name>
     *
     * The directory is created if it does not exist. Intended for CI artifact pickup
     * on test failure so failures ship reviewable screenshots.
     */
    fun saveForArtifact(bmp: Bitmap, name: String) {
        val dir = File("/sdcard/Android/data/com.gb4pc/files/screenshots")
        dir.mkdirs()
        val file = File(dir, name)
        FileOutputStream(file).use { out ->
            bmp.compress(Bitmap.CompressFormat.PNG, 100, out)
        }
    }
}
