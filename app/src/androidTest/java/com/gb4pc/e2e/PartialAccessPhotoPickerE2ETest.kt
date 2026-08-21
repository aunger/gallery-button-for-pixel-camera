package com.gb4pc.e2e

import android.Manifest
import android.app.NotificationManager
import android.content.pm.PackageManager
import android.os.SystemClock
import android.util.Log
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import androidx.test.uiautomator.BySelector
import androidx.test.uiautomator.StaleObjectException
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
 * ### The "Select photos and videos" option, and the manifest's partial-access declaration
 *
 * The app declares both `READ_MEDIA_IMAGES` and `READ_MEDIA_VISUAL_USER_SELECTED` and targets
 * SDK 35. On a device running API 34+, Android's Selected Photos Access is enabled *by default*
 * for any app targeting SDK 34+, so requesting `READ_MEDIA_IMAGES` shows the three-option dialog
 * (Allow all / Select photos and videos / Don't allow). Declaring `READ_MEDIA_VISUAL_USER_SELECTED`
 * is what makes a "Select photos" grant leave `READ_MEDIA_IMAGES` reading as DENIED, instead of the
 * temporary backward-compatibility grant Android would otherwise apply (see
 * [PermissionHelper.hasMediaPermission]'s doc); that declaration is the production fix for issue
 * #568, and it is what lets the H2 assertion below hold. This CI emulator is API 35, so the option
 * is present.
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
 * ### Why full access reads as not-granted for a partial grant, and the async settle
 *
 * The load-bearing reason `hasMediaPermission()` returns false here is that the manifest declares
 * `READ_MEDIA_VISUAL_USER_SELECTED` (see above): with it declared, a "Select photos" grant does not
 * grant `READ_MEDIA_IMAGES`, so `checkSelfPermission(READ_MEDIA_IMAGES)` returns DENIED. Without
 * that declaration Android runs a backward-compatibility mode that *temporarily* reports
 * `READ_MEDIA_IMAGES` as granted (flagged `FLAG_PERMISSION_REVOKED_COMPAT`) while the app is
 * foregrounded -- which is exactly what made an earlier revision of this test time out: its poll
 * for `!hasMediaPermission()` never became true because the in-process compat grant kept
 * `READ_MEDIA_IMAGES` reading as granted (issue #568's real root cause was that missing manifest
 * declaration, a production bug, not a test-timing flake). `GrantPermissionsViewModel` logged
 * `clickedButton == ALLOW_SELECTED_BUTTON` on a real run, confirming "Select photos and videos"
 * really was the option tapped, not "Allow all".
 *
 * The picker still delivers the `READ_MEDIA_VISUAL_USER_SELECTED` grant to this still-running
 * process asynchronously, so this class polls for the state to *settle* -- the partial grant
 * visible (`READ_MEDIA_VISUAL_USER_SELECTED` granted) and full access not granted -- rather than
 * reading once, mirroring how [SetupActivityPermissionDialogE2ETest] polls for its (full) grant to
 * propagate.
 *
 * That end state alone is not evidence, though. Runtime permissions are package-level state that
 * outlives the process, and a real CI run was observed entering this test with
 * `READ_MEDIA_VISUAL_USER_SELECTED` already granted (most plausibly residue from
 * [SetupActivityPermissionDialogE2ETest]'s "Allow all", which the CI step's
 * `-PmediaPermissionGranted=false` undoes for `READ_MEDIA_IMAGES` only), which would have satisfied
 * the poll with no partial grant of this test's own making involved. So the CI step revokes
 * `READ_MEDIA_VISUAL_USER_SELECTED` before instrumentation starts, this class asserts it is denied
 * on entry, and the poll's job is to observe the *transition* to granted (issue #925). If the
 * revoke ever stops working, the entry assertion fails loudly rather than letting the suite go
 * quietly vacuous.
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
 * As the issue anticipated ("expect more work identifying the right resource IDs/gestures"), the
 * system photo picker's thumbnail/confirm controls do not have a resource id confirmed against
 * this CI emulator (API 35, `google_apis` system image) the way
 * [SetupActivityPermissionDialogE2ETest]'s `permission_allow_all_button` was. The permission
 * dialog's own "Select photos and videos" option no longer shares that uncertainty:
 * `permission_allow_selected_button` is confirmed against current AOSP `PermissionController`
 * source (issue #581), so [tapSelectPhotosInSystemDialog] leads with it rather than guessing. This
 * dev environment has no emulator/device to verify the picker controls against (see the several
 * "NOT AUTOMATABLE" comments on issue #568), so:
 *
 *  - [tapSelectPhotosInSystemDialog] falls back from the confirmed id to a case-insensitive
 *    "Select photos" text match -- the exact phrase Android's own developer documentation uses
 *    for this option, which should hold even if the resource id changes in a future Android
 *    version.
 *  - [selectPhotosInSystemPickerAndConfirm] matches either the Google-branded or the AOSP package
 *    name for the MediaProvider photo picker module, in one selector rather than one blocking
 *    wait apiece (issue #925: the absent name used to cost a full timeout on every run). It
 *    requires a selectable thumbnail rather than tolerating an empty grid: the test seeds one
 *    image via [E2EFixture.seedOnePhoto] before the picker opens, so the grid is deterministically
 *    non-empty and the resulting grant is a genuine partial grant over a real item (an empty
 *    selection would not produce `READ_MEDIA_VISUAL_USER_SELECTED`, so it could not exercise H2
 *    at all).
 *
 * A future CI run may reveal these guesses need correcting, exactly as happened over several
 * rounds for [SetupActivityPermissionDialogE2ETest] (see PR #576) and the sibling permission
 * suites before it (see PR #564). [FailureScreenshotRule] is in this class's [RuleChain] so such a
 * run leaves behind what it saw: a screenshot and a window-hierarchy dump listing every window and
 * resource id that was on screen at the moment of failure, pulled by the `Pull and upload E2E
 * screenshots on failure` step in `build.yml`. Correcting a guess should not need a second run to
 * find out what the first one was looking at (issue #925).
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

    private val failureDiagnostics = FailureScreenshotRule()

    @get:Rule
    val ruleChain: RuleChain =
        // failureDiagnostics is innermost so its screenshot and window-hierarchy dump are taken at
        // the moment of failure, before composeRule's teardown replaces whatever was on screen
        // (typically the system photo picker, whose contents are the thing worth seeing) with a
        // destroyed SetupActivity. An outer rule would only ever capture the aftermath.
        RuleChain.outerRule(keyguardDismiss).around(composeRule).around(failureDiagnostics)

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
        // Partial access must be denied on entry too, or assertion 1's denied-to-granted
        // transition is not a transition at all (issue #925). Runtime permissions are
        // package-level state that outlives the process, and this suite runs after
        // SetupActivityPermissionDialogE2ETest's "Allow all", whose grant the CI step's
        // -PmediaPermissionGranted=false undoes for READ_MEDIA_IMAGES alone; a failing run really
        // did observe READ_MEDIA_VISUAL_USER_SELECTED already granted here, before this test
        // touched the picker. The `Run PartialAccessPhotoPickerE2ETest` step therefore revokes it
        // as well, and this assertion is what keeps that revoke honest: if it ever stops working,
        // the suite fails loudly here instead of continuing to "pass" on state it did not create.
        assertFalse(
            "This class assumes READ_MEDIA_VISUAL_USER_SELECTED is NOT granted before the dialog " +
                "is driven (the CI step revokes it alongside READ_MEDIA_IMAGES); it was already " +
                "granted, so observing it granted afterwards would not prove this test's own " +
                "picker selection produced it",
            hasPartialMediaAccess(),
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
        // This holds because the manifest declares READ_MEDIA_VISUAL_USER_SELECTED (see the class
        // doc and PermissionHelper.hasMediaPermission): with it declared, a "Select photos" grant
        // leaves READ_MEDIA_IMAGES reading as DENIED, so hasMediaPermission() is false. Without it,
        // Android's backward-compatibility mode keeps READ_MEDIA_IMAGES reading as granted in-process
        // (FLAG_PERMISSION_REVOKED_COMPAT) for a partial grant, which is why an earlier revision of
        // this test timed out here. GrantPermissionsViewModel logged clickedButton=4096 ==
        // ALLOW_SELECTED_BUTTON on a real run, confirming "Select photos and videos" was tapped.
        //
        // The READ_MEDIA_VISUAL_USER_SELECTED grant is delivered to this still-running process
        // asynchronously, so poll for the state to settle, mirroring how
        // SetupActivityPermissionDialogE2ETest polls for its (full) grant to propagate. The
        // condition requires the partial grant to have genuinely landed (READ_MEDIA_VISUAL_USER_-
        // SELECTED granted); paired with the entry assertion above that it was *denied* before the
        // dialog was driven, what this poll observes is a real denied-to-granted transition
        // produced by this test's own picker selection, not an end state that leftover package
        // state could have supplied on its own (issue #925).
        val partialGrantSettled =
            fixture.waitForCondition(GRANT_TIMEOUT_MS) {
                hasPartialMediaAccess() && !PermissionHelper.hasMediaPermission(context)
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
     * Whether the OS currently reports partial media access (`READ_MEDIA_VISUAL_USER_SELECTED`) as
     * granted to `com.gb4pc`. Read both before the dialog is driven and after the picker selection,
     * so the test asserts the transition between those two readings rather than the later one alone
     * (issue #925).
     */
    private fun hasPartialMediaAccess(): Boolean =
        instrumentation.targetContext.checkSelfPermission(
            Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED,
        ) == PackageManager.PERMISSION_GRANTED

    /**
     * Waits for the real system permission dialog and taps its "Select photos and videos"
     * option (partial access, H2), rather than [SetupActivityPermissionDialogE2ETest]'s "Allow
     * all". `permission_allow_selected_button` is confirmed against the current AOSP
     * `PermissionController` source (issue #581's investigation): it is the id
     * `GrantPermissionsViewHandlerImpl`'s `BUTTON_RES_ID_TO_NUM` maps to `ALLOW_SELECTED_BUTTON`,
     * so it leads the list rather than the two ids this class originally guessed
     * (`permission_more_photos_button`, `permission_allow_partial_button`), which do not exist
     * in that source and always failed over. The text fallbacks remain in case a future Android
     * version renames the id.
     *
     * `device.waitForWindowUpdate()` is a cheap, early guard against the button not existing in
     * the tree yet. It is deliberately not paired with the retry-on-failure loop
     * [SetupActivityPermissionDialogE2ETest] uses to work around AOSP's `SecureButton` silently
     * dropping window-obscured touches (issue #581): this suite has not shown that flake, and a
     * blind retry here is riskier, since a re-tap that lands after the button dismisses could hit
     * whatever the system photo picker (a second dialog) puts in its place instead.
     */
    private fun tapSelectPhotosInSystemDialog() {
        device.waitForWindowUpdate(PERMISSION_CONTROLLER_PKG, WINDOW_UPDATE_TIMEOUT_MS)

        val button =
            findDialogObject(By.res(PERMISSION_CONTROLLER_PKG, "permission_allow_selected_button"))
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
     *
     * ### Why the picker gets its own budget, spent differently (issue #925)
     *
     * This is the slow step. The permission dialog above is proven to appear in about a second, but
     * the picker is cold-started for the first time in the job, and its grid is served from
     * MediaProvider's own synced picker database rather than from MediaStore directly, which
     * [E2EFixture.seedOnePhoto] does not wait for (it confirms only MediaStore visibility). So every
     * lookup here gets [PICKER_TIMEOUT_MS], several times the [DIALOG_TIMEOUT_MS] the dialog keeps,
     * instead of sharing that one.
     *
     * The budget is also spent differently. The previous revision chained one *blocking*
     * `device.wait` per candidate picker package, so the package name that does not exist on a given
     * system image burned a whole [DIALOG_TIMEOUT_MS] before the one that does was even tried,
     * leaving the picker itself ~5 s. Any run where the picker needed longer than that failed at
     * the thumbnail lookup, which is the flake tracked by issue #813: the CI logs for it show the
     * dialog tap landing and then 10.1 s of silence, two [DIALOG_TIMEOUT_MS] windows expiring back
     * to back, with no evidence of anything else going wrong. Now both package names are a single
     * [PICKER_PKG] [Pattern], and every lookup polls with the non-blocking [UiDevice.findObject]
     * (the same treatment [SetupActivityPermissionDialogE2ETest.findAllowAllButtonNow] already
     * gives its dialog), so a tick that finds nothing costs one tree read per selector instead of
     * a full timeout apiece, and an added fallback selector costs no wall clock at all.
     *
     * What a fallback selector does still cost is precision, which is why every selector below is
     * scoped to [PICKER_PKG]. Under the old blocking chain the loose text matches were unreachable
     * until the scoped one had been absent for two full [DIALOG_TIMEOUT_MS] windows; under the poll
     * they get their first look ~100 ms in, while the picker may still be animating, so anything
     * they can match outside the picker they will eventually match at the wrong moment.
     *
     * The picker's *window* is awaited before its contents are hunted for, so the two failure modes
     * this step used to have to hedge between -- the picker never opened, versus it opened onto a
     * grid that was empty or still loading -- are reported as the two different problems they are.
     */
    private fun selectPhotosInSystemPickerAndConfirm() {
        val windowStartMs = SystemClock.uptimeMillis()
        val pickerOpened = device.wait(Until.hasObject(By.pkg(PICKER_PKG)), PICKER_TIMEOUT_MS) == true
        require(pickerOpened) {
            "The system photo picker never opened within $PICKER_TIMEOUT_MS ms of tapping " +
                "\"Select photos and videos\": no window belonging to a MediaProvider photo picker " +
                "module (${PICKER_PKG.pattern()}) ever appeared, and the foreground package is " +
                "\"${device.currentPackageName}\"."
        }
        Log.i(
            TAG,
            "picker window appeared after ${SystemClock.uptimeMillis() - windowStartMs}ms " +
                "of its ${PICKER_TIMEOUT_MS}ms budget",
        )

        require(awaitAndTap("thumbnail", listOf(By.res(pickerRes("icon_thumbnail"))))) {
            "The system photo picker opened but showed no selectable thumbnail within " +
                "$PICKER_TIMEOUT_MS ms, even though seedOnePhoto() inserted one image before it " +
                "opened. Either its grid was still empty (MediaProvider's picker database had not " +
                "synced the seeded row yet) or the thumbnail resource id differs on this emulator " +
                "(see the class doc's H2 caveat)."
        }

        // Every selector is scoped to the picker's own package, the text fallbacks included. They
        // are consulted ~100 ms after the thumbnail tap now rather than after two 5 s windows had
        // expired, which is exactly the interval where the picker's bottom bar has not animated in
        // yet and `button_add` is legitimately absent. Unscoped, `.*(allow|done|add).*` full-matches
        // this app's own `setup_media_button` ("Allow Photo Access"), `setup_media_desc` and
        // `setup_notification_button`, and the permission dialog's "Allow all" -- and since
        // UiObject2.click() reports nothing back, tapping SetupActivity's button behind the picker
        // would look exactly like confirming the selection, then fail 10 s later at the grant
        // assertion with a message blaming the grant (PR #926 review).
        val confirmTapped =
            awaitAndTap(
                "confirm button",
                listOf(
                    By.res(pickerRes("button_add")),
                    By.pkg(PICKER_PKG).textContains("Add"),
                    By.pkg(PICKER_PKG).text(Pattern.compile(".*(allow|done|add).*", Pattern.CASE_INSENSITIVE)),
                ),
            )
        require(confirmTapped) {
            "The system photo picker's confirm/add button was not found within " +
                "$PICKER_TIMEOUT_MS ms of tapping a thumbnail, though the picker itself did open. " +
                "Its resource ids/labels may differ on this emulator (see the class doc's H2 " +
                "caveat); the foreground package is \"${device.currentPackageName}\"."
        }
    }

    /**
     * Polls up to [PICKER_TIMEOUT_MS] for the first of [selectors] to match, then taps it, and
     * returns whether that happened.
     *
     * Each 100 ms tick costs one non-blocking [UiDevice.findObject] per selector rather than
     * [UiDevice.wait]'s whole per-selector budget, which is what makes several fallback selectors
     * free in wall-clock terms (see [selectPhotosInSystemPickerAndConfirm]'s doc).
     *
     * The outcome is logged under [TAG], which `scripts/ci/test-support/filter_logcat.sh` keeps, so
     * each CI run states its own margin instead of leaving the next reader to reconstruct it from
     * log timestamps (issue #925). A green run whose margin is thin is a flake about to happen, and
     * that is worth seeing before it does.
     */
    private fun awaitAndTap(
        what: String,
        selectors: List<BySelector>,
    ): Boolean {
        val startMs = SystemClock.uptimeMillis()
        val tapped =
            fixture.waitForCondition(PICKER_TIMEOUT_MS) {
                val target =
                    selectors.firstNotNullOfOrNull { device.findObject(it) }
                        ?: return@waitForCondition false
                // Let an in-flight animation (the picker sliding up, the grid binding its first
                // row) settle, so the tap is not delivered to a view that is still moving and
                // silently swallowed.
                device.waitForIdle()
                try {
                    target.click()
                    true
                } catch (e: StaleObjectException) {
                    // The node was recycled between the find and the click, so nothing was tapped.
                    // Let the next tick find it afresh rather than failing this test with an
                    // exception in place of its intended assertion.
                    Log.w(TAG, "picker $what went stale between find and tap; retrying", e)
                    false
                }
            }
        if (tapped) {
            Log.i(
                TAG,
                "picker $what found and tapped after ${SystemClock.uptimeMillis() - startMs}ms " +
                    "of its ${PICKER_TIMEOUT_MS}ms budget",
            )
        } else {
            Log.w(TAG, "picker $what never appeared within its ${PICKER_TIMEOUT_MS}ms budget")
        }
        return tapped
    }

    private fun findDialogObject(selector: BySelector): UiObject2? = device.wait(Until.findObject(selector), DIALOG_TIMEOUT_MS)

    private companion object {
        const val TAG = "GB4PC_E2E"
        const val PERMISSION_CONTROLLER_PKG = "com.android.permissioncontroller"

        // The Google-branded and AOSP package names for the MediaProvider photo picker module, as
        // one selector: only one of them exists on any given system image, and matching both at
        // once is what stops the absent one from costing a timeout (issue #925).
        val PICKER_PKG: Pattern = Pattern.compile("(com\\.google\\.android|com\\.android)\\.providers\\.media\\.module")

        // The permission dialog is proven to resolve in ~1 s on this emulator, so it keeps the
        // short budget; the picker behind it does not (see selectPhotosInSystemPickerAndConfirm).
        const val DIALOG_TIMEOUT_MS = 5_000L
        const val PICKER_TIMEOUT_MS = 30_000L
        const val GRANT_TIMEOUT_MS = 10_000L
        const val BANNER_TIMEOUT_MS = 10_000L

        // Matches SetupActivityPermissionDialogE2ETest's budget for the same guard (issue #581).
        const val WINDOW_UPDATE_TIMEOUT_MS = 5_000L

        /** The `pkg:id/name` selector pattern for [id] in whichever picker package is installed. */
        fun pickerRes(id: String): Pattern = Pattern.compile("${PICKER_PKG.pattern()}:id/$id")
    }
}
