package com.gb4pc.service

import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import com.gb4pc.util.DebugLog
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.mockito.kotlin.*
import org.robolectric.RobolectricTestRunner

/**
 * Tests for Issue #80: ForegroundDetector must skip its own package name so that the
 * overlay window's presence does not displace the camera from the detected foreground app.
 *
 * UsageEvents.Event is a real Android framework class with public fields in Robolectric
 * (mPackage, mEventType, mTimeStamp). Robolectric is required so those fields are
 * accessible — they are stubs with default values under the plain JVM test runner.
 */
@Suppress("DEPRECATION") // MOVE_TO_FOREGROUND / MOVE_TO_BACKGROUND are deprecated by the SDK
                          // but are valid event types still exercised in mixed-event tests here.
@RunWith(RobolectricTestRunner::class)
class ForegroundDetectorSelfFilterTest {

    private val selfPkg = "com.gb4pc"
    private val cameraPkg = "com.google.android.GoogleCamera"
    private val otherPkg = "com.example.other"

    private lateinit var usm: UsageStatsManager
    private lateinit var detector: ForegroundDetector

    @Before
    fun setUp() {
        usm = mock()
        detector = ForegroundDetector(usm, selfPkg)
        DebugLog.clear()
    }

    /**
     * Helper: returns a mock UsageEvents whose getNextEvent() populates the passed Event
     * object with each successive event descriptor.
     *
     * Each descriptor is a Triple(packageName, eventType, timestamp).
     *
     * Field names (mPackage, mEventType, mTimeStamp) are set via reflection because the
     * compile-time android.jar exposes only stubs. Robolectric's android-all implementation
     * makes these fields public and settable at runtime.
     */
    private fun eventsOf(vararg specs: Triple<String, Int, Long>): UsageEvents {
        val specList = specs.toList()
        var index = 0
        val events: UsageEvents = mock()
        whenever(events.hasNextEvent()).thenAnswer { index < specList.size }
        whenever(events.getNextEvent(any())).thenAnswer { invocation ->
            val event = invocation.getArgument<UsageEvents.Event>(0)
            val spec = specList[index++]
            setEventField(event, "mPackage", spec.first)
            setEventField(event, "mEventType", spec.second)
            setEventField(event, "mTimeStamp", spec.third)
            true
        }
        whenever(usm.queryEvents(any(), any())).thenReturn(events)
        return events
    }

    private fun setEventField(event: UsageEvents.Event, fieldName: String, value: Any) {
        val field = event.javaClass.getDeclaredField(fieldName)
        field.isAccessible = true
        field.set(event, value)
    }

    // ── Self-filter: GB4PC events are skipped ───────────────────────────────

    @Test
    fun `self MOVE_TO_FOREGROUND alone returns null`() {
        // Only GB4PC itself appears in events — overlay is visible, no camera event.
        eventsOf(Triple(selfPkg, UsageEvents.Event.MOVE_TO_FOREGROUND, 1000L))

        assertNull(
            "GB4PC's own MOVE_TO_FOREGROUND must not be returned as the foreground package",
            detector.getForegroundPackage()
        )
    }

    @Test
    fun `self MOVE_TO_FOREGROUND does not displace earlier camera event`() {
        // Camera opened first, then GB4PC overlay fired its own event — camera should remain.
        eventsOf(
            Triple(cameraPkg, UsageEvents.Event.MOVE_TO_FOREGROUND, 1000L),
            Triple(selfPkg,    UsageEvents.Event.MOVE_TO_FOREGROUND, 2000L),
        )

        assertEquals(
            "Camera package must remain the detected foreground app when GB4PC fires after it (Issue #80)",
            cameraPkg,
            detector.getForegroundPackage()
        )
    }

    @Test
    fun `self MOVE_TO_FOREGROUND between two other apps is skipped`() {
        // Some other app, then GB4PC overlay, then camera — camera wins on timestamp.
        eventsOf(
            Triple(otherPkg,  UsageEvents.Event.MOVE_TO_FOREGROUND, 1000L),
            Triple(selfPkg,   UsageEvents.Event.MOVE_TO_FOREGROUND, 2000L),
            Triple(cameraPkg, UsageEvents.Event.MOVE_TO_FOREGROUND, 3000L),
        )

        assertEquals(
            "Camera package (latest non-self MOVE_TO_FOREGROUND) must be detected",
            cameraPkg,
            detector.getForegroundPackage()
        )
    }

