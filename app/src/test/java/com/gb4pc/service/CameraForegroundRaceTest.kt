package com.gb4pc.service

import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.os.Handler
import android.os.Looper
import com.gb4pc.Constants
import com.gb4pc.overlay.OverlayManager
import com.gb4pc.util.DebugLog
import com.gb4pc.viewer.SessionTracker
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.mockito.kotlin.*
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import java.time.Duration

/**
 * Issue #907: pins the behavior of, and the diagnostic signal for, the Issue #86 race where
 * another app wins the foreground lookup while Pixel Camera is holding the camera.
 *
 * Unlike [OverlayServiceLogicTest], which mocks [ForegroundDetector] and dictates its answer,
 * these tests drive a real detector over a real [OverlayServiceLogic] and control the situation
 * only through the UsageStats event window, because the ordering of those events is the whole
 * subject: the launcher carries the latest ACTIVITY_RESUMED, Pixel Camera carries an earlier one
 * inside the same window, and a camera is held throughout.
 *
 * The activation assertions are characterization assertions. They record what the code does today
 * (the overlay stays hidden), not what it ought to do. Issue #86 proposes a camera-corroborated
 * activation predicate, and landing it is exactly what should flip them; a failure here after that
 * change is the expected signal, not a regression.
 *
 * The Handler is a real main-looper one rather than a mock, and Robolectric leaves that looper
 * paused, so posted work runs only when a test asks for it via [idleRetryChain]. That matters
 * here: [OverlayServiceLogic]'s DT-06a retry chain re-enters `evaluateForeground()` several times
 * per camera open, which is exactly the traffic a diagnostic counter has to survive, and a mocked
 * Handler would silently drop all of it.
 *
 * Robolectric is also required so that [UsageEvents.Event]'s fields can be populated; see
 * [stubUsageEvents].
 */
@RunWith(RobolectricTestRunner::class)
class CameraForegroundRaceTest {
    private val selfPkg = "com.gb4pc"
    private val cameraPkg = Constants.PIXEL_CAMERA_PACKAGE
    private val launcherPkg = "com.google.android.apps.nexuslauncher"

    private lateinit var usm: UsageStatsManager
    private lateinit var overlayManager: OverlayManager
    private lateinit var sessionTracker: SessionTracker
    private lateinit var cameraState: CameraState
    private var keyguardLocked = false
    private lateinit var detector: ForegroundDetector
    private lateinit var logic: OverlayServiceLogic

    @Before
    fun setUp() {
        usm = mock()
        overlayManager = mock()
        sessionTracker = mock()
        cameraState = CameraState()
        keyguardLocked = false
        detector = ForegroundDetector(usm, selfPkg)
        logic =
            OverlayServiceLogic(
                hasUsageStatsPermission = { true },
                hasOverlayPermission = { true },
                overlayManager = overlayManager,
                cameraState = cameraState,
                foregroundDetector = detector,
                sessionTracker = sessionTracker,
                handler = Handler(Looper.getMainLooper()),
                debounceMs = 0L,
                onUsageAccessLost = {},
                onOverlayPermissionLost = {},
                isKeyguardLocked = { keyguardLocked },
                onRegisterMediaObserver = {},
                onUnregisterMediaObserver = {},
            )
        DebugLog.clear()
    }

    /** The Issue #86 window: Pixel Camera resumed first, the launcher resumed after it. */
    private fun launcherWinsWindow() =
        stubUsageEvents(
            usm,
            Triple(cameraPkg, UsageEvents.Event.ACTIVITY_RESUMED, 1_000L),
            Triple(launcherPkg, UsageEvents.Event.ACTIVITY_RESUMED, 2_000L),
        )

    private fun raceSignals(): List<String> =
        DebugLog
            .getEntries()
            .map { it.message }
            .filter { it.contains(OverlayServiceLogic.CAMERA_FOREGROUND_RACE) }

    /**
     * Runs the posted DT-06a retry chain to exhaustion by advancing the (paused) main looper past
     * the whole retry budget. The Handler here is a real main-looper Handler precisely so that
     * this is possible: a mock would swallow every runnable, and the retry chain is the one path
     * most likely to interact with anything counted per evaluation.
     */
    private fun idleRetryChain() =
        shadowOf(Looper.getMainLooper()).idleFor(
            Duration.ofMillis(Constants.ACTIVATION_RETRY_MS * (Constants.ACTIVATION_RETRY_MAX_ATTEMPTS + 1)),
        )

