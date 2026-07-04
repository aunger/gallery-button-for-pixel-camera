package com.gb4pc.e2e

import android.Manifest
import android.app.NotificationManager
import android.content.pm.PackageManager
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import androidx.test.uiautomator.BySelector
import androidx.test.uiautomator.UiDevice
import androidx.test.uiautomator.UiObject2
import androidx.test.uiautomator.Until
import com.gb4pc.Constants
import com.gb4pc.R
import com.gb4pc.data.PrefsManager
import com.gb4pc.ui.settings.MainActivity
import com.gb4pc.ui.setup.SetupActivity
import com.gb4pc.util.DebugLog
import com.gb4pc.util.PermissionHelper
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.ExternalResource
import org.junit.rules.RuleChain
import org.junit.runner.RunWith
import java.util.regex.Pattern

/**
 * E2E coverage for issue #568's last remaining sub-claim (H2): drives the *real* API 34+
 * "Select photos and videos" partial-access picker on the CI emulator, end to end, and confirms
 * [PermissionHelper.hasMediaPermission] treats the resulting `READ_MEDIA_VISUAL_USER_SELECTED`
 * grant as **not granted** -- and that the two UI surfaces gated on that same boolean
 * ([MainActivity]'s banner, [com.gb4pc.service.OverlayService]'s tap-to-fix notification) still
 * react correctly.
 *
 * The other three sub-claims bundled into #568 (see the issue's "Clarification" comment) are
 * already covered elsewhere: the overlay notification and banner for a *fully* denied permission
 * ([PermissionsDeniedE2ETest], [com.gb4pc.ui.MainSettingsScreenTest]), and tap-through routing
 * ([com.gb4pc.ui.MainSettingsScreenTest]). This class is the one genuinely new piece: nothing
 * before it interacted with the live system photo picker, only a Robolectric stub of the
 * resulting grant state ([com.gb4pc.util.PermissionHelperRobolectricTest]).
 *
 * ### The "Select photos and videos" option appears even though the manifest omits the permission
 *
 * The app declares only `READ_MEDIA_IMAGES` (not `READ_MEDIA_VISUAL_USER_SELECTED`) and targets
 * SDK 35. On a device running API 34+, Android's Selected Photos Access is enabled *by default*
 * for any app targeting SDK 34+, so requesting `READ_MEDIA_IMAGES` shows the three-option dialog
 * (Allow all / Select photos and videos / Don't allow) regardless of whether the app declares
 * `READ_MEDIA_VISUAL_USER_SELECTED`; declaring it only changes the re-selection behaviour, not
 * whether the option is offered. This CI emulator is API 35, so the option is present, and no
 * manifest change (which the app deliberately avoids, per [PermissionHelper.hasMediaPermission]'s
 * doc) is needed to reach the H2 path.
 *
 * ### Why this reuses [SetupActivityPermissionDialogE2ETest]'s scaffolding, then goes further
 *
 * The first half of this test (reach the MEDIA step, tap its button, drive the real
 * `com.android.permissioncontroller` dialog) is identical in shape to
 * [SetupActivityPermissionDialogE2ETest], which proved the requestPermissions() dialog flow does
 * not crash the instrumented `com.gb4pc` process on this CI emulator (see that class's doc for
 * the process-restart investigation). This class picks "Select photos and videos" instead of
 * "Allow all", then drives the resulting system photo picker grid, then continues past what that
 * sibling class checks: it also launches [MainActivity] and the mock Pixel Camera (via
 * [E2EFixture]) *within the same live permission state*, to confirm the banner and notification
 * actually render for a genuine partial grant, not only for a fully denied one.
 *
 * ### The partial grant must not tear down the instrumented process
 *
 * Because this test runs the banner and notification assertions *in-process, after* the picker
 * grant, it depends on `com.gb4pc` surviving that grant. The scoped-storage process-kill that
 * [PermissionsDeniedE2ETest] documents was reproduced only for *out-of-band* `pm grant`/`pm
 * revoke`; the sibling above showed a grant delivered through the standard in-app
 * `requestPermissions()` dialog (the OS's intended in-app flow, which delivers a result callback
 * to the still-running app) does *not* kill the process. That in-app delivery mechanism, not the
 * particular permission, is what keeps the process alive, and this test grants
 * `READ_MEDIA_VISUAL_USER_SELECTED` through the very same dialog flow. Should that assumption not
 * hold for this permission on some future build, the CI step's host-side diagnosis (see the
 * `Run PartialAccessPhotoPickerE2ETest` step in `.github/workflows/build.yml`) reports the
 * torn-down-but-partially-granted signature explicitly instead of failing opaquely. The first
 * real CI run (run 28706188622) confirmed the process does survive: its in-process assertions
 * ran, so the assumption holds for `READ_MEDIA_VISUAL_USER_SELECTED` too.
 *
 * ### The partial grant settles asynchronously in this process
 *
 * The picker delivers its grant to this still-running process asynchronously. That first CI run
 * showed `checkSelfPermission(READ_MEDIA_IMAGES)` reading as *granted* in-process for a short
 * window immediately after the picker returned, even though the authoritative package state was
 * only ever partially granted (the same run's host-side `dumpsys package` reported
 * `READ_MEDIA_VISUAL_USER_SELECTED granted=true` and `READ_MEDIA_IMAGES granted=false`, and
 * `GrantPermissionsViewModel` logged `clickedButton == ALLOW_SELECTED_BUTTON`, i.e. "Select photos
 * and videos" really was the option tapped). The single immediate read raced the client-side
 * permission cache before it settled. So this class waits for the state to *settle* -- the
 * partial grant visible (`READ_MEDIA_VISUAL_USER_SELECTED` granted) and full access back to its
 * settled not-granted value -- rather than reading once, mirroring how
 * [SetupActivityPermissionDialogE2ETest] polls for its (full) grant to propagate. Requiring the
 * partial grant to have landed keeps the poll from passing vacuously on the pre-grant state.
 *
 * All of this happens in a single `@Test` method rather than split across several, because the
 * OS-level permission grant this test produces (`READ_MEDIA_VISUAL_USER_SELECTED`, granted) is
 * package-level state that outlives any one `@Test` method and is **not** reset by JUnit's
 * per-method `@Before`/`@After` cycle. Splitting the dialog-driving step into its own method and
 * re-running it from a second `@Test` would hit Android's already-partially-granted follow-up UI
 * (typically a direct re-open of the picker, not the original three-button dialog), which this
 * class was not written to handle. Keeping the whole flow in one method sidesteps that entirely.
 *
 * ### Uncertainties this class cannot resolve without a real CI run
 *
 * As the issue anticipated ("expect more work identifying the right resource IDs/gestures"),
 * neither the permission dialog's "Select photos and videos" option nor the system photo
 * picker's thumbnail/confirm controls have a resource id confirmed against this CI emulator (API
 * 35, `google_apis` system image) the way [SetupActivityPermissionDialogE2ETest]'s
 * `permission_allow_all_button` was. This dev environment has no emulator/device to verify
 * against (see the several "NOT AUTOMATABLE" comments on issue #568), so:
 *
 *  - [tapSelectPhotosInSystemDialog] tries several plausible resource ids, then falls back to a
 *    case-insensitive "Select photos" text match -- the exact phrase Android's own developer
 *    documentation uses for this option, which should hold even if the resource id differs.
 *  - [selectPhotosInSystemPickerAndConfirm] tries both the Google-branded and AOSP package names
 *    for the MediaProvider photo picker module. It requires a selectable thumbnail rather than
 *    tolerating an empty grid: the test seeds one image via [E2EFixture.seedOnePhoto] before the
 *    picker opens, so the grid is deterministically non-empty and the resulting grant is a
 *    genuine partial grant over a real item (an empty selection would not produce
 *    `READ_MEDIA_VISUAL_USER_SELECTED`, so it could not exercise H2 at all).
 *
 * A future CI run may reveal these guesses need correcting, exactly as happened over several
 * rounds for [SetupActivityPermissionDialogE2ETest] (see PR #576) and the sibling permission
 * suites before it (see PR #564).
 *
 * Run via a dedicated CI step:
 * `connectedE2EAndroidTest -Pe2eClass=com.gb4pc.e2e.PartialAccessPhotoPickerE2ETest -PmediaPermissionGranted=false`.
 */
