package com.gb4pc.service

import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.os.Handler
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
 * Robolectric is required so that [UsageEvents.Event]'s fields can be populated; see
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
    private lateinit var handler: Handler
    private lateinit var cameraState: CameraState
    private lateinit var detector: ForegroundDetector
    private lateinit var logic: OverlayServiceLogic

    @Before
    fun setUp() {
        usm = mock()
        overlayManager = mock()
        sessionTracker = mock()
        handler = mock()
        cameraState = CameraState()
        detector = ForegroundDetector(usm, selfPkg)
        logic =
            OverlayServiceLogic(
                hasUsageStatsPermission = { true },
                hasOverlayPermission = { true },
                overlayManager = overlayManager,
                cameraState = cameraState,
                foregroundDetector = detector,
                sessionTracker = sessionTracker,
                handler = handler,
                debounceMs = 0L,
                onUsageAccessLost = {},
                onOverlayPermissionLost = {},
                isKeyguardLocked = { false },
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
    fun `repeat occurrences within one session are counted`() {
        launcherWinsWindow()

        logic.onCameraUnavailable("0")
        logic.evaluateForeground()
        logic.evaluateForeground()

        val signals = raceSignals()
        assertEquals("Every occurrence must be logged", 3, signals.size)
        assertTrue("First occurrence must be numbered #1: ${signals[0]}", signals[0].contains("#1"))
        assertTrue("Second occurrence must be numbered #2: ${signals[1]}", signals[1].contains("#2"))
        assertTrue("Third occurrence must be numbered #3: ${signals[2]}", signals[2].contains("#3"))
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

    // ── Activation behavior is unchanged ────────────────────────────────────

    @Test
    fun `the race signal does not disturb the activation retry`() {
        launcherWinsWindow()

        logic.onCameraUnavailable("0")

        // DT-06a still treats a non-camera winner with a held camera as possible UsageStats lag.
        verify(handler).postDelayed(any(), eq(Constants.ACTIVATION_RETRY_MS))
    }
}
