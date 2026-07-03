package com.gb4pc.ui

import android.provider.Settings
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.core.app.ActivityScenario
import androidx.test.espresso.intent.Intents
import androidx.test.espresso.intent.matcher.IntentMatchers.hasAction
import androidx.test.espresso.intent.matcher.IntentMatchers.hasComponent
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.gb4pc.R
import com.gb4pc.data.PrefsManager
import com.gb4pc.ui.settings.MainActivity
import com.gb4pc.ui.setup.SetupActivity
import com.gb4pc.util.PermissionHelper
import org.junit.After
import org.junit.Assume.assumeFalse
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
    private val prefsSetup =
        object : ExternalResource() {
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

    /**
     * Regression coverage for #568 (banner half): the media-missing banner's visibility must
     * track the real `hasMediaPermission` state, matching the existing branching style of
     * [mainScreen_showsPixelCameraMissingCard_matchingInstalledState] above rather than assuming
     * a fixed environment. `connectedDebugAndroidTest` (this test's task, distinct from the E2E
     * task's `PermissionsGrantedE2ETest` / `PermissionsDeniedE2ETest`) has never granted
     * `READ_MEDIA_IMAGES`, matching [SetupActivityTest]'s documented "nothing pre-granted"
     * baseline, so the banner is expected to be visible in practice; branching keeps this test
     * correct even if that ambient baseline ever changes.
     */
    @Test
    fun mainScreen_showsMediaMissingBanner_matchingPermissionState() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val hasMedia = PermissionHelper.hasMediaPermission(context)

        val bannerNode =
            composeRule.onNodeWithText(
                context.getString(R.string.settings_media_missing),
                substring = true,
            )
        if (hasMedia) {
            bannerNode.assertDoesNotExist()
        } else {
            bannerNode.assertIsDisplayed()
        }
    }

    /**
     * Regression coverage for #568 (banner tap-through half): tapping the media-missing banner
     * must route to the app details screen (`ACTION_APPLICATION_DETAILS_SETTINGS`), not a
     * permission re-request, since ordinary dangerous runtime permissions have no dedicated
     * settings sub-screen to deep-link to (unlike Usage Access / Overlay above). This locks in
     * today's static routing; issue #572 will make it dynamic based on
     * `shouldShowRequestPermissionRationale()` and this test will need revisiting then.
     *
     * Skips (does not fail) if the environment happens to already have the permission granted,
     * since then the banner, and its tap target, would not exist.
     */
    @Test
    fun mainScreen_tappingMediaMissingBanner_opensAppDetailsSettings() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        assumeFalse(
            "Requires the media permission to be missing so the banner (and its tap target) exists",
            PermissionHelper.hasMediaPermission(context),
        )

        Intents.init()
        try {
            composeRule
                .onNodeWithText(context.getString(R.string.settings_media_missing), substring = true)
                .performClick()
            Intents.intended(hasAction(Settings.ACTION_APPLICATION_DETAILS_SETTINGS))
        } finally {
            Intents.release()
        }
    }
}