    private fun retryFirings(): Int = DebugLog.getEntries().count { it.message.contains("activation retry firing") }

    // ── Characterization: what happens today ────────────────────────────────

    @Test
    fun `launcher wins the window while the camera is held and the overlay stays hidden`() {
        launcherWinsWindow()

        logic.onCameraUnavailable("0")

        assertTrue("The camera must still be held for this to be the Issue #86 case", cameraState.anyCameraUnavailable())
        assertFalse(
            "Characterization (Issue #907): with a non-camera winner the overlay does not activate, " +
                "even though Pixel Camera is holding the camera and appears in the same window. " +
                "Issue #86's camera-corroborated predicate is what should flip this.",
            logic.isOverlayActive,
        )
        verify(overlayManager, never()).show()
    }

    @Test
    fun `pixel camera winning the same window activates the overlay`() {
        // The other ordering, as a control: it is the timestamps, not the presence of the
        // launcher, that decide the outcome today.
        stubUsageEvents(
            usm,
            Triple(launcherPkg, UsageEvents.Event.ACTIVITY_RESUMED, 1_000L),
            Triple(cameraPkg, UsageEvents.Event.ACTIVITY_RESUMED, 2_000L),
        )

        logic.onCameraUnavailable("0")

        assertTrue("Pixel Camera as the latest foreground event must activate the overlay", logic.isOverlayActive)
        assertTrue("The race signal must not fire when Pixel Camera wins", raceSignals().isEmpty())
    }

    // ── The named, counted diagnostic signal ────────────────────────────────

    @Test
    fun `the race is logged by name with both packages and the overlay state`() {
        launcherWinsWindow()

        logic.onCameraUnavailable("0")

        val signals = raceSignals()
        assertEquals("Exactly one race signal is expected for one evaluation", 1, signals.size)
        val signal = signals.single()
        assertTrue("The signal must name the winning package: $signal", signal.contains(launcherPkg))
        assertTrue("The signal must name Pixel Camera as a candidate: $signal", signal.contains(cameraPkg))
        assertTrue("The signal must report the overlay state: $signal", signal.contains("overlayActive=false"))
        assertTrue("The signal must point at the issue it is evidence for: $signal", signal.contains("Issue #86"))
    }

    @Test
    fun `each camera-open sequence that races is one more numbered episode`() {
        launcherWinsWindow()

        // Camera opened, raced, and closed again without the overlay ever appearing.
        logic.onCameraUnavailable("0")
        logic.onCameraAvailable("0")
        // A second, separate camera open that races the same way.
        logic.onCameraUnavailable("0")

        val signals = raceSignals()
        assertEquals("Two camera opens that both race must be two episodes", 2, signals.size)
        assertTrue("First episode must be numbered #1: ${signals[0]}", signals[0].contains("#1"))
        assertTrue("Second episode must be numbered #2: ${signals[1]}", signals[1].contains("#2"))
    }

    @Test
    fun `the DT-06a retry chain re-observes one episode without re-counting it`() {
        launcherWinsWindow()

        logic.onCameraUnavailable("0")
        idleRetryChain()

        // The retry chain is untouched: it still re-checks for UsageStats lag the full budget of
        // times, and each of those re-entries re-reads the same window and re-sees the fingerprint.
        assertEquals(
            "DT-06a must still retry its full budget",
            Constants.ACTIVATION_RETRY_MAX_ATTEMPTS,
            retryFirings(),
        )
        assertEquals(
            "One camera open is one episode, however many times the retry chain re-observes it. " +
                "Counting per evaluation would report this single failure as " +
                "${Constants.ACTIVATION_RETRY_MAX_ATTEMPTS + 1}, inflating a device log by that factor.",
            1,
            raceSignals().size,
        )
        assertFalse("The overlay still never appears", logic.isOverlayActive)
    }

    @Test
    fun `an episode the retry resolves is marked resolved, not left looking like a miss`() {
        launcherWinsWindow()
        logic.onCameraUnavailable("0")
        assertEquals("The race is reported first", 1, raceSignals().size)

        // UsageStats catches up mid-chain: Pixel Camera now carries the latest event, so a retry
        // activates the overlay after all. This episode was lag resolving, not a missed overlay.
        stubUsageEvents(
            usm,
            Triple(launcherPkg, UsageEvents.Event.ACTIVITY_RESUMED, 2_000L),
            Triple(cameraPkg, UsageEvents.Event.ACTIVITY_RESUMED, 3_000L),
        )
        idleRetryChain()

        assertTrue("The overlay activates once Pixel Camera wins a later lookup", logic.isOverlayActive)
        val resolution = raceSignals().single { it.contains("resolved") }
        assertTrue("The resolution must name the episode it closes: $resolution", resolution.contains("#1"))
    }

