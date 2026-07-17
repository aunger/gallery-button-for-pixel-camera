package com.gb4pc.e2e

import android.util.Log
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import androidx.test.uiautomator.BySelector
import androidx.test.uiautomator.StaleObjectException
import androidx.test.uiautomator.UiDevice
import androidx.test.uiautomator.UiObject2
import androidx.test.uiautomator.Until
import com.gb4pc.R
import com.gb4pc.ui.setup.SetupActivity
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
 * E2E coverage for issue #575: drives the *real* system Photos & Media permission dialog on the CI
 * emulator, end to end, and confirms the guided setup flow reacts correctly to a live grant.
 *
 * The two sibling classes stop short of this: [SetupActivityDeniedE2ETest] confirms the MEDIA step
 * is *reached* when the permission is not granted, and [SetupActivityGrantedE2ETest] confirms it is
 * *skipped* when the permission is already granted before the process starts. Neither exercises the
 * transition between those two states through the actual OS dialog. This class does: it reaches the
 * MEDIA step (permission revoked at launch via `-PmediaPermissionGranted=false`), taps the step's
 * own button to fire `SetupActivity.handleGrant`'s `requestPermissions()` launch, then uses UI
 * Automator to tap "Allow all" in the real `com.android.permissioncontroller` dialog, and asserts:
 *
 *  1. `PermissionHelper.hasMediaPermission()` becomes true (the grant actually took effect), and
 *  2. the setup flow advances past the MEDIA step (its `onResume` auto-advance runs after the
 *     permission result), landing on the first still-ungranted step.
 *
 * ### The process-kill hypothesis this test exists to settle
 *
 * `connectedE2EAndroidTest` self-instruments: `am instrument` runs this test code *inside*
 * `com.gb4pc`'s own process. Changing a storage-group runtime permission on an already-running
 * process can make Android kill that process to re-establish its scoped-storage mount; an earlier
 * suite that toggled this permission via out-of-band `pm grant`/`pm revoke` mid-test reproduced
 * exactly that (commit e5d37ed; see [PermissionsDeniedE2ETest]'s class doc). Before this suite's
 * first real CI execution, it was not established whether a grant delivered through the standard
 * in-app `requestPermissions()` dialog (the OS's intended in-app flow, designed to deliver a
 * result callback to the still-running app) has the same effect, because `pm grant` is a
 * different, out-of-band mechanism.
 *
 * That first real run (a `workflow_dispatch` execution, since this PR's base is a feature branch
 * and `build.yml`'s `pull_request` trigger is scoped to `branches: [main]`) settled it: the
 * in-process assertions below ran and passed directly, with `com.gb4pc` staying alive throughout.
 * Tapping "Allow all" through the standard `requestPermissions()` flow does *not* crash or restart
 * the process the way the out-of-band `pm grant` did. The CI step's host-side recovery path
 * (a `dumpsys package` grant check, a `pidof` before/after, and a UI Automator dump confirming
 * setup progress survives) is retained as a safety net: if a future Android version or app change
 * ever reintroduces the crash, that path still distinguishes a genuine restart-with-recovery from
 * an unrelated regression, and reports it instead of silently masking it. See the
 * `Run SetupActivityPermissionDialogE2ETest` step in `.github/workflows/build.yml`.
 *
 * ### Why the same [RuleChain] + `createAndroidComposeRule<SetupActivity>()` scaffolding
 *
 * Identical to [SetupActivityDeniedE2ETest]: the compose rule launches `SetupActivity` and is
 * wired into Compose's test synchronization before any assertion runs, and the
 * [RuleChain.outerRule] keyguard dismissal (via [E2EFixture.dismissSecureKeyguard], which types the
 * suite's PIN rather than only swiping) runs ahead of the compose rule's own `before()`, so this
 * class's activity launch does not race a keyguard re-engagement several E2E suites into the job.
 * See that class's doc for the full rationale.
 *
 * Run via a dedicated CI step:
 * `connectedE2EAndroidTest -Pe2eClass=com.gb4pc.e2e.SetupActivityPermissionDialogE2ETest -PmediaPermissionGranted=false`.
 */
@E2ETest
@RunWith(AndroidJUnit4::class)
class SetupActivityPermissionDialogE2ETest {
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

    private val testNameToastRule = TestNameToastRule()

    @get:Rule
    val ruleChain: RuleChain =
        // testNameToastRule is innermost, running after the activity launch, so its ~3s toast
        // delay does not push the keyguard-dismissal-then-launch sequence into a re-engaged
        // keyguard (see SetupActivityDeniedE2ETest's class doc for that race).
        RuleChain.outerRule(keyguardDismiss).around(composeRule).around(testNameToastRule)

    @Test
    fun setupFlow_grantsMediaPermissionViaSystemDialog_andAdvances() {
        val context = instrumentation.targetContext
        val mediaTitle = context.getString(R.string.setup_media_title)
        val mediaButton = context.getString(R.string.setup_media_button)
        val batteryTitle = context.getString(R.string.setup_battery_title)

        // Precondition: the MEDIA step is showing and the permission is genuinely not granted yet.
        // (The CI step launches this class with -PmediaPermissionGranted=false.)
        composeRule.onNodeWithText(mediaTitle).assertIsDisplayed()
        assertFalse(
            "This class assumes READ_MEDIA_IMAGES is NOT granted before the dialog is driven " +
                "(via -PmediaPermissionGranted=false); it was already granted",
            PermissionHelper.hasMediaPermission(context),
        )

        // Tap the MEDIA step's own button, which calls requestPermissions() and shows the real
        // system permission dialog over SetupActivity.
        composeRule.onNodeWithText(mediaButton).performClick()

        // Drive the real com.android.permissioncontroller dialog: tap "Allow all".
        tapAllowAllInSystemDialog()

        // 1) The grant actually took effect. Poll, retrying the tap if the button is still
        //    visible (issue #581): AOSP's SecureButton (the permission dialog's button class)
        //    silently drops any touch the system's input dispatcher flags
        //    FLAG_WINDOW_IS_OBSCURED/FLAG_WINDOW_IS_PARTIALLY_OBSCURED, with no exception, no
        //    log, and no symptom other than the grant never landing. SecureButton's own AOSP
        //    source contains no self-timer; the flag comes from the real dispatcher computation,
        //    which can transiently flag even a freshly created window's first touches while its
        //    input bookkeeping settles, with no second app window involved. Retrying the tap,
        //    rather than waiting longer or polling a different pre-tap signal, is the fix that
        //    does not depend on identifying which transient condition caused any one drop: if
        //    the button is still visible, the previous tap did not register, so tap it again.
        //    If the button has disappeared, the previous tap succeeded and the grant is simply
        //    propagating asynchronously (the pre-existing, documented issue #604 case); nothing
        //    to retry.
        //
        //    findAllowAllButtonNow() and button.click() are two separate round-trips to the
        //    accessibility tree, so the button can go stale in between: the dialog can start
        //    tearing down (e.g. because the *previous* tap actually did register, just after
        //    this poll tick's find call) between the find and the click, which makes
        //    UiObject2.click() throw StaleObjectException instead of silently no-op'ing. That is
        //    the same "previous tap already succeeded" case findAllowAllButtonNow() returning
        //    null handles, just observed at click time rather than find time, so it is caught
        //    and treated the same way (poll again) rather than letting it escape and fail the
        //    test with an exception instead of the intended assertion.
        var retryCount = 0
        val granted =
            fixture.waitForCondition(GRANT_TIMEOUT_MS) {
                if (PermissionHelper.hasMediaPermission(context)) return@waitForCondition true
                findAllowAllButtonNow()?.let { button ->
                    retryCount++
                    Log.w(TAG, "Grant not yet registered; retrying \"Allow all\" tap (attempt ${retryCount + 1})")
                    try {
                        button.click()
                    } catch (e: StaleObjectException) {
                        Log.w(
                            TAG,
                            "\"Allow all\" button went stale between find and click (attempt " +
                                "${retryCount + 1}); the previous tap likely already registered " +
                                "and the dialog is tearing down, so treating this like a vanished " +
                                "button rather than failing the test",
                        )
                    }
                }
                false
            }
        assertTrue(
            "hasMediaPermission() should become true after tapping \"Allow all\" in the real " +
                "system permission dialog",
            granted,
        )

        // 2) The setup flow advances past the MEDIA step. SetupActivity.onResume runs
        //    autoAdvanceIfGranted() after the permission result; with MEDIA now granted (and
        //    NOTIFICATION/USAGE_ACCESS/OVERLAY already granted in the connectedE2EAndroidTest
        //    harness), the flow auto-advances to BATTERY, the first still-ungranted step. Waiting
        //    for the BATTERY step to appear (a positive assertion against a live compose hierarchy)
        //    proves real advancement, rather than vacuously passing on an absent hierarchy the way
        //    assertDoesNotExist() alone could (see SetupActivityGrantedE2ETest's class doc).
        composeRule.waitUntil(timeoutMillis = ADVANCE_TIMEOUT_MS) {
            composeRule.onAllNodesWithText(batteryTitle).fetchSemanticsNodes().isNotEmpty()
        }
        composeRule.onNodeWithText(batteryTitle).assertIsDisplayed()
        composeRule.onNodeWithText(mediaTitle).assertDoesNotExist()
    }

    /**
     * Waits for the real system permission dialog and taps its "Allow all" affordance.
     *
     * On API 33+ the READ_MEDIA_IMAGES request shows the photos/media grant dialog, whose
     * full-access button is `com.android.permissioncontroller:id/permission_allow_all_button`.
     * Older single-permission layouts use `permission_allow_button` instead; both are tried by
     * resource id, with a case-insensitive "Allow all"/"Allow" text match as a final fallback so
     * the test does not silently pass by failing to find the dialog.
     *
     * `device.waitForWindowUpdate()` is a cheap, early guard against the common case (the button
     * not existing in the tree yet), but two prior commits on this PR ruled out both a longer
     * pre-tap wait (a window-focus-transfer poll: the window takes focus in under 1 s, so there
     * is nothing slow to wait for) and a pre-tap `isEnabled()` poll (the button reported enabled
     * after ~100ms and the tap still silently failed) as fixes for the flake by themselves. See
     * the retry loop around this method's call site for the mechanism that actually addresses
     * it: AOSP's `SecureButton` class drops touches the system flags as window-obscured, with no
     * exception or log, and that is inherently a transient condition worth retrying rather than
     * waiting out.
     */
    private fun tapAllowAllInSystemDialog() {
        device.waitForWindowUpdate(PERMISSION_CONTROLLER_PKG, WINDOW_UPDATE_TIMEOUT_MS)

        val button = findAllowAllButton()
        requireNotNull(button) {
            "The system permission dialog's \"Allow all\" button was not found within " +
                "$DIALOG_TIMEOUT_MS ms after tapping the MEDIA step button. The requestPermissions() " +
                "dialog may not have appeared, or its resource ids/labels differ on this emulator."
        }
        button.click()
    }

    private fun findAllowAllButton(): UiObject2? =
        findDialogObject(By.res(PERMISSION_CONTROLLER_PKG, "permission_allow_all_button"))
            ?: findDialogObject(By.res(PERMISSION_CONTROLLER_PKG, "permission_allow_button"))
            ?: findDialogObject(By.textContains("Allow all"))
            ?: findDialogObject(By.text(Pattern.compile("Allow.*", Pattern.CASE_INSENSITIVE)))

    /**
     * Non-blocking counterpart to [findAllowAllButton], used by the retry poll in
     * [setupFlow_grantsMediaPermissionViaSystemDialog_andAdvances]: an immediate lookup (no
     * `device.wait()`) so a poll tick that finds nothing returns quickly instead of blocking for
     * up to [DIALOG_TIMEOUT_MS] per selector, which would stack badly inside a 100 ms poll loop.
     * `null` here means the dialog has already dismissed (the previous tap likely succeeded and
     * the grant is just propagating), not that it never appeared in the first place.
     */
    private fun findAllowAllButtonNow(): UiObject2? =
        device.findObject(By.res(PERMISSION_CONTROLLER_PKG, "permission_allow_all_button"))
            ?: device.findObject(By.res(PERMISSION_CONTROLLER_PKG, "permission_allow_button"))
            ?: device.findObject(By.textContains("Allow all"))
            ?: device.findObject(By.text(Pattern.compile("Allow.*", Pattern.CASE_INSENSITIVE)))

    private fun findDialogObject(selector: BySelector): UiObject2? = device.wait(Until.findObject(selector), DIALOG_TIMEOUT_MS)

    private companion object {
        const val TAG = "GB4PC_E2E"
        const val PERMISSION_CONTROLLER_PKG = "com.android.permissioncontroller"
        const val DIALOG_TIMEOUT_MS = 5_000L

        // The window-content-change event alone arrives too early to fix the flake on its own
        // (issue #581); this is just its own find-the-window budget, not expected to be load-bearing.
        const val WINDOW_UPDATE_TIMEOUT_MS = 5_000L

        // Widened from 10 s to 20 s (issue #604): this suite gained a `screenrecord` process for
        // the first time in issue #604 (see build.yml's "Run SetupActivityPermissionDialogE2ETest"
        // step), and the extra CPU load it puts on the CI emulator was enough to push the real,
        // asynchronous permission-result delivery past the previous 10 s budget in CI, failing
        // this assertion even though "Allow all" was tapped successfully (confirmed via CI logcat:
        // GrantPermissionsActivity opened and the dialog tap completed without the
        // requireNotNull(button) failure tapAllowAllInSystemDialog() would otherwise throw).
        const val GRANT_TIMEOUT_MS = 20_000L
        const val ADVANCE_TIMEOUT_MS = 10_000L
    }
}
