package com.gb4pc.service

import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import org.mockito.kotlin.any
import org.mockito.kotlin.mock
import org.mockito.kotlin.whenever

/**
 * Test fixture shared by the [ForegroundDetector] test classes: stubs [usm] so that every
 * queryEvents() call returns a [UsageEvents] whose getNextEvent() populates the passed Event
 * object with each successive descriptor in [specs].
 *
 * Each descriptor is a Triple(packageName, eventType, timestamp).
 *
 * Each queryEvents() call gets its own freshly positioned event stream, so a test may drive the
 * detector more than once and see the same window every time.
 *
 * Callers must run under RobolectricTestRunner: the field names below exist only on Robolectric's
 * android-all implementation of [UsageEvents.Event], because the compile-time android.jar exposes
 * the class as a stub whose fields cannot be set.
 */
fun stubUsageEvents(
    usm: UsageStatsManager,
    vararg specs: Triple<String, Int, Long>,
) {
    val specList = specs.toList()
    whenever(usm.queryEvents(any(), any())).thenAnswer { usageEventsOf(specList) }
}

private fun usageEventsOf(specs: List<Triple<String, Int, Long>>): UsageEvents {
    var index = 0
    val events: UsageEvents = mock()
    whenever(events.hasNextEvent()).thenAnswer { index < specs.size }
    whenever(events.getNextEvent(any())).thenAnswer { invocation ->
        val event = invocation.getArgument<UsageEvents.Event>(0)
        val spec = specs[index++]
        setEventField(event, "mPackage", spec.first)
        setEventField(event, "mEventType", spec.second)
        setEventField(event, "mTimeStamp", spec.third)
        true
    }
    return events
}

private fun setEventField(
    event: UsageEvents.Event,
    fieldName: String,
    value: Any,
) {
    val field = event.javaClass.getDeclaredField(fieldName)
    field.isAccessible = true
    field.set(event, value)
}