    @Test
    fun `an episode the lock-screen bypass resolves is marked resolved (Issue #81)`() {
        launcherWinsWindow()
        logic.onCameraUnavailable("0")
        assertEquals("The race is reported first", 1, raceSignals().size)

        // The screen turns off inside the retry window, so the next DT-06a retry takes Issue #81's
        // lock-screen bypass instead of the UsageStats path, and the overlay appears that way.
        keyguardLocked = true
        idleRetryChain()

        assertTrue("The lock-screen bypass activates the overlay", logic.isOverlayActive)
        val resolution = raceSignals().single { it.contains("resolved") }
        assertTrue("The resolution must name the episode it closes: $resolution", resolution.contains("#1"))
        assertTrue("The resolution must name the path that recovered: $resolution", resolution.contains("Issue #81"))
    }

    @Test
    fun `an episode resolved by regaining overlay focus is marked resolved (Issue #92)`() {
        launcherWinsWindow()
        logic.onCameraUnavailable("0")
        assertEquals("The race is reported first", 1, raceSignals().size)

        // Issue #92's focusable-overlay path activates without going through evaluateForeground()
        // at all, so it is the one activation that could leave an episode looking like a miss.
        logic.onOverlayFocusGained()

        assertTrue("Regaining focus activates the overlay", logic.isOverlayActive)
        val resolution = raceSignals().single { it.contains("resolved") }
        assertTrue("The resolution must name the episode it closes: $resolution", resolution.contains("#1"))
        assertTrue("The resolution must name the path that recovered: $resolution", resolution.contains("Issue #92"))
    }

    @Test
    fun `a resolved episode is not re-resolved when the same sequence activates again`() {
        launcherWinsWindow()
        logic.onCameraUnavailable("0")
        logic.onOverlayFocusGained()
        assertEquals("One resolution so far", 1, raceSignals().count { it.contains("resolved") })

        // Focus lost and regained again inside the same camera hold: the episode is already closed,
        // so nothing further is claimed about it.
        logic.onOverlayFocusLost()
        logic.onOverlayFocusGained()

        assertEquals(
            "An episode must be resolved once, not once per activation",
            1,
            raceSignals().count { it.contains("resolved") },
        )
    }

    @Test
    fun `the race is not reported when no camera is held`() {
        launcherWinsWindow()

        // Same event window, but nothing is holding the camera, so nothing is being missed.
        logic.evaluateForeground()

        assertTrue("Without a held camera there is no race to report", raceSignals().isEmpty())
    }

    @Test
    fun `the race is not reported when pixel camera is absent from the window`() {
        stubUsageEvents(usm, Triple(launcherPkg, UsageEvents.Event.ACTIVITY_RESUMED, 2_000L))

        logic.onCameraUnavailable("0")

        assertFalse("A non-camera app alone must not activate the overlay", logic.isOverlayActive)
        assertTrue(
            "Some other app holding the camera is not the Issue #86 race and must not be reported as it",
            raceSignals().isEmpty(),
        )
    }

    @Test
    fun `the race is reported with overlayActive=true when the overlay is already showing`() {
        // Benign variant: the overlay is already up and another app comes to the front over the
        // camera. The condition still holds, so it is still logged, and the overlay state in the
        // message is what separates this from the failure the signal exists to catch.
        stubUsageEvents(
            usm,
            Triple(launcherPkg, UsageEvents.Event.ACTIVITY_RESUMED, 1_000L),
            Triple(cameraPkg, UsageEvents.Event.ACTIVITY_RESUMED, 2_000L),
        )
        logic.onCameraUnavailable("0")
        assertTrue(logic.isOverlayActive)

        launcherWinsWindow()
        logic.evaluateForeground()

        assertTrue("The overlay must stay active; this change is observability only", logic.isOverlayActive)
        val signal = raceSignals().single()
        assertTrue("The signal must report the overlay as active: $signal", signal.contains("overlayActive=true"))
    }
}
