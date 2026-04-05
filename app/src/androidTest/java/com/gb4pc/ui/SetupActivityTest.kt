package com.gb4pc.ui

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.gb4pc.data.PrefsManager
import com.gb4pc.ui.setup.SetupActivity
import org.junit.After
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Instrumented tests for the setup flow.
 * The emulator is assumed to have none of the special permissions pre-granted,
 * so SetupActivity will pause on the first non-granted step.
 */
@RunWith(AndroidJUnit4::class)
class SetupActivityTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<SetupActivity>()

    @After
    fun tearDown() {
        PrefsManager(InstrumentationRegistry.getInstrumentation().targetContext)
            .isSetupCompleted = false
    }

    @Test
    fun setupScreen_showsSetupTitle() {
        composeRule.onNodeWithText("GB4PC Setup").assertIsDisplayed()
    }

    @Test
    fun setupScreen_showsSkipButton() {
        composeRule.onNodeWithText("Skip").assertIsDisplayed()
    }

    @Test
    fun setupScreen_showsGrantButton() {
        // Every step has a "Grant …" button — at least one should be present
        composeRule.onNodeWithText("Grant", substring = true).assertIsDisplayed()
    }

    @Test
    fun setupScreen_skipThroughAllSteps_completesSetup() {
        // Clicking Skip four times covers all steps and marks setup complete.
        // If the activity finishes before four clicks (all permissions already granted)
        // the loop exits early — that is also a valid passing state.
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        repeat(4) {
            try {
                composeRule.onNodeWithText("Skip").performClick()
                composeRule.waitForIdle()
            } catch (_: AssertionError) {
                // Activity finished — verify setup is actually marked complete
                assert(PrefsManager(context).isSetupCompleted) {
                    "Activity finished but isSetupCompleted is still false"
                }
                return
            }
        }
        assert(PrefsManager(context).isSetupCompleted) {
            "isSetupCompleted should be true after all steps are skipped"
        }
    }
}
