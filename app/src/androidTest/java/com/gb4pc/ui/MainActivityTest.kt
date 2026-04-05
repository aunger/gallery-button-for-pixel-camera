package com.gb4pc.ui

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.gb4pc.data.PrefsManager
import com.gb4pc.ui.settings.MainActivity
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.ExternalResource
import org.junit.rules.RuleChain
import org.junit.runner.RunWith

/**
 * Tests for MainActivity redirect behaviour (setup not completed) and
 * the main settings screen (setup completed).
 */

@RunWith(AndroidJUnit4::class)
class MainActivityRedirectTest {

    private lateinit var prefs: PrefsManager

    @Before
    fun setUp() {
        prefs = PrefsManager(InstrumentationRegistry.getInstrumentation().targetContext)
        prefs.isSetupCompleted = false
    }

    @After
    fun tearDown() {
        prefs.isSetupCompleted = false
    }

    @Test
    fun whenSetupNotCompleted_mainActivityFinishes() {
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            var isFinishing = false
            scenario.onActivity { activity ->
                isFinishing = activity.isFinishing
            }
            assert(isFinishing) { "MainActivity should call finish() when setup is not completed" }
        }
    }
}

@RunWith(AndroidJUnit4::class)
class MainSettingsScreenTest {

    private val composeRule = createAndroidComposeRule<MainActivity>()
    private val prefsSetup = object : ExternalResource() {
        override fun before() {
            PrefsManager(InstrumentationRegistry.getInstrumentation().targetContext)
                .isSetupCompleted = true
        }
        override fun after() {
            PrefsManager(InstrumentationRegistry.getInstrumentation().targetContext)
                .isSetupCompleted = false
        }
    }

    @get:Rule
    val chain: RuleChain = RuleChain.outerRule(prefsSetup).around(composeRule)

    @Test
    fun mainScreen_showsAppName() {
        composeRule.onNodeWithText("GB4PC").assertIsDisplayed()
    }

    @Test
    fun mainScreen_showsPixelCameraMissingCard_whenNotInstalled() {
        // On an emulator Pixel Camera is never present — the error card must be visible
        composeRule.onNodeWithText("Pixel Camera is not installed", substring = true)
            .assertIsDisplayed()
    }
}
