package com.gb4pc.data

import com.gb4pc.Constants
import org.junit.Assert.*
import org.junit.Test

class OverlayPositionTest {

    @Test
    fun `default position has correct values from spec PS-02`() {
        val pos = OverlayPosition.default()
        assertEquals(Constants.DEFAULT_X_PERCENT, pos.xPercent, 0.001f)
        assertEquals(Constants.DEFAULT_Y_PERCENT, pos.yPercent, 0.001f)
        assertEquals(Constants.DEFAULT_SIZE_PERCENT, pos.sizePercent, 0.001f)
    }

    @Test
    fun `xPercent is clamped to 0-100 range`() {
        val pos = OverlayPosition(xPercent = -5f, yPercent = 50f, sizePercent = 10f)
        assertEquals(0f, pos.xPercent, 0.001f)

        val pos2 = OverlayPosition(xPercent = 150f, yPercent = 50f, sizePercent = 10f)
        assertEquals(100f, pos2.xPercent, 0.001f)
    }

    @Test
    fun `yPercent is clamped to 0-100 range`() {
        val pos = OverlayPosition(xPercent = 50f, yPercent = -10f, sizePercent = 10f)
        assertEquals(0f, pos.yPercent, 0.001f)

        val pos2 = OverlayPosition(xPercent = 50f, yPercent = 200f, sizePercent = 10f)
        assertEquals(100f, pos2.yPercent, 0.001f)
    }

    @Test
    fun `sizePercent is clamped to min-max range`() {
        val pos = OverlayPosition(xPercent = 50f, yPercent = 50f, sizePercent = 0.5f)
        assertEquals(Constants.MIN_SIZE_PERCENT, pos.sizePercent, 0.001f)

        val pos2 = OverlayPosition(xPercent = 50f, yPercent = 50f, sizePercent = 50f)
        assertEquals(Constants.MAX_SIZE_PERCENT, pos2.sizePercent, 0.001f)
    }

    @Test
    fun `valid values are preserved`() {
        val pos = OverlayPosition(xPercent = 25.5f, yPercent = 75.3f, sizePercent = 15.0f)
        assertEquals(25.5f, pos.xPercent, 0.001f)
        assertEquals(75.3f, pos.yPercent, 0.001f)
        assertEquals(15.0f, pos.sizePercent, 0.001f)
    }
}