@E2ETest
@RunWith(AndroidJUnit4::class)
class PartialAccessPhotoPickerE2ETest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val device = UiDevice.getInstance(instrumentation)
    private val fixture =
        E2EFixture(
            context = instrumentation.targetContext,
            uiAutomation = instrumentation.uiAutomation,
        )

    private val keyguardDismiss =
        object : ExternalResource() {
            override fun before() {
                fixture.dismissSecureKeyguard()
            }
        }

    private val composeRule = createAndroidComposeRule<SetupActivity>()

    @get:Rule
    val ruleChain: RuleChain = RuleChain.outerRule(keyguardDismiss).around(composeRule)

    @Test
    fun partialPhotoAccess_isTreatedAsNotGranted_bannerAndNotificationStillAppear() {
        val context = instrumentation.targetContext
        val mediaTitle = context.getString(R.string.setup_media_title)
        val mediaButton = context.getString(R.string.setup_media_button)
        val batteryTitle = context.getString(R.string.setup_battery_title)

        // Precondition: the MEDIA step is showing and the permission is genuinely not granted
        // yet (the CI step launches this class with -PmediaPermissionGranted=false).
        composeRule.onNodeWithText(mediaTitle).assertIsDisplayed()
        assertFalse(
            "This class assumes READ_MEDIA_IMAGES is NOT granted before the dialog is driven " +
                "(via -PmediaPermissionGranted=false); it was already granted",
            PermissionHelper.hasMediaPermission(context),
        )

        // Seed one image so the system photo picker's grid is deterministically non-empty. This
        // lets the picker step select a real thumbnail and produce a genuine
        // READ_MEDIA_VISUAL_USER_SELECTED grant (H2's subject), rather than depending on the
        // emulator's non-deterministic residual library. Writing the app's own media needs no
        // media permission on API 29+, so this works with READ_MEDIA_IMAGES still revoked.
        fixture.seedOnePhoto()

        // Tap the MEDIA step's own button, which calls requestPermissions() and shows the real
        // system permission dialog over SetupActivity.
        composeRule.onNodeWithText(mediaButton).performClick()

        // Drive the real com.android.permissioncontroller dialog: pick "Select photos and
        // videos" (partial access, H2) instead of SetupActivityPermissionDialogE2ETest's
        // "Allow all", then drive the resulting system photo picker to completion.
        tapSelectPhotosInSystemDialog()
        selectPhotosInSystemPickerAndConfirm()

        // Let SetupActivity's onResume()/autoAdvanceIfGranted() run after the picker returns
        // control to it, then confirm the things #568 needs proven live.
        composeRule.waitForIdle()

        // 1) hasMediaPermission() treats the partial grant as NOT granted (H2's core claim).
        //
        // The picker delivers its grant to this still-running process asynchronously, and the
        // client-side permission cache settles a little after the grant lands. A real CI run
        // (run 28706188622, job 85132114900) showed READ_MEDIA_IMAGES reading as *granted*
        // in-process for a short window right after the picker returned -- even though the
        // authoritative package state was only ever partially granted: that run's host-side
        // `dumpsys package` confirmed READ_MEDIA_VISUAL_USER_SELECTED granted=true and
        // READ_MEDIA_IMAGES granted=false (the correct partial-access state), while
        // GrantPermissionsViewModel logged clickedButton=4096 == ALLOW_SELECTED_BUTTON, i.e. the
        // "Select photos and videos" option really was the one tapped. So PermissionHelper's
        // logic is right; the single immediate read just raced the cache.
        //
        // Poll for the state to settle instead, mirroring how SetupActivityPermissionDialogE2ETest
        // polls for its (full) grant to propagate. The condition also requires the partial grant
        // to have genuinely landed (READ_MEDIA_VISUAL_USER_SELECTED granted), so this cannot pass
        // vacuously on the pre-grant state where hasMediaPermission() is already false.
        val partialGrantSettled =
            fixture.waitForCondition(GRANT_TIMEOUT_MS) {
                context.checkSelfPermission(Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED) ==
                    PackageManager.PERMISSION_GRANTED &&
                    !PermissionHelper.hasMediaPermission(context)
            }
        assertTrue(
            "After choosing \"Select photos and videos\", the partial grant should settle to " +
                "READ_MEDIA_VISUAL_USER_SELECTED granted while hasMediaPermission() (full access) " +
                "stays false; it did not settle within ${GRANT_TIMEOUT_MS}ms. Partial access " +
                "(READ_MEDIA_VISUAL_USER_SELECTED only) must not count as granted.",
            partialGrantSettled,
        )

        // 2) The setup flow does NOT advance past MEDIA (mirrors
        //    SetupActivityPermissionDialogE2ETest's "advances" assertion, inverted).
        composeRule.onNodeWithText(mediaTitle).assertIsDisplayed()
        composeRule.onNodeWithText(batteryTitle).assertDoesNotExist()

        // 3) MainActivity's media-missing banner still appears. It renders directly from the
        //    same hasMediaPermission() boolean just confirmed false (MainActivity.onResume()),
        //    so this proves that reaction against a genuine live partial grant, not only a
        //    fully denied one (see com.gb4pc.ui.MainSettingsScreenTest for that case).
        PrefsManager(context).isSetupCompleted = true // avoid MainActivity redirecting to SetupActivity
        val bannerText = context.getString(R.string.settings_media_missing)
        ActivityScenario.launch(MainActivity::class.java).use {
            val bannerShown = device.wait(Until.findObject(By.textContains(bannerText)), BANNER_TIMEOUT_MS) != null
            assertTrue(
                "MainActivity's media-missing banner should appear when the media permission is " +
                    "only partially granted (H2), exactly as it does when fully denied",
                bannerShown,
            )
        }

        // 4) OverlayService's tap-to-fix notification still appears, exercised the same way as
        //    PermissionsDeniedE2ETest, now against a genuine partial grant. fixture.setUp()
        //    establishes the usual "service running, Pixel Camera stopped, overlay inactive"
        //    precondition; it does not touch the media-permission state this test built above.
        fixture.setUp()
        DebugLog.clear()
        fixture.launchPixelCamera()

        val logged =
            fixture.waitForCondition(10_000L) {
                DebugLog.getEntries().any { it.message.contains("Media read permission not granted") }
            }
        assertTrue(
            "OverlayService should still treat partial media access as missing and log " +
                "accordingly, instead of polling MediaStore for the thumbnail",
            logged,
        )

        val notificationManager = context.getSystemService(NotificationManager::class.java)
        val notified =
            fixture.waitForCondition(5_000L) {
                notificationManager.activeNotifications.any {
                    it.id == Constants.NOTIFICATION_MEDIA_PERMISSION_ID
                }
            }
        assertTrue(
            "A tap-to-fix notification should still be posted when the media permission is " +
                "only partially granted (H2), exactly as when fully denied",
            notified,
        )
    }

    /**
     * Waits for the real system permission dialog and taps its "Select photos and videos"
     * option (partial access, H2), rather than [SetupActivityPermissionDialogE2ETest]'s "Allow
     * all". See the class doc for why the resource id guesses below are unconfirmed.
     */
    private fun tapSelectPhotosInSystemDialog() {
        val button =
            findDialogObject(By.res(PERMISSION_CONTROLLER_PKG, "permission_more_photos_button"))
                ?: findDialogObject(By.res(PERMISSION_CONTROLLER_PKG, "permission_allow_selected_button"))
                ?: findDialogObject(By.res(PERMISSION_CONTROLLER_PKG, "permission_allow_partial_button"))
                ?: findDialogObject(By.textContains("Select photos"))
                ?: findDialogObject(By.text(Pattern.compile(".*select.*photo.*", Pattern.CASE_INSENSITIVE)))
        requireNotNull(button) {
            "The system permission dialog's \"Select photos and videos\" option was not found " +
                "within $DIALOG_TIMEOUT_MS ms after tapping the MEDIA step button. Either the " +
                "requestPermissions() dialog did not appear, or this option's wording/resource " +
                "id differs on this emulator/Android build (see the class doc's H2 caveat)."
        }
        button.click()
    }

    /**
     * Drives the real system photo picker that "Select photos and videos" opens: selects the first
     * thumbnail (guaranteed present because [E2EFixture.seedOnePhoto] seeded one image before the
     * picker opened), then confirms, producing a genuine `READ_MEDIA_VISUAL_USER_SELECTED` grant
     * over a real item.
     */
    private fun selectPhotosInSystemPickerAndConfirm() {
        val thumbnail =
            findDialogObject(By.res(PHOTO_PICKER_PKG_GOOGLE, "icon_thumbnail"))
                ?: findDialogObject(By.res(PHOTO_PICKER_PKG_AOSP, "icon_thumbnail"))
        requireNotNull(thumbnail) {
            "The system photo picker showed no selectable thumbnail within $DIALOG_TIMEOUT_MS ms, " +
                "even though seedOnePhoto() inserted one image before it opened. The picker may " +
                "not have opened, or its thumbnail resource id differs on this emulator (see the " +
                "class doc's H2 caveat)."
        }
        thumbnail.click()

        val confirmButton =
            findDialogObject(By.res(PHOTO_PICKER_PKG_GOOGLE, "button_add"))
                ?: findDialogObject(By.res(PHOTO_PICKER_PKG_AOSP, "button_add"))
                ?: findDialogObject(By.textContains("Add"))
                ?: findDialogObject(By.text(Pattern.compile(".*(allow|done|add).*", Pattern.CASE_INSENSITIVE)))
        requireNotNull(confirmButton) {
            "The system photo picker's confirm/add button was not found within " +
                "$DIALOG_TIMEOUT_MS ms after tapping \"Select photos and videos\". The picker " +
                "may not have opened, or its resource ids/labels differ on this emulator " +
                "(see the class doc's H2 caveat)."
        }
        confirmButton.click()
    }

    private fun findDialogObject(selector: BySelector): UiObject2? = device.wait(Until.findObject(selector), DIALOG_TIMEOUT_MS)

    private companion object {
        const val PERMISSION_CONTROLLER_PKG = "com.android.permissioncontroller"
        const val PHOTO_PICKER_PKG_GOOGLE = "com.google.android.providers.media.module"
        const val PHOTO_PICKER_PKG_AOSP = "com.android.providers.media.module"
        const val DIALOG_TIMEOUT_MS = 5_000L
        const val GRANT_TIMEOUT_MS = 10_000L
        const val BANNER_TIMEOUT_MS = 10_000L
    }
}
