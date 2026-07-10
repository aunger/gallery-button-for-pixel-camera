package com.gb4pc.e2e

import android.util.Log
import android.widget.Toast
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.rules.TestWatcher
import org.junit.runner.Description

/**
 * JUnit [TestWatcher] rule that toasts the name of each test as it starts, so anyone watching a
 * screen recording or the live emulator can tell which test is currently running (issue #604).
 *
 * [starting] shows a short toast built from the same `"<ClassName>_<methodName>"` prefix
 * [ScreenshotTestRule] already computes ([ScreenshotNaming.buildPrefix]), then blocks the test
 * thread for [TOAST_DURATION_MS] before explicitly cancelling the toast. Blocking--rather than
 * merely showing the toast and moving on--delays the start of the test body until the toast has
 * cleared, so it does not overlap the test's own UI or pollute its screenshots/video.
 *
 * ## Usage
 *
 * Add alongside [ScreenshotTestRule] / [FailureScreenshotRule] in any E2E test class:
 * ```kotlin
 * @get:Rule
 * val testNameToastRule = TestNameToastRule()
 * ```
 *
 * For classes that assemble a [org.junit.rules.RuleChain] (to sequence keyguard dismissal ahead of
 * a compose rule, for example), add this as the outermost link so the toast is the very first
 * thing shown when the test starts:
 * ```kotlin
 * @get:Rule
 * val ruleChain: RuleChain = RuleChain.outerRule(testNameToastRule).around(keyguardDismiss).around(composeRule)
 * ```
 */
class TestNameToastRule : TestWatcher() {
    companion object {
        private const val TAG = "TestNameToastRule"

        /** How long the toast stays visible before the test body is allowed to start. */
        private const val TOAST_DURATION_MS = 1_000L
    }

    override fun starting(description: Description) {
        val testName = testPrefix(description)
        showAndClearToast(testName)
    }

    private fun testPrefix(description: Description): String {
        val className = description.testClass?.simpleName ?: "UnknownClass"
        return ScreenshotNaming.buildPrefix(className, description.methodName)
    }

    /**
     * Shows [testName] in a toast on the main thread, waits [TOAST_DURATION_MS] so it is visible
     * for a predictable, short duration, then cancels it explicitly rather than relying on the
     * platform's own (longer and less precise) toast duration to elapse.
     */
    private fun showAndClearToast(testName: String) {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        var toast: Toast? = null
        try {
            instrumentation.runOnMainSync {
                toast = Toast.makeText(context, testName, Toast.LENGTH_SHORT)
                toast?.show()
            }
        } catch (e: Exception) {
            Log.w(TAG, "Could not show test-name toast for $testName: ${e.message}")
            return
        }
        Thread.sleep(TOAST_DURATION_MS)
        try {
            instrumentation.runOnMainSync { toast?.cancel() }
        } catch (e: Exception) {
            Log.w(TAG, "Could not cancel test-name toast for $testName: ${e.message}")
        }
    }
}
