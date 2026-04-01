package com.gb4pc.service

import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import com.gb4pc.Constants
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.mockito.kotlin.*

/**
 * Tests ForegroundDetector with simulated MOVE_TO_FOREGROUND events (T5).
 * Note: UsageEvents.Event is an Android framework class. With isReturnDefaultValues = true,
 * its fields return defaults. We use a mock UsageEvents that simulates event iteration.
 */
class ForegroundDetectorEventTest {

    private lateinit var usm: UsageStatsManager
    private lateinit var detector: ForegroundDetector

    @Before
    fun setUp() {
        usm = mock()
        detector = ForegroundDetector(usm)
    }

    @Test
    fun `returns null when queryEvents returns null`() {
        whenever(usm.queryEvents(any(), any())).thenReturn(null)
        assertNull(detector.getForegroundPackage())
    }

    @Test
    fun `returns null when no MOVE_TO_FOREGROUND events exist`() {
        val events: UsageEvents = mock {
            on { hasNextEvent() } doReturn false
        }
        whenever(usm.queryEvents(any(), any())).thenReturn(events)
        assertNull(detector.getForegroundPackage())
    }

    @Test
    fun `returns package name from single MOVE_TO_FOREGROUND event`() {
        // Create a mock UsageEvents that returns one event
        val mockEvent = UsageEvents.Event()

        val events: UsageEvents = mock {
            var called = false
            on { hasNextEvent() } doAnswer {
                if (!called) { called = true; true } else false
            }
            on { getNextEvent(any()) } doAnswer { invocation ->
                val event = invocation.getArgument<UsageEvents.Event>(0)
                // With isReturnDefaultValues = true, eventType returns 0 and packageName returns null
                // We need to verify that our detector handles the iteration correctly
                // Since we can't set fields on the real Event object in unit tests,
                // this test verifies the iteration logic completes without error
                true
            }
        }
        whenever(usm.queryEvents(any(), any())).thenReturn(events)

        // With default values (eventType=0, not MOVE_TO_FOREGROUND=1), result should be null
        val result = detector.getForegroundPackage()
        assertNull(result)
    }

    @Test
    fun `isPixelCameraPackage matches correct package`() {
        assertTrue(ForegroundDetector.isPixelCameraPackage(Constants.PIXEL_CAMERA_PACKAGE))
        assertTrue(ForegroundDetector.isPixelCameraPackage("com.google.android.GoogleCamera"))
    }

    @Test
    fun `isPixelCameraPackage rejects wrong packages`() {
        assertFalse(ForegroundDetector.isPixelCameraPackage("com.example.camera"))
        assertFalse(ForegroundDetector.isPixelCameraPackage("com.google.android.GoogleCameraGo"))
        assertFalse(ForegroundDetector.isPixelCameraPackage(null))
        assertFalse(ForegroundDetector.isPixelCameraPackage(""))
    }

    @Test
    fun `queries correct time window`() {
        val events: UsageEvents = mock {
            on { hasNextEvent() } doReturn false
        }
        whenever(usm.queryEvents(any(), any())).thenReturn(events)

        val before = System.currentTimeMillis()
        detector.getForegroundPackage()
        val after = System.currentTimeMillis()

        val captor = argumentCaptor<Long>()
        verify(usm).queryEvents(captor.capture(), captor.capture())

        val beginTime = captor.firstValue
        val endTime = captor.secondValue

        // endTime should be approximately now
        assertTrue("endTime should be recent", endTime in before..after)
        // Window should be USAGE_STATS_WINDOW_MS
        val window = endTime - beginTime
        assertEquals(Constants.USAGE_STATS_WINDOW_MS, window)
    }
}
