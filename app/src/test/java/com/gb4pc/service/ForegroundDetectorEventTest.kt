package com.gb4pc.service

import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import com.gb4pc.Constants
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.mockito.kotlin.*
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Tests ForegroundDetector with simulated events (T5).
 * Note: UsageEvents.Event is an Android framework class. With isReturnDefaultValues = true,
 * its fields return defaults (eventType=0, packageName=null).
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
    fun `returns null when no events exist`() {
        val events: UsageEvents = mock {
            on { hasNextEvent() } doReturn false
        }
        whenever(usm.queryEvents(any(), any())).thenReturn(events)
        assertNull(detector.getForegroundPackage())
    }

    @Test
    fun `iterates through events without crashing`() {
        // With isReturnDefaultValues = true, Event() returns a stub object
        // eventType defaults to 0 (not MOVE_TO_FOREGROUND which is 1)
        // so the detector should return null since no matching events found
        val called = AtomicBoolean(false)
        val events: UsageEvents = mock()
        whenever(events.hasNextEvent()).thenAnswer {
            if (!called.getAndSet(true)) true else false
        }
        whenever(events.getNextEvent(any())).thenAnswer { /* no-op, fields stay default */ }
        whenever(usm.queryEvents(any(), any())).thenReturn(events)

        val result = detector.getForegroundPackage()
        assertNull(result)

        // Verify iteration happened
        verify(events, times(2)).hasNextEvent()
        verify(events, times(1)).getNextEvent(any())
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
