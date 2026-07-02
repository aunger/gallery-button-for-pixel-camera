package com.gb4pc.e2e

import android.util.Log
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.UiDevice
import org.junit.rules.TestWatcher
import org.junit.runner.Description
import java.io.File

/**
 * JUnit [TestWatcher] rule that captures a diagnostic screenshot whenever a test fails.
 *
 * The screenshot is saved to the app's external files directory under a "screenshots"
 * subdirectory (the same location used by [com.gb4pc.e2e.visual.Screenshot.saveForArtifact])
 * so that CI artifact pickup collects it automatically alongside any other screenshots produced
 * during the test.
 *
 * File name format: `<ClassName>-<methodName>-failure.png`
 *
 * Apply in any E2E test class with:
 * ```kotlin
 * @get:Rule
 * val screenshotOnFailure = FailureScreenshotRule()
 * ```
 */
class FailureScreenshotRule : TestWatcher() {
    companion object {
        private const val TAG = "FailureScreenshotRule"
    }

    override fun failed(
        e: Throwable?,
        description: Description,
    ) {
        val externalFilesDir =
            InstrumentationRegistry
                .getInstrumentation()
                .targetContext
                .getExternalFilesDir(null)
        if (externalFilesDir == null) {
            Log.w(TAG, "External storage unavailable; skipping failure screenshot for ${description.methodName}")
            return
        }
        val dir = File(externalFilesDir, "screenshots")
        dir.mkdirs()
        val className = description.testClass?.simpleName ?: "UnknownClass"
        val screenshotFile = File(dir, "$className-${description.methodName}-failure.png")
        val success =
            UiDevice
                .getInstance(InstrumentationRegistry.getInstrumentation())
                .takeScreenshot(screenshotFile)
        if (!success) {
            Log.w(TAG, "Failed to write failure screenshot to ${screenshotFile.absolutePath}")
        }
    }
}
