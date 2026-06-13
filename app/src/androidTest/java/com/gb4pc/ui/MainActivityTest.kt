package com.gb4pc.ui

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.test.core.app.ActivityScenario
import androidx.test.espresso.intent.Intents
import androidx.test.espresso.intent.matcher.IntentMatchers.hasComponent
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.gb4pc.data.PrefsManager
import com.gb4pc.ui.settings.MainActivity
import com.gb4pc.ui.setup.SetupActivity
import com.gb4pc.util.PermissionHelper
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
    fun whenSetupNotCompleted_launchesSetupActivity() {
        Intents.init()
        try {
            ActivityScenario.launch(MainActivity::class.java)
            Intents.intended(hasComponent(SetupActivity::class.java.name))
        } finally {
            Intents.release()
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
    fun mainScreen_showsPixelCameraMissingCard_matchingInstalledState() {
        // The e2e-mock-camera stub shares Pixel Camera's applicationId, so on CI
        // (and on any device where it has been side-loaded) isPixelCameraInstalled
        // is true and the missing-camera card must be hidden. On a real device
        // without Pixel Camera (or its stub) installed, the card must be shown.
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val cameraInstalled = PermissionHelper.isPixelCameraInstalled(context)

        val cardNode = composeRule.onNodeWithText("Pixel Camera is not installed", substring = true)
        if (cameraInstalled) {
            cardNode.assertDoesNotExist()
        } else {
            cardNode.assertIsDisplayed()
        }
    }
}