    // ── Edge cases: genuine navigation away from camera ─────────────────────

    @Test
    fun `user navigating to another app is still detected`() {
        // Camera first, then user opens another app — that app should be returned.
        eventsOf(
            Triple(cameraPkg, UsageEvents.Event.MOVE_TO_FOREGROUND, 1000L),
            Triple(otherPkg,  UsageEvents.Event.MOVE_TO_FOREGROUND, 2000L),
        )

        assertEquals(
            "When the user genuinely navigates away, the new foreground app must be detected",
            otherPkg,
            detector.getForegroundPackage()
        )
    }

    @Test
    fun `self event after genuine navigation does not restore camera`() {
        // Camera → user navigates away → GB4PC overlay fires (shouldn't matter) → other stays.
        eventsOf(
            Triple(cameraPkg, UsageEvents.Event.MOVE_TO_FOREGROUND, 1000L),
            Triple(otherPkg,  UsageEvents.Event.MOVE_TO_FOREGROUND, 2000L),
            Triple(selfPkg,   UsageEvents.Event.MOVE_TO_FOREGROUND, 3000L),
        )

        assertEquals(
            "The other app must remain detected even when GB4PC fires after it",
            otherPkg,
            detector.getForegroundPackage()
        )
    }

    @Test
    fun `multiple self events are all skipped`() {
        eventsOf(
            Triple(selfPkg, UsageEvents.Event.MOVE_TO_FOREGROUND, 1000L),
            Triple(selfPkg, UsageEvents.Event.MOVE_TO_FOREGROUND, 2000L),
            Triple(selfPkg, UsageEvents.Event.MOVE_TO_FOREGROUND, 3000L),
        )

        assertNull(
            "Multiple GB4PC self-events must all be skipped, returning null",
            detector.getForegroundPackage()
        )
    }

    @Test
    fun `non-foreground events for self package are not affected`() {
        // Self fires a non-foreground event type — should not interfere with detection.
        eventsOf(
            Triple(cameraPkg, UsageEvents.Event.MOVE_TO_FOREGROUND,  1000L),
            Triple(selfPkg,   UsageEvents.Event.MOVE_TO_BACKGROUND,  2000L),
        )

        assertEquals(
            "Non-foreground events for selfPkg must not interfere with camera detection",
            cameraPkg,
            detector.getForegroundPackage()
        )
    }

    // ── ACTIVITY_RESUMED (API 29+): replaces MOVE_TO_FOREGROUND on modern devices ──

    @Test
    fun `ACTIVITY_RESUMED for camera is detected (Issue #86)`() {
        // On API 29+ the system emits ACTIVITY_RESUMED instead of MOVE_TO_FOREGROUND.
        // The detector must recognise it as a foreground event.
        eventsOf(Triple(cameraPkg, UsageEvents.Event.ACTIVITY_RESUMED, 1000L))

        assertEquals(
            "ACTIVITY_RESUMED must be recognised as a foreground event (Issue #86)",
            cameraPkg,
            detector.getForegroundPackage()
        )
    }

    @Test
    fun `self ACTIVITY_RESUMED is skipped (Issue #86)`() {
        // GB4PC's own ACTIVITY_RESUMED must be filtered out, same as MOVE_TO_FOREGROUND.
        eventsOf(Triple(selfPkg, UsageEvents.Event.ACTIVITY_RESUMED, 1000L))

        assertNull(
            "Self ACTIVITY_RESUMED must be skipped (Issue #86)",
            detector.getForegroundPackage()
        )
    }

    @Test
    fun `self ACTIVITY_RESUMED does not displace earlier camera ACTIVITY_RESUMED (Issue #86)`() {
        eventsOf(
            Triple(cameraPkg, UsageEvents.Event.ACTIVITY_RESUMED, 1000L),
            Triple(selfPkg,   UsageEvents.Event.ACTIVITY_RESUMED, 2000L),
        )

        assertEquals(
            "Camera ACTIVITY_RESUMED must not be displaced by self ACTIVITY_RESUMED (Issue #86)",
            cameraPkg,
            detector.getForegroundPackage()
        )
    }

