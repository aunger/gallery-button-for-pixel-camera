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
 * [SetupActivityPermissionDialogE2ETest]'s "Allow all", which `-PmediaPermissionGranted=false` then
 * undid for `READ_MEDIA_IMAGES` only), which would have satisfied the poll with no partial grant of
 * this test's own making involved. So that flag now revokes `READ_MEDIA_VISUAL_USER_SELECTED` too
 * (`app/build.gradle.kts`, before `am instrument` starts the process), this class asserts it is
 * denied on entry, and the poll's job is to observe the *transition* to granted (issue #925). If
 * the revoke ever stops working, the entry assertion fails loudly rather than letting the suite go
 * quietly vacuous.
 *
 * The revoke lives with the flag rather than in the CI step so that the run command at the bottom
 * of this doc is true wherever it is run: this suite ends with the partial grant in place, so a dev
 * machine that ran it once would otherwise fail the entry assertion on every later run.
 *
 * All of this happens in a single `@Test` method rather than split across several, because the
 * OS-level permission grant this test produces (`READ_MEDIA_VISUAL_USER_SELECTED`, granted) is
 * package-level state that outlives any one `@Test` method and is **not** reset by JUnit's
 * per-method `@Before`/`@After` cycle. Splitting the dialog-driving step into its own method and
 * re-running it from a second `@Test` would hit Android's already-partially-granted follow-up UI
 * (typically a direct re-open of the picker, not the original three-button dialog), which this
 * class was not written to handle. Keeping the whole flow in one method sidesteps that entirely.
 *
 * ### What this emulator actually shows, and what is still guessed
 *
 * As the issue anticipated ("expect more work identifying the right resource IDs/gestures"), most
 * of what this class knows about the system UI it drives was inferred from AOSP source rather than
 * observed, because this dev environment has no emulator or device to check against (see the
 * several "NOT AUTOMATABLE" comments on issue #568). Run 32466889251 changed that for the
 * permission dialog: it failed with [FailureScreenshotRule] attached, so its window dump is a
 * direct reading of this CI emulator (API 35, `google_apis`). From that dump:
 *
 *  - `permission_allow_selected_button` exists, an `android.widget.Button`, clickable and enabled,
 *    which confirms against the device what issue #581 had only confirmed against source.
 *  - Its label is **"Allow limited access"**, not the "Select photos and videos" wording Android's
 *    developer documentation uses and this class's text fallbacks were written from. Both are
 *    listed now; only the id was carrying the lookup before.
 *  - The dialog's *window* belongs to `com.google.android.permissioncontroller`, while its
 *    *resource ids* keep the AOSP `com.android.permissioncontroller` prefix. Those are two
 *    different names for the same UI and this class needs both, for [By.pkg] and [By.res]
 *    respectively.
 *
 * The picker's own controls (`icon_thumbnail`, `button_add`) remain unconfirmed: that run never
 * got the picker open, so nothing has yet dumped its hierarchy. They are still guesses.
 *
 *  - [selectPhotosInSystemPickerAndConfirm] matches either the Google-branded or the AOSP package
 *    name for the MediaProvider photo picker module, in one selector rather than one blocking
 *    wait apiece (issue #925: the absent name used to cost a full timeout on every run). It
 *    requires a selectable thumbnail rather than tolerating an empty grid: the test seeds one
 *    image via [E2EFixture.seedOnePhoto] before the picker opens, so the grid is deterministically
 *    non-empty and the resulting grant is a genuine partial grant over a real item (an empty
 *    selection would not produce `READ_MEDIA_VISUAL_USER_SELECTED`, so it could not exercise H2
 *    at all).
 *
 * A future CI run may reveal the remaining guesses need correcting, exactly as happened over
 * several rounds for [SetupActivityPermissionDialogE2ETest] (see PR #576) and the sibling
 * permission suites before it (see PR #564). [FailureScreenshotRule] is in this class's [RuleChain]
 * so such a run leaves behind what it saw: a screenshot and a window-hierarchy dump listing every
 * window and resource id that was on screen at the moment of failure, pulled by the `Pull and
 * upload E2E screenshots on failure` step in `build.yml`. Correcting a guess should not need a
 * second run to find out what the first one was looking at (issue #925). That is not a hypothetical
 * benefit: everything the section above states about this emulator came out of the first failure
 * that captured it.
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
        // SetupActivityPermissionDialogE2ETest's "Allow all", which -PmediaPermissionGranted=false
        // used to undo for READ_MEDIA_IMAGES alone; a failing run really did observe
        // READ_MEDIA_VISUAL_USER_SELECTED already granted here, before this test touched the
        // picker. That flag now revokes both, and this assertion is what keeps the revoke honest:
        // if it ever stops working, the suite fails loudly here instead of continuing to "pass" on
        // state it did not create.
        assertFalse(
            "This class assumes READ_MEDIA_VISUAL_USER_SELECTED is NOT granted before the dialog " +
                "is driven (-PmediaPermissionGranted=false revokes it alongside READ_MEDIA_IMAGES); " +
                "it was already granted, so observing it granted afterwards would not prove this " +
                "test's own picker selection produced it",
            hasPartialMediaAccess(),
        )
        // Since #572 the step routes to Settings instead of the dialog once it has asked before
        // and Android has stopped showing dialogs. This suite runs after
        // SetupActivityPermissionDialogE2ETest, which does tap the dialog, so without this reset
        // the tap below would open Settings and never reach the picker. See
        // E2EFixture.resetPermissionRequestHistory.
        fixture.resetPermissionRequestHistory(PermissionHelper.mediaPermission)

        // Seed one image so the system photo picker's grid is deterministically non-empty. This
        // lets the picker step select a real thumbnail and produce a genuine
        // READ_MEDIA_VISUAL_USER_SELECTED grant (H2's subject), rather than depending on the
        // emulator's non-deterministic residual library. Writing the app's own media needs no
        // media permission on API 29+, so this works with READ_MEDIA_IMAGES still revoked.
        fixture.seedOnePhoto()

        // Tap the MEDIA step's own button, which calls requestPermissions() and shows the real
        // system permission dialog over SetupActivity.
        composeRule.onNodeWithText(mediaButton).performClick()

        // Drive the real PermissionController dialog: pick partial access ("Allow limited access"
        // on this emulator, H2) instead of SetupActivityPermissionDialogE2ETest's "Allow all",
        // then drive the resulting system photo picker to completion.
        tapPartialAccessOptionInSystemDialog()
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
     * Selectors for the permission dialog's partial-access option, best first.
     *
     * `permission_allow_selected_button` is the id `GrantPermissionsViewHandlerImpl`'s
     * `BUTTON_RES_ID_TO_NUM` maps to `ALLOW_SELECTED_BUTTON` (issue #581's investigation), and
     * run 32466889251's window dump confirms it against this emulator directly, rather than
     * against AOSP source alone: the node is there, an `android.widget.Button`, clickable and
     * enabled. Its label on this image is **"Allow limited access"**, not the "Select photos and
     * videos" wording Android's developer documentation uses, so the text fallbacks list both;
     * the old ones would have matched nothing here had the id ever failed.
     *
     * The text fallbacks are scoped to [PERMISSION_CONTROLLER_PKG] for the reason the picker's
     * are scoped to its own package (see [selectPhotosInSystemPickerAndConfirm]): a loose text
     * match that can leave the window it is meant to search will eventually match something else
     * at the wrong moment. `SetupActivity` is directly behind this dialog, and its own
     * `setup_media_desc` string contains the phrase "limited access".
     */
    private val partialAccessOptionSelectors: List<BySelector> =
        listOf(
            By.res(PERMISSION_CONTROLLER_RES_PKG, "permission_allow_selected_button"),
            By.pkg(PERMISSION_CONTROLLER_PKG).textContains("Allow limited access"),
            By.pkg(PERMISSION_CONTROLLER_PKG).textContains("Select photos"),
            By.pkg(PERMISSION_CONTROLLER_PKG).text(
                Pattern.compile(".*(limited access|select.*photo).*", Pattern.CASE_INSENSITIVE),
            ),
        )

    /**
     * Waits for the real system permission dialog and taps its partial-access option (H2), rather
     * than [SetupActivityPermissionDialogE2ETest]'s "Allow all".
     *
     * Only the first tap happens here. Whether it *took* is not observable at this point -- see
     * [awaitPickerWindow], which watches for the consequence and taps again if it never arrives.
     *
     * The previous revision also called `device.waitForWindowUpdate(PERMISSION_CONTROLLER_PKG,
     * ...)` as "a cheap, early guard against the button not existing in the tree yet". That guard
     * was doing nothing: it took the AOSP package name while the dialog's window belongs to
     * `com.google.android.permissioncontroller` on this image, and `waitForWindowUpdate` returns
     * immediately when the current window's package does not match the one asked for. It is gone
     * rather than corrected, because the polling lookup below already waits for the button itself,
     * which is the thing the guard was a proxy for.
     */
    private fun tapPartialAccessOptionInSystemDialog() {
        val tapped = awaitAndTap("the dialog's partial-access option", partialAccessOptionSelectors, DIALOG_TIMEOUT_MS)
        require(tapped) {
            "The system permission dialog's partial-access option (\"Allow limited access\" on " +
                "this emulator) was not found within $DIALOG_TIMEOUT_MS ms after tapping the MEDIA " +
                "step button. Either the requestPermissions() dialog did not appear, or this " +
                "option's wording/resource id differs on this build; the foreground package is " +
                "\"${device.currentPackageName}\"."
        }
    }

    /**
     * Waits for the system photo picker's window, re-tapping the dialog's partial-access option
     * while the picker has not appeared and that option is still on screen.
     *
     * ### Why a re-tap, having previously argued against one (issue #925, run 32466889251)
     *
     * This class's earlier revision deliberately did *not* pair the dialog tap with the
     * retry-on-failure loop [SetupActivityPermissionDialogE2ETest] uses against AOSP's
     * `SecureButton` silently dropping window-obscured touches (issue #581), on the grounds that
     * "this suite has not shown that flake". It has now, and the evidence is unambiguous:
     *
     *  - one tap was injected, 1.2 s after the dialog appeared (`MotionEvent.setDisplayId` in
     *    logcat at 09:26:42.996),
     *  - no MediaProvider picker activity was ever started, anywhere in that run's log, and
     *  - 30 s later the dialog was still up, with `permission_allow_selected_button` still
     *    clickable, enabled and visible at the same bounds the tap was aimed at.
     *
     * A tap that lands on a live button and changes nothing at all is `SecureButton`'s signature:
     * it drops any touch the input dispatcher flags `FLAG_WINDOW_IS_OBSCURED`/
     * `FLAG_WINDOW_IS_PARTIALLY_OBSCURED`, with no exception, no log, and no symptom other than
     * the thing you asked for not happening. That is transient by nature, so retrying is the fix
     * that does not depend on identifying which window did the obscuring.
     *
     * ### One window it was not, and what the drop tracks instead (issue #930)
     *
     * The named suspect was the full-screen `pointer_location` readout that
     * `scripts/ci/test-support/setup-e2e-emulator.sh` turns on for every E2E job, visible across
     * the top of run 32466889251's failure screenshot. It is not the cause, and what settles that
     * is the mechanism rather than any statistic: AOSP adds the readout as a
     * `TYPE_SECURE_SYSTEM_OVERLAY` window, and `InputDispatcher::canBeObscuredBy()` excludes every
     * trusted overlay from the computation that raises the two flags `SecureButton` filters on,
     * unconditionally and on type alone. That script's step 7 carries the argument in full.
     *
     * A second, much weaker observation is recorded there too, because it is the only signal so
     * far about what *does* vary. Over the 25 E2E runs of 22-23 Aug 2026, the 6 that logged a
     * re-tap had tapped the option sooner after the request than the 19 that did not, a mean
     * 1430ms against 1735ms (p = 0.012). That is a lead, not a finding: n is 6, the two ranges
     * almost entirely overlap, the comparison is observational, and because this loop taps as
     * soon as the option is findable, a shorter elapsed may mean a younger dialog window at tap
     * time (issue #581's condition) or merely a dialog that appeared sooner. Either way, a re-tap
     * in the log is evidence about this suite's own timing rather than a reason to turn a
     * debugging aid off.
     *
     * The old objection to retrying was real, and it is answered by bounding the retries rather
     * than by abandoning them. A re-tap must not land while the picker is launching, because the
     * option's centre on this emulator is (540, 1233) -- inside the grid the picker puts there,
     * where a stray tap would select or deselect the one seeded photo every later assertion
     * depends on. Three limits keep that narrow:
     *
     *  - a tick re-taps only while the picker window is absent *and* the option is still findable;
     *  - the first re-tap waits [RETAP_INTERVAL_MS], set from measurement rather than intuition
     *    (see below), and each later one waits that long again, so a tap always has time to take
     *    effect before another follows it;
     *  - there are at most [MAX_RETAPS] of them, which is simply what that spacing affords inside
     *    [PICKER_TIMEOUT_MS] with room left to watch the last one take effect.
     *
     * [RETAP_INTERVAL_MS] is set against a healthy launch, not a guess about how long a dropped
     * touch takes to notice. Run 32468442166 passed this suite and logged `picker window appeared
     * after 1126ms`, timed from the same point this loop starts from, so a re-tap at 5 s sits at
     * roughly four times a healthy launch: one could only land inside a launch that was four times
     * slower than the only healthy measurement there is. The generosity is deliberate, because the
     * two ways of being wrong are not symmetric. Waiting too long costs seconds of a budget the
     * healthy path uses 1.1 s of, and a dropped tap leaves the dialog up indefinitely (the failing
     * run's dialog was still there 30 s later), so nothing is lost by asking again late. Tapping
     * too early corrupts the selection under test and reports it as something else.
     *
     * Past the cap, more taps would not help anyway: a drop that outlives it is not the transient
     * condition this guards against, and would need diagnosing rather than re-sending.
     */
    private fun awaitPickerWindow(): Boolean {
        val startMs = SystemClock.uptimeMillis()
        var lastTapMs = startMs
        var retaps = 0
        val opened =
            fixture.waitForCondition(PICKER_TIMEOUT_MS) {
                if (device.hasObject(By.pkg(PICKER_PKG))) return@waitForCondition true
                val nowMs = SystemClock.uptimeMillis()
                if (retaps >= MAX_RETAPS || nowMs - lastTapMs < RETAP_INTERVAL_MS) return@waitForCondition false
                findNow(partialAccessOptionSelectors)?.let { option ->
                    retaps++
                    lastTapMs = nowMs
                    Log.w(
                        TAG,
                        "picker flow: no picker window ${nowMs - startMs}ms after the dialog tap, and the " +
                            "partial-access option is still on screen, so that tap was dropped " +
                            "(issue #581's SecureButton case); re-tap $retaps of $MAX_RETAPS",
                    )
                    try {
                        option.click()
                    } catch (e: StaleObjectException) {
                        // The dialog started dismissing between the find and the click, so the
                        // previous tap did register after all. Nothing to re-tap.
                        Log.w(TAG, "picker flow: partial-access option went stale between find and re-tap", e)
                    }
                }
                false
            }
        // Both paths report the re-tap count as one number, so neither a green run's headroom nor
        // a red run's "how hard did it have to try" has to be reconstructed by counting log lines.
        val elapsedMs = SystemClock.uptimeMillis() - startMs
        if (opened) {
            Log.i(
                TAG,
                "picker flow: picker window appeared after ${elapsedMs}ms of its ${PICKER_TIMEOUT_MS}ms " +
                    "budget, after $retaps re-tap(s) of the dialog's partial-access option",
            )
        } else {
            Log.w(
                TAG,
                "picker flow: picker window never appeared within its ${PICKER_TIMEOUT_MS}ms budget, " +
                    "after $retaps re-tap(s) of the dialog's partial-access option",
            )
        }
        return opened
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
        require(awaitPickerWindow()) {
            // Which of the two remaining explanations applies is readable off the screen at the
            // moment of failure, so read it rather than making the next reader open the artifact:
            // an option still sitting there means every tap was dropped, an option gone means a
            // tap registered and no picker followed.
            val optionFate =
                if (findNow(partialAccessOptionSelectors) != null) {
                    "still on screen, so every tap, including the re-taps, was dropped"
                } else {
                    "gone, so a tap did register but no picker window followed it"
                }
            "The system photo picker never opened within $PICKER_TIMEOUT_MS ms of tapping the " +
                "dialog's partial-access option: no window belonging to a MediaProvider photo " +
                "picker module (${PICKER_PKG.pattern()}) ever appeared, and the foreground package " +
                "is \"${device.currentPackageName}\". The dialog's partial-access option is " +
                "$optionFate."
        }

        require(awaitAndTap("thumbnail", listOf(By.res(pickerRes("icon_thumbnail"))), PICKER_TIMEOUT_MS)) {
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
                PICKER_TIMEOUT_MS,
            )
        require(confirmTapped) {
            "The system photo picker's confirm/add button was not found within " +
                "$PICKER_TIMEOUT_MS ms of tapping a thumbnail, though the picker itself did open. " +
                "Its resource ids/labels may differ on this emulator (see the class doc's H2 " +
                "caveat); the foreground package is \"${device.currentPackageName}\"."
        }
    }

    /**
     * Polls up to [timeoutMs] for the first of [selectors] to match, then taps it, and returns
     * whether that happened.
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
        timeoutMs: Long,
    ): Boolean {
        val startMs = SystemClock.uptimeMillis()
        val tapped =
            fixture.waitForCondition(timeoutMs) {
                val target = findNow(selectors) ?: return@waitForCondition false
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
                    Log.w(TAG, "$what went stale between find and tap; retrying", e)
                    false
                }
            }
        val elapsedMs = SystemClock.uptimeMillis() - startMs
        if (tapped) {
            Log.i(TAG, "picker flow: $what found and tapped after ${elapsedMs}ms of its ${timeoutMs}ms budget")
            // waitForCondition evaluates its condition once more *after* the deadline, and this
            // condition taps as a side effect, so a tap can land past the budget and still report
            // success. Say so when it does: this line is the headroom signal issue #925's
            // acceptance criteria are read off, and it must not be able to overstate the budget it
            // fit inside.
            if (elapsedMs > timeoutMs) {
                Log.w(
                    TAG,
                    "picker flow: $what was found only by waitForCondition's post-deadline retry, " +
                        "${elapsedMs - timeoutMs}ms past its ${timeoutMs}ms budget; " +
                        "the line above is an overrun, not headroom",
                )
            }
        } else {
            Log.w(TAG, "picker flow: $what never appeared within its ${timeoutMs}ms budget")
        }
        return tapped
    }

    /** The first of [selectors] currently in the tree, without blocking on any of them. */
    private fun findNow(selectors: List<BySelector>): UiObject2? = selectors.firstNotNullOfOrNull { device.findObject(it) }

    private companion object {
        const val TAG = "GB4PC_E2E"

        // The permission dialog's *window* belongs to whichever PermissionController the image
        // ships, Google's or AOSP's, so scoping a selector to it takes both names (issue #925 /
        // PR #926: run 32466889251's window dump has package="com.google.android.permission-
        // controller" on this emulator).
        val PERMISSION_CONTROLLER_PKG: Pattern = Pattern.compile("(com\\.google\\.android|com\\.android)\\.permissioncontroller")

        // Its *resource ids*, though, keep the AOSP prefix even there: the same dump lists
        // "com.android.permissioncontroller:id/permission_allow_selected_button" inside a window
        // whose package is the Google one. The two are separate names and only this one belongs in
        // By.res.
        const val PERMISSION_CONTROLLER_RES_PKG = "com.android.permissioncontroller"

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

        // Spacing and count for re-taps of the dialog's partial-access option (see
        // awaitPickerWindow for the full reasoning). 5 s is ~4x the 1126 ms a healthy picker
        // launch took on the green run 32468442166, measured from the same point the re-tap clock
        // starts: the first re-tap must sit clearly outside a normal launch, because one landing
        // inside it would tap the picker's photo grid. The cap is what this spacing affords inside
        // PICKER_TIMEOUT_MS with time left to see the last re-tap take effect.
        const val RETAP_INTERVAL_MS = 5_000L
        const val MAX_RETAPS = 5

        /** The `pkg:id/name` selector pattern for [id] in whichever picker package is installed. */
        fun pickerRes(id: String): Pattern = Pattern.compile("${PICKER_PKG.pattern()}:id/$id")
    }
}
