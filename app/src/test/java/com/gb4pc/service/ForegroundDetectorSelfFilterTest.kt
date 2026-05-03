package com.gb4pc.service

import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
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
                          // but are the correct event types for pre-API-29 paths exercised here.
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
}
