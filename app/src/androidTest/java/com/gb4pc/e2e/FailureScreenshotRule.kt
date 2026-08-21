package com.gb4pc.e2e

import android.util.Log
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.UiDevice
import org.junit.rules.TestWatcher
import org.junit.runner.Description
import java.io.File

/**
 * JUnit [TestWatcher] rule that captures a screenshot and a window-hierarchy dump whenever a test
 * fails.
 *
 * Both are saved to the app's external files directory under a "screenshots" subdirectory (the same
 * location used by [com.gb4pc.e2e.visual.Screenshot.saveForArtifact]) so that CI artifact pickup
 * collects them automatically alongside any other screenshots produced during the test.
 *
 * File name format: `<ClassName>-<methodName>-failure.png` and
 * `<ClassName>-<methodName>-failure-window-hierarchy.xml`.
 *
 * The hierarchy dump answers what a screenshot cannot when a test fails hunting for a UI element
 * that is not there (issue #925): it names every window on screen and every resource id in it, so
 * "the element's id differs on this build" and "the window in front is not the one expected" become
 * distinguishable after the fact, from the CI artifact alone.
 *
 * Both captures are best-effort. A failure to write either one is logged and swallowed, because the
 * test failure that triggered this rule is the result worth reporting; a diagnostic that cannot be
 * taken must not replace it with an unrelated error.
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
        val device = UiDevice.getInstance(InstrumentationRegistry.getInstrumentation())

        val screenshotFile = File(dir, "$className-${description.methodName}-failure.png")
        val success = device.takeScreenshot(screenshotFile)
        if (!success) {
            Log.w(TAG, "Failed to write failure screenshot to ${screenshotFile.absolutePath}")
        }

        val hierarchyFile = File(dir, "$className-${description.methodName}-failure-window-hierarchy.xml")
        try {
            device.dumpWindowHierarchy(hierarchyFile)
        } catch (e: Exception) {
            // Deliberately broader than the declared IOException: the dump walks the accessibility
            // tree of whatever is on screen at the moment of a failure, and that layer throws
            // unchecked exceptions of its own. Letting one escape would add a second, unrelated
            // failure to the one this rule exists to document (JUnit folds it into a
            // MultipleFailureException rather than replacing the real one, so the cost is noise
            // rather than a lost result -- but noise on top of a failure is what this rule is
            // supposed to prevent).
            Log.w(TAG, "Failed to write failure window hierarchy to ${hierarchyFile.absolutePath}", e)
        }
    }
}
