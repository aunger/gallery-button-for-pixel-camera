package com.gb4pc.ui

import android.Manifest
import android.os.Build
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.gb4pc.R
import com.gb4pc.data.PrefsManager
import com.gb4pc.ui.setup.SetupActivity
import com.gb4pc.ui.setup.SetupStep
import com.gb4pc.ui.setup.getSetupSteps
import org.junit.After
import org.junit.Assert.assertTrue
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
        // Undo any grant setupScreen_showsMediaStep_andAutoAdvancesOnceGranted made, so every
        // test in this class keeps starting from the "nothing pre-granted" baseline the class
        // doc promises. Harmless no-op if the permission was never granted.
        revokeMediaPermission()
    }

    private val mediaPermission: String
        get() =
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                Manifest.permission.READ_MEDIA_IMAGES
            } else {
                Manifest.permission.READ_EXTERNAL_STORAGE
            }

    private fun revokeMediaPermission() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        InstrumentationRegistry
            .getInstrumentation()
            .uiAutomation
            .executeShellCommand("pm revoke ${context.packageName} $mediaPermission")
            .close()
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
    fun setupScreen_showsActionButton() {
        // Every step has a primary action button, but its label depends on
        // the step (e.g. "Allow Notifications", "Grant Usage Access", "Grant
        // Overlay Permission", "Exclude from Battery Optimization"); not all
        // of them say "Grant". Whichever step is shown first, its button
        // should be present.
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val possibleButtonTexts =
            listOf(
                R.string.setup_notification_button,
                R.string.setup_media_button,
                R.string.setup_usage_access_button,
                R.string.setup_overlay_button,
                R.string.setup_battery_button,
            ).map { context.getString(it) }

        val displayedButtons =
            possibleButtonTexts.filter { text ->
                composeRule.onAllNodesWithText(text).fetchSemanticsNodes().isNotEmpty()
            }

        assertTrue(
            "Expected one of $possibleButtonTexts to be displayed, but none were found",
            displayedButtons.isNotEmpty(),
        )
    }

    @Test
    fun setupScreen_skipThroughAllSteps_completesSetup() {
        // Clicking Skip once per step covers all steps and marks setup complete.
        // If the activity finishes before every click (some permissions already granted)
        // the loop exits early; that is also a valid passing state.
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        repeat(getSetupSteps().size) {
            try {
                composeRule.onNodeWithText("Skip").performClick()
                composeRule.waitForIdle()
            } catch (_: AssertionError) {
                // Activity finished; verify setup is actually marked complete
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

    /**
     * Regression coverage for issue #509 / #566: the Photos & Media step must actually be
     * reachable in the flow, and the flow must auto-advance once the permission it requests is
     * granted, exactly like every other step's PM-02 auto-advance behavior.
     *
     * Grants the permission via `pm grant` rather than tapping through the real system dialog:
     * that shell command is the same PackageManager-level grant tapping "Allow all" produces, and
     * avoids depending on system-dialog resource IDs that vary by API level and vendor (see
     * PermissionsE2ETest for the same design choice). [SetupActivity.recreate] then re-runs
     * `onResume`'s `autoAdvanceIfGranted()`, exactly as returning to the activity from the real
     * permission dialog would.
     */
    @Test
    fun setupScreen_showsMediaStep_andAutoAdvancesOnceGranted() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext

        // MEDIA is always the step right after NOTIFICATION (see getSetupSteps()); skip past
        // NOTIFICATION first if this API level includes it.
        if (getSetupSteps().first() == SetupStep.NOTIFICATION) {
            composeRule.onNodeWithText(context.getString(R.string.setup_skip)).performClick()
            composeRule.waitForIdle()
        }

        composeRule.onNodeWithText(context.getString(R.string.setup_media_title)).assertIsDisplayed()
        composeRule.onNodeWithText(context.getString(R.string.setup_media_button)).assertIsDisplayed()

        InstrumentationRegistry
            .getInstrumentation()
            .uiAutomation
            .executeShellCommand("pm grant ${context.packageName} $mediaPermission")
            .close()

        composeRule.activity.recreate()
        composeRule.waitForIdle()

        composeRule.onNodeWithText(context.getString(R.string.setup_media_title)).assertDoesNotExist()
    }
}
