package com.gb4pc.e2e

import android.util.Log
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.UiDevice
import com.gb4pc.e2e.visual.Screenshot
import org.junit.rules.TestWatcher
import org.junit.runner.Description
import java.io.File

/**
 * JUnit [TestWatcher] rule that associates every screenshot produced during a test with
 * the test that produced it.
 *
 * ## Option B — deterministic prefix (primary)
 *
 * At the start of each test [starting] sets [Screenshot.currentTestPrefix] to
 * `"<ClassName>_<methodName>"`. [Screenshot.saveForArtifact] reads this prefix and
 * prepends it to every filename, so `"0-screen.png"` becomes e.g.
 * `"GalleryButtonVisualE2ETest_test0_smokeGreenFeedVisible_0-screen.png"`.
 * The prefix is cleared in [finished] so screenshots saved outside of any test body
 * are never mistakenly attributed.
 *
 * ## Option A — straggler detection (safety net)
 *
 * In [starting], a snapshot of all files currently in the screenshot directory is
 * recorded. In [finished], any new files that appeared during the test but were NOT
 * saved through [Screenshot.saveForArtifact] (i.e. already prefixed by Option B) are
 * renamed with the test name prefix so they too carry a test association. This catches
 * screenshots written directly by the test runner or other out-of-band paths (e.g. a
 * crash handler).
 *
 * ## Failure screenshot
 *
 * On test failure [failed] captures a device screenshot and saves it as
 * `"<ClassName>_<methodName>_failure.png"` in the same directory — the test name is
 * embedded in the filename directly (no reliance on [Screenshot.currentTestPrefix]
 * which is cleared before [failed] is called by JUnit's default watcher order).
 *
 * ## Usage
 *
 * Replace [FailureScreenshotRule] with this rule in any E2E test class:
 * ```kotlin
 * @get:Rule
 * val screenshotRule = ScreenshotTestRule()
 * ```
 */
class ScreenshotTestRule : TestWatcher() {

    companion object {
        private const val TAG = "ScreenshotTestRule"
    }

    /** Files present in the screenshot directory before the current test started. */
    private var baselineFiles: Set<String> = emptySet()

    override fun starting(description: Description) {
        val prefix = testPrefix(description)
        Screenshot.currentTestPrefix.set(prefix)
        baselineFiles = existingScreenshotFileNames()
    }

    override fun finished(description: Description) {
        // Clear the prefix first so that any post-test screenshot saves (by framework code)
        // are not mistakenly attributed to this test.
        Screenshot.currentTestPrefix.set(null)
        renameStragglersForTest(description)
    }

    override fun failed(e: Throwable?, description: Description) {
        val prefix = testPrefix(description)
        val externalFilesDir = InstrumentationRegistry.getInstrumentation().targetContext
            .getExternalFilesDir(null)
        if (externalFilesDir == null) {
            Log.w(TAG, "External storage unavailable; skipping failure screenshot for ${description.methodName}")
            return
        }
        val dir = File(externalFilesDir, "screenshots")
        dir.mkdirs()
        val screenshotFile = File(dir, "${prefix}_failure.png")
        val success = UiDevice.getInstance(InstrumentationRegistry.getInstrumentation())
            .takeScreenshot(screenshotFile)
        if (!success) {
            Log.w(TAG, "Failed to write failure screenshot to ${screenshotFile.absolutePath}")
        }
    }

    // ── Private helpers ───────────────────────────────────────────────────────

    private fun testPrefix(description: Description): String {
        val className = description.testClass?.simpleName ?: "UnknownClass"
        return ScreenshotNaming.buildPrefix(className, description.methodName)
    }

    private fun existingScreenshotFileNames(): Set<String> {
        return try {
            Screenshot.screenshotDir().listFiles()?.map { it.name }?.toSet() ?: emptySet()
        } catch (e: Exception) {
            Log.w(TAG, "Could not list screenshot directory: ${e.message}")
            emptySet()
        }
    }

    /**
     * Renames any new files that appeared during the test and are not already prefixed
     * with the test name (i.e. were not saved through [Screenshot.saveForArtifact]).
     *
     * New files whose names already start with the test prefix are left untouched —
     * they were already attributed by the Option B path.
     */
    private fun renameStragglersForTest(description: Description) {
        val prefix = testPrefix(description)
        val dir = try {
            Screenshot.screenshotDir()
        } catch (e: Exception) {
            Log.w(TAG, "Could not access screenshot directory for straggler rename: ${e.message}")
            return
        }
        val currentFiles = dir.listFiles() ?: return
        for (file in currentFiles) {
            val name = file.name
            if (name !in baselineFiles && !name.startsWith(prefix)) {
                val newFile = File(dir, ScreenshotNaming.resolvedName(prefix, name))
                val renamed = file.renameTo(newFile)
                if (!renamed) {
                    Log.w(TAG, "Could not rename straggler $name → ${newFile.name}")
                }
            }
        }
    }
}
