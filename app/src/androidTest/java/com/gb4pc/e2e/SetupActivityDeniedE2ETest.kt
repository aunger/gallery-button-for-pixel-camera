package com.gb4pc.e2e

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.gb4pc.R
import com.gb4pc.ui.setup.SetupActivity
import com.gb4pc.util.PermissionHelper
import org.junit.Assert.assertFalse
import org.junit.Rule
import org.junit.Test
import org.junit.rules.ExternalResource
import org.junit.rules.RuleChain
import org.junit.runner.RunWith

/**
 * E2E regression coverage for issue #509 / #566 (UI half), denied-permission side: confirms the
 * guided setup flow actually reaches and displays the Photos & Media step when the permission is
 * not yet granted at launch, rather than skipping it or (worse) crashing.
 *
 * ### Why this is a separate file from [PermissionsDeniedE2ETest], and why it uses
 * ### `createAndroidComposeRule<SetupActivity>()` instead of `createEmptyComposeRule()`
 *
 * An earlier version of this coverage (PR #564, commit c5b75f3) added this assertion directly to
 * [PermissionsDeniedE2ETest], using `createEmptyComposeRule()` plus a manually launched
 * `ActivityScenario.launch(SetupActivity::class.java)`. That combination does not reliably let
 * Compose's test framework find `SetupActivity`'s compose hierarchy: CI failed with
 * `IllegalStateException: No compose hierarchies found in the app`, thrown from
 * `assertIsDisplayed()`. Worse, the mirrored test in `PermissionsGrantedE2ETest` (asserting the
 * step is *absent* via `assertDoesNotExist()`) had the identical underlying problem but never
 * surfaced it: `assertDoesNotExist()` internally fetches nodes with `atLeastOneRootRequired =
 * false`, so it passes vacuously when no compose hierarchy is found at all, rather than actually
 * confirming the step is absent. Both tests were unreliable; only one visibly failed.
 *
 * The fix is this dedicated file, using the same `createAndroidComposeRule<SetupActivity>()`
 * class-level rule [SetupActivityTest] already relies on successfully (that file has passed in
 * CI across every round of this PR). The rule launches `SetupActivity` itself and is guaranteed
 * to be wired into Compose's test synchronization before any assertion runs, unlike a manually
 * launched `ActivityScenario` alongside an unrelated empty rule.
 *
 * `POST_NOTIFICATIONS` is granted unconditionally by `connectedE2EAndroidTest` (see
 * [PermissionsDeniedE2ETest]'s class doc), so the NOTIFICATION step auto-advances on its own here
 * and MEDIA is the first step `SetupActivity` shows.
 *
 * Stops at asserting the step is showing; does not tap the button or call `pm grant` to complete
 * it, since either would need to interact with (or bypass) the real system permission dialog, and
 * `pm grant` specifically would reproduce the process-crash fixed in commit e5d37ed if issued
 * while this process is alive. See [SetupActivityGrantedE2ETest] for the granted-precondition
 * half.
 *
 * Run via a dedicated CI step: `connectedE2EAndroidTest -Pe2eClass=com.gb4pc.e2e.SetupActivityDeniedE2ETest
 * -PmediaPermissionGranted=false` (see `.github/workflows/build.yml`), mirroring
 * [PermissionsDeniedE2ETest]'s own step.
 *
 * ### Keyguard dismissal
 *
 * A first version of this file omitted any keyguard handling, on the theory that the CI step's
 * own `input keyevent KEYCODE_WAKEUP` + swipe (issued from the shell, before Gradle even starts)
 * would be enough, matching [SetupActivityTest] (which also does not call
 * [E2EFixture.wakeAndDismissKeyguard]). That worked for [SetupActivityGrantedE2ETest] but not
 * here: CI logcat showed this class's `SetupActivity` reaching `RESUMED` and then `PAUSED` again
 * 45 ms later, consistent with the keyguard (this suite runs under a PIN-secured lock screen, see
 * `scripts/setup-e2e-emulator.sh`) re-engaging in the gap between the CI step's one-time,
 * pre-Gradle shell dismissal and this specific class's turn to launch its activity, several
 * suites later in the same job. `SetupActivityTest` avoids this because the CI script's
 * `connectedDebugAndroidTest` step wakes/dismisses immediately before that whole (short) suite
 * runs, with no other E2E suites in between to let the keyguard reassert itself.
 *
 * The fix: dismiss the keyguard from *inside* the test process, immediately before
 * `SetupActivity` launches, reusing [E2EFixture.wakeAndDismissKeyguard] (the exact mechanism
 * every other, reliably-passing E2E suite in this package already uses via `fixture.setUp()`).
 * A plain `@Before` method would run too late here: `createAndroidComposeRule`'s underlying
 * `ActivityScenarioRule` launches the activity as part of the *rule's* `before()`, which JUnit
 * runs ahead of the test class's own `@Before` methods. [RuleChain.outerRule] instead runs the
 * keyguard dismissal's `before()` ahead of the compose rule's own, mirroring the
 * `RuleChain.outerRule(prefsSetup).around(composeRule)` pattern already used in
 * `MainActivityTest`'s `MainSettingsScreenTest`.
 */
@E2ETest
@RunWith(AndroidJUnit4::class)
class SetupActivityDeniedE2ETest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()

    private val keyguardDismiss =
        object : ExternalResource() {
            override fun before() {
                E2EFixture(
                    context = instrumentation.targetContext,
                    uiAutomation = instrumentation.uiAutomation,
                ).wakeAndDismissKeyguard()
            }
        }

    private val composeRule = createAndroidComposeRule<SetupActivity>()

    @get:Rule
    val ruleChain: RuleChain = RuleChain.outerRule(keyguardDismiss).around(composeRule)

    @Test
    fun setupFlow_reachesMediaStep_whenPermissionNotGranted() {
        val context = instrumentation.targetContext

        composeRule.onNodeWithText(context.getString(R.string.setup_media_title)).assertIsDisplayed()
        composeRule.onNodeWithText(context.getString(R.string.setup_media_button)).assertIsDisplayed()
        assertFalse(
            "hasMediaPermission should still be false while the Photos & Media step is showing",
            PermissionHelper.hasMediaPermission(context),
        )
    }
}