    @Test
    fun `camera ACTIVITY_RESUMED beats older launcher MOVE_TO_FOREGROUND (Issue #86)`() {
        // Real-world scenario on Android 10+: launcher has an old MOVE_TO_FOREGROUND event
        // from before the user launched Pixel Camera, which then emits ACTIVITY_RESUMED.
        eventsOf(
            Triple("com.android.launcher3", UsageEvents.Event.MOVE_TO_FOREGROUND, 1000L),
            Triple(cameraPkg,               UsageEvents.Event.ACTIVITY_RESUMED,   2000L),
        )

        assertEquals(
            "Camera ACTIVITY_RESUMED (newer) must win over older launcher MOVE_TO_FOREGROUND (Issue #86)",
            cameraPkg,
            detector.getForegroundPackage()
        )
    }

    @Test
    fun `launcher MOVE_TO_FOREGROUND alone does not match camera (Issue #86)`() {
        // If only a launcher MOVE_TO_FOREGROUND event is present (no camera event yet),
        // isPixelCameraPackage must return false so the retry logic can kick in.
        eventsOf(
            Triple("com.android.launcher3", UsageEvents.Event.MOVE_TO_FOREGROUND, 1000L),
        )

        val pkg = detector.getForegroundPackage()
        assertFalse(
            "Launcher MOVE_TO_FOREGROUND must not be detected as Pixel Camera (Issue #86)",
            ForegroundDetector.isPixelCameraPackage(pkg)
        )
    }

    // ── Issue #324: summary log must list all FG apps, not just the top one ──

    @Test
    fun `summary log includes all FG apps when multiple packages appear (Issue #324)`() {
        // Three distinct packages appear in the window; the summary log must list all of them.
        eventsOf(
            Triple("com.android.launcher3", UsageEvents.Event.MOVE_TO_FOREGROUND, 1000L),
            Triple(otherPkg,                UsageEvents.Event.MOVE_TO_FOREGROUND, 2000L),
            Triple(cameraPkg,               UsageEvents.Event.MOVE_TO_FOREGROUND, 3000L),
        )

        detector.getForegroundPackage()

        val summaryLine = DebugLog.getEntries()
            .map { it.message }
            .last { it.startsWith("ForegroundDetector: foreground=") }
        assertTrue(
            "Summary log must include 'all FG apps=' with the full package list (Issue #324). Got: $summaryLine",
            summaryLine.contains("all FG apps=")
        )
        assertTrue(
            "Summary log must include launcher package. Got: $summaryLine",
            summaryLine.contains("com.android.launcher3")
        )
        assertTrue(
            "Summary log must include otherPkg. Got: $summaryLine",
            summaryLine.contains(otherPkg)
        )
        assertTrue(
            "Summary log must include cameraPkg. Got: $summaryLine",
            summaryLine.contains(cameraPkg)
        )
    }

    @Test
    fun `summary log all FG apps excludes self package (Issue #324)`() {
        // When selfPkg appears among events, it must not appear in the all FG apps list.
        eventsOf(
            Triple(cameraPkg, UsageEvents.Event.MOVE_TO_FOREGROUND, 1000L),
            Triple(selfPkg,   UsageEvents.Event.MOVE_TO_FOREGROUND, 2000L),
        )

        detector.getForegroundPackage()

        val summaryLine = DebugLog.getEntries()
            .map { it.message }
            .last { it.startsWith("ForegroundDetector: foreground=") }
        assertTrue(
            "Summary log must contain 'all FG apps='. Got: $summaryLine",
            summaryLine.contains("all FG apps=")
        )
        assertFalse(
            "Self package must not appear in the all FG apps list (Issue #324). Got: $summaryLine",
            summaryLine.contains(selfPkg)
        )
        assertTrue(
            "Camera package must appear in the all FG apps list. Got: $summaryLine",
            summaryLine.contains(cameraPkg)
        )
    }

    @Test
    fun `summary log all FG apps shows single package when only one foreground app (Issue #324)`() {
        eventsOf(Triple(cameraPkg, UsageEvents.Event.MOVE_TO_FOREGROUND, 1000L))

        detector.getForegroundPackage()

        val summaryLine = DebugLog.getEntries()
            .map { it.message }
            .last { it.startsWith("ForegroundDetector: foreground=") }
        assertTrue(
            "Summary log must contain 'all FG apps=' even with a single package. Got: $summaryLine",
            summaryLine.contains("all FG apps=")
        )
        assertTrue(
            "Single foreground package must appear in the list. Got: $summaryLine",
            summaryLine.contains(cameraPkg)
        )
    }
}
