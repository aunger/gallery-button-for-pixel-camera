package com.gb4pc.e2e

import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.gb4pc.R
import com.gb4pc.ui.setup.SetupActivity
import org.junit.Rule
import org.junit.Test
import org.junit.rules.ExternalResource
import org.junit.rules.RuleChain
import org.junit.runner.RunWith

/**
 * E2E regression coverage for issue #509 / #566 (UI half), granted-permission side: confirms the
 * guided setup flow auto-advances past the Photos & Media step without ever showing it, when the
 * permission is already granted before `SetupActivity` starts (this class's precondition, the
 * default `connectedE2EAndroidTest` state; PM-02's existing auto-advance behavior, exercised here
 * for the MEDIA step specifically).
 *
 * See [SetupActivityDeniedE2ETest]'s class doc for why this lives in its own file using
 * `createAndroidComposeRule<SetupActivity>()`, rather than folded into [PermissionsGrantedE2ETest]
 * using `createEmptyComposeRule()` plus a manually launched `ActivityScenario` (PR #564, commit
 * c5b75f3): that combination let this exact test pass in CI, but only because `assertDoesNotExist()`
 * vacuously succeeds when Compose's test framework finds no hierarchy at all to search, which is
 * exactly what was happening, not because the MEDIA step was actually confirmed absent. The
 * sibling denied-precondition test using `assertIsDisplayed()` (which does require a hierarchy)
 * failed loudly with the same underlying problem, which is what exposed it.
 *
 * Run via a dedicated CI step: `connectedE2EAndroidTest -Pe2eClass=com.gb4pc.e2e.SetupActivityGrantedE2ETest`
 * (see `.github/workflows/build.yml`), using the task's default precondition, same as
 * [PermissionsGrantedE2ETest]'s own step.
 *
 * ### Keyguard dismissal
 *
 * This class happened to pass in CI without any keyguard handling, while its sibling
 * [SetupActivityDeniedE2ETest] failed with `IllegalStateException: No compose hierarchies found
 * in the app` (`SetupActivity` reaching `RESUMED` then `PAUSED` again 45 ms later, per CI
 * logcat), consistent with the keyguard reasserting itself between the CI step's one-time,
 * pre-Gradle shell dismissal and the activity's actual launch. This class simply runs earlier in
 * the job's E2E sequence (right after `PermissionsGrantedE2ETest`, whose own dismissal likely
 * left the screen awake), so it dodged the same race rather than being immune to it, per
 * [SetupActivityDeniedE2ETest]'s class doc.
 *
 * Fixed here too with the identical [RuleChain]-based dismissal, so this test does not silently
 * start flaking the next time CI step ordering or timing shifts, and using the same
 * [E2EFixture.dismissSecureKeyguard] (not [E2EFixture.wakeAndDismissKeyguard], which only
 * performs a swipe and does nothing against this suite's PIN-secured lock screen; see
 * [SetupActivityDeniedE2ETest]'s class doc for the incident where a swipe-only dismissal here
 * still let the same failure recur for the sibling test).
 */
@E2ETest
@RunWith(AndroidJUnit4::class)
class SetupActivityGrantedE2ETest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()

    private val keyguardDismiss =
        object : ExternalResource() {
            override fun before() {
                E2EFixture(
                    context = instrumentation.targetContext,
                    uiAutomation = instrumentation.uiAutomation,
                ).dismissSecureKeyguard()
            }
        }

    private val composeRule = createAndroidComposeRule<SetupActivity>()

    private val testNameToastRule = TestNameToastRule()

    @get:Rule
    val ruleChain: RuleChain =
        RuleChain.outerRule(testNameToastRule).around(keyguardDismiss).around(composeRule)

    @Test
    fun setupFlow_skipsMediaStep_whenPermissionAlreadyGranted() {
        val context = instrumentation.targetContext

        composeRule.onNodeWithText(context.getString(R.string.setup_media_title)).assertDoesNotExist()
    }
}
