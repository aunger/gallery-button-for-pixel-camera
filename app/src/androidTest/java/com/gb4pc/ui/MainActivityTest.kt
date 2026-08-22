package com.gb4pc.ui

import android.app.Activity
import android.app.Instrumentation
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
import com.gb4pc.util.PermissionRequestRoute
import org.junit.After
import org.junit.Assert.assertEquals
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
                prefs().isSetupCompleted = true
                // The media banner's route now depends on whether the app has ever fired the
                // system dialog for the permission (#572). Start every test from "never asked",
                // the state a fresh install is in, so neither an earlier test here nor an earlier
                // suite on the same emulator decides it.
                prefs().setRuntimePermissionRequested(PermissionHelper.mediaPermission, false)
            }

            override fun after() {
                prefs().isSetupCompleted = false
                prefs().setRuntimePermissionRequested(PermissionHelper.mediaPermission, false)
            }
        }

    private fun prefs() = PrefsManager(InstrumentationRegistry.getInstrumentation().targetContext)

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
     * Regression coverage for #572: when the permission is *permanently denied*, tapping the
     * media-missing banner must route to the app details screen
     * (`ACTION_APPLICATION_DETAILS_SETTINGS`), since a fresh `requestPermissions()` call would
     * return DENIED synchronously without showing anything, and an ordinary dangerous runtime
     * permission has no dedicated settings sub-screen to deep-link to (unlike Usage Access /
     * Overlay above).
     *
     * This test previously asserted the same intent unconditionally, locking in the static routing
     * #568 shipped. #572 made the route depend on the permission's denial history, so the test now
     * establishes that history (the app has asked before, and Android will no longer show a
     * dialog) instead of assuming it. Its sibling
     * [mainScreen_mediaMissingBanner_routesToTheDialogWhenNeverAsked] covers the other branch.
     *
     * Skips (does not fail) if the environment happens to already have the permission granted,
     * since then the banner, and its tap target, would not exist.
     *
     * ### Stubbing the intent (CI hang, this round)
     *
     * An earlier version of this test asserted `Intents.intended(...)` after the click without
     * ever calling `Intents.intending(...).respondWith(...)` first. Espresso-Intents only
     * intercepts an intent when a response is stubbed *before* the action that fires it;
     * otherwise `Intents.intended(...)` is a passive assertion checked after the real intent
     * already launched its real target. `ACTION_APPLICATION_DETAILS_SETTINGS` targets the actual
     * system Settings app (`com.android.settings`), a separate process, unlike
     * [MainActivityRedirectTest]'s `Intents` usage, which launches [SetupActivity], a real
     * activity within this same app and therefore harmless. Letting the real Settings app launch
     * on the CI emulator, with nothing to bring `MainActivity` back to the foreground afterward,
     * left Compose's test synchronization waiting on a hierarchy that was no longer in the
     * foreground; the general instrumented suite (`connectedDebugAndroidTest`) hung for over 30
     * minutes on the run that first exercised this test and had to be manually cancelled. Now
     * stubs the response before the click, so the real activity never launches at all and the
     * assertion only confirms the *intent* MainActivity attempted to fire.
     */
    @Test
    fun mainScreen_tappingMediaMissingBanner_whenPermanentlyDenied_opensAppDetailsSettings() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        assumeFalse(
            "Requires the media permission to be missing so the banner (and its tap target) exists",
            PermissionHelper.hasMediaPermission(context),
        )
        // Half of the permanently-denied state: the app has fired the dialog at least once.
        prefs().setRuntimePermissionRequested(PermissionHelper.mediaPermission, true)
        // The other half is the platform's, and cannot be forced from here: Android must be
        // refusing to show a fresh dialog. It is, on a device where nothing has user-denied this
        // permission, which is this suite's documented baseline.
        assumeFalse(
            "Requires Android to be refusing a fresh permission dialog, so the Settings route " +
                "is the one under test",
            composeRule.activity.shouldShowRequestPermissionRationale(PermissionHelper.mediaPermission),
        )

        Intents.init()
        try {
            Intents
                .intending(hasAction(Settings.ACTION_APPLICATION_DETAILS_SETTINGS))
                .respondWith(Instrumentation.ActivityResult(Activity.RESULT_OK, null))

            composeRule
                .onNodeWithText(context.getString(R.string.settings_media_missing), substring = true)
                .performClick()
            Intents.intended(hasAction(Settings.ACTION_APPLICATION_DETAILS_SETTINGS))
        } finally {
            Intents.release()
        }
    }

    /**
     * The other half of #572, on real Android: a user who has never been asked (they skipped the
     * setup step, or reached this screen first) must get the in-app dialog, not a detour through
     * system Settings for a grant one tap could have collected.
     *
     * Asserts the routing decision rather than tapping the banner. Tapping would fire the real
     * `com.android.permissioncontroller` dialog over `MainActivity`, and this suite
     * (`connectedDebugAndroidTest`) has no UI Automator driving to dismiss it, which is exactly
     * the "foreground window this suite cannot get back from" shape that hung it for 30+ minutes
     * once already (see the sibling test's note above). The banner's own wiring to this decision
     * is covered by that sibling: it only reaches Settings because the route says so.
     */
    @Test
    fun mainScreen_mediaMissingBanner_routesToTheDialogWhenNeverAsked() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        assumeFalse(
            "Requires the media permission to be missing, the only state the banner appears in",
            PermissionHelper.hasMediaPermission(context),
        )

        val permission = PermissionHelper.mediaPermission
        assertEquals(
            "A permission that has never been asked for must route to the system dialog",
            PermissionRequestRoute.DIALOG,
            PermissionHelper.permissionRequestRoute(
                activity = composeRule.activity,
                permission = permission,
                hasBeenRequestedBefore = prefs().hasRequestedRuntimePermission(permission),
            ),
        )
    }
}
