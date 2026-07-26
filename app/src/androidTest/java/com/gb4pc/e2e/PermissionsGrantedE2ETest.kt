package com.gb4pc.e2e

import android.content.Context
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.gb4pc.util.DebugLog
import com.gb4pc.util.PermissionHelper
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.ExternalResource
import org.junit.rules.RuleChain
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

    // Dismiss the PIN-secured keyguard before the start-of-test toast so the slate renders over the
    // app UI rather than behind the lock screen (issue #761). The suite's own setUp() only wires
    // fixture.wakeAndDismissKeyguard() (a swipe), which does nothing against the secure keyguard
    // scripts/setup-e2e-emulator.sh configures, and it runs from @Before, after the toast has
    // already fired; if the keyguard has reasserted between CI steps the marker would be occluded.
    private val keyguardDismiss =
        object : ExternalResource() {
            override fun before() {
                fixture.dismissSecureKeyguard()
            }
        }

    private val testNameToastRule = TestNameToastRule()

    @get:Rule
    val ruleChain: RuleChain =
        // keyguardDismiss is outermost so the secure keyguard is cleared before testNameToastRule
        // (innermost) shows the slate; the toast's ~3s duration therefore sits after dismissal,
        // preserving the keyguard-dismissal-then-launch ordering TestNameToastRule.kt documents (the
        // camera launch itself happens later, in each test body, via fixture.launchPixelCamera()).
        RuleChain.outerRule(keyguardDismiss).around(testNameToastRule)

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
}
