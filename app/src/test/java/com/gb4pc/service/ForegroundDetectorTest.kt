package com.gb4pc.service

import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import com.gb4pc.Constants
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.mockito.kotlin.*

class ForegroundDetectorTest {

    private lateinit var usm: UsageStatsManager
    private lateinit var detector: ForegroundDetector

    @Before
    fun setUp() {
        usm = mock()
        detector = ForegroundDetector(usm)
    }

    @Test
    fun `getForegroundPackage returns null when no events`() {
        val events: UsageEvents = mock {
            on { hasNextEvent() } doReturn false
        }
        whenever(usm.queryEvents(any(), any())).thenReturn(events)
        assertNull(detector.getForegroundPackage())
    }

    @Test
    fun `getForegroundPackage queries last 5 seconds`() {
        val events: UsageEvents = mock {
            on { hasNextEvent() } doReturn false
        }
        whenever(usm.queryEvents(any(), any())).thenReturn(events)

        detector.getForegroundPackage()

        val captor = argumentCaptor<Long>()
        verify(usm).queryEvents(captor.capture(), captor.capture())
        val beginTime = captor.firstValue
        val endTime = captor.secondValue
        // Window should be approximately USAGE_STATS_WINDOW_MS
        assertTrue(endTime - beginTime <= Constants.USAGE_STATS_WINDOW_MS + 100)
        assertTrue(endTime - beginTime >= Constants.USAGE_STATS_WINDOW_MS - 100)
    }

    @Test
    fun `isPixelCameraForeground returns true for matching package`() {
        assertTrue(ForegroundDetector.isPixelCameraPackage(Constants.PIXEL_CAMERA_PACKAGE))
    }

    @Test
    fun `isPixelCameraForeground returns false for other package`() {
        assertFalse(ForegroundDetector.isPixelCameraPackage("com.example.other"))
    }

    @Test
    fun `isPixelCameraForeground returns false for null`() {
        assertFalse(ForegroundDetector.isPixelCameraPackage(null))
    }
}
