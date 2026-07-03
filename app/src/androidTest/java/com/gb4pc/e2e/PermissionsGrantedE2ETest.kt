package com.gb4pc.e2e

import android.content.Context
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.gb4pc.R
import com.gb4pc.ui.setup.SetupActivity
import com.gb4pc.util.DebugLog
import com.gb4pc.util.PermissionHelper
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * E2E regression coverage for issue #509 (root cause H1), granted-permission half.
 *
 * See [PermissionsDeniedE2ETest] for the denied-permission half and the design rationale shared
 * by both classes; this doc covers only what is specific to this one.
 *
 * This class assumes `READ_MEDIA_IMAGES` (or `READ_EXTERNAL_STORAGE` below API 33) is already
 * granted *before* the app process starts. That is the default state every `connectedE2EAndroidTest`
 * invocation already produces (`app/build.gradle.kts` grants it in `doLast`, before `am instrument`
 * launches the process), so no test here needs to touch the grant itself; touching it from inside
 * a running test is what [PermissionsDeniedE2ETest]'s doc explains is unsafe.
 */
@E2ETest
@RunWith(AndroidJUnit4::class)
class PermissionsGrantedE2ETest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context: Context = instrumentation.targetContext
    private val fixture =
        E2EFixture(
            context = context,
            uiAutomation = instrumentation.uiAutomation,
        )

    // Used only by setupFlow_skipsMediaStep_whenPermissionAlreadyGranted below. createEmptyComposeRule()
    // (rather than createAndroidComposeRule<SetupActivity>()) does not auto-launch anything, so it
    // has no effect on this class's other tests, which drive OverlayService via E2EFixture and have
    // no need for SetupActivity on screen.
    @get:Rule
    val composeTestRule = createEmptyComposeRule()

    @Before
    fun setUp() {
        fixture.setUp()
    }

    /** Sanity check that this class's assumed precondition actually holds. */
    @Test
    fun hasMediaPermissionIsTrueWhenGranted() {
        assertTrue(
            "This class assumes READ_MEDIA_IMAGES is granted before the process starts " +
                "(the default connectedE2EAndroidTest precondition); it was not",
            PermissionHelper.hasMediaPermission(context),
        )
    }

    /**
     * The core regression test for issue #509: with the permission genuinely granted (via the
     * task-level `pm grant` that runs before this process starts, the same OS mechanism the setup
     * step's system dialog produces), taking a real photo through the mock camera must cause the
     * overlay thumbnail to update. Before PR #564 this was entirely unverified by automation; CI's
     * blanket pre-grant only masked whether the app *itself* ever requested the permission, not
     * whether the thumbnail path worked once it had it.
     */
    @Test
    fun overlayUpdatesThumbnailAfterRealPhotoOnceMediaPermissionGranted() {
        DebugLog.clear()

        fixture.launchPixelCamera()
        fixture.captureOnePhoto()

        val updated =
            fixture.waitForCondition(10_000L) {
                DebugLog.getEntries().any { it.message.startsWith("Thumbnail updated") }
            }
        assertTrue(
            "Overlay thumbnail should update to the newly captured photo within 10 s (issue #509)",
            updated,
        )
    }

    /**
     * Regression coverage for #566 (UI half): confirms the guided setup flow auto-advances past
     * the Photos & Media step without ever showing it, when the permission is already granted
     * before `SetupActivity` starts (this class's precondition; PM-02's existing auto-advance
     * behavior, exercised here for the MEDIA step specifically). See
     * [PermissionsDeniedE2ETest.setupFlow_reachesMediaStep_whenPermissionNotGranted] for the
     * denied-precondition half.
     */
    @Test
    fun setupFlow_skipsMediaStep_whenPermissionAlreadyGranted() {
        ActivityScenario.launch(SetupActivity::class.java).use {
            composeTestRule.waitForIdle()
            composeTestRule.onNodeWithText(context.getString(R.string.setup_media_title)).assertDoesNotExist()
        }
    }
}
