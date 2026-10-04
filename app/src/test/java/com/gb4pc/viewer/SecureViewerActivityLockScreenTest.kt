package com.gb4pc.viewer

import android.os.Build
import android.view.WindowManager
import androidx.test.core.app.ActivityScenario
import org.junit.After
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * Robolectric tests for SF-06: [SecureViewerActivity] shows over the lock screen and turns the
 * screen on, on both sides of API 27, where `setShowWhenLocked` and `setTurnScreenOn` first appear
 * (issue #985).
 *
 * The API 26 case is the regression guard. Robolectric runs it against the API 26 framework, which
 * has neither method, so calling them there throws `NoSuchMethodError` from `onCreate` exactly as
 * it would on an Android 8.0 device. The case at the project's default simulated SDK (targetSdk 35)
 * covers the branch that does call them.
 */
@RunWith(RobolectricTestRunner::class)
class SecureViewerActivityLockScreenTest {
    @Before
    fun startSession() {
        SessionTracker.instance.startSession()
    }

    @After
    fun endSession() {
        SessionTracker.instance.endSession()
    }

    @Test
    @Config(sdk = [Build.VERSION_CODES.O])
    @Suppress("DEPRECATION") // The two flags are the API 26 mechanism under test.
    fun `on API 26 the viewer sets the show-when-locked and turn-screen-on window flags`() {
        ActivityScenario.launch(SecureViewerActivity::class.java).use { scenario ->
            scenario.onActivity { activity ->
                val flags = activity.window.attributes.flags
                assertTrue(
                    "FLAG_SHOW_WHEN_LOCKED must be set on API 26",
                    (flags and WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED) != 0,
                )
                assertTrue(
                    "FLAG_TURN_SCREEN_ON must be set on API 26",
                    (flags and WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON) != 0,
                )
            }
        }
    }

    @Test
    fun `on the default SDK the viewer calls setShowWhenLocked and setTurnScreenOn`() {
        ActivityScenario.launch(SecureViewerActivity::class.java).use { scenario ->
            scenario.onActivity { activity ->
                assertTrue("setShowWhenLocked(true) must have been called", shadowOf(activity).showWhenLocked)
                assertTrue("setTurnScreenOn(true) must have been called", shadowOf(activity).turnScreenOn)
            }
        }
    }
}
