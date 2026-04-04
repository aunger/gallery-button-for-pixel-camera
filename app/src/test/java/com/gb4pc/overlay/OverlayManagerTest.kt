package com.gb4pc.overlay

import com.gb4pc.Constants
import com.gb4pc.data.OverlayPosition
import org.junit.Assert.*
import org.junit.Test

class OverlayManagerTest {

    @Test
    fun `calculateOverlaySizePx uses min of width and height`() {
        // size% = 11.5, min(1080, 2400) = 1080
        // expected = 1080 * 11.5 / 100 = 124.2
        val sizePx = OverlayPositionCalculator.calculateSizePx(
            sizePercent = 11.5f,
            displayWidth = 1080,
            displayHeight = 2400
        )
        assertEquals(124, sizePx)
    }

    @Test
    fun `calculateOverlaySizePx with landscape uses min dimension`() {
        val sizePx = OverlayPositionCalculator.calculateSizePx(
            sizePercent = 10.0f,
            displayWidth = 2400,
            displayHeight = 1080
        )
        // min(2400, 1080) = 1080, 1080 * 10 / 100 = 108
        assertEquals(108, sizePx)
    }

    @Test
    fun `calculateOverlayXPx positions center of overlay`() {
        val xPx = OverlayPositionCalculator.calculateXPx(
            xPercent = 50.0f,
            displayWidth = 1080,
            overlaySize = 100
        )
        // center at 50% of 1080 = 540, left edge = 540 - 50 = 490
        assertEquals(490, xPx)
    }

    @Test
    fun `calculateOverlayYPx positions center of overlay`() {
        val yPx = OverlayPositionCalculator.calculateYPx(
            yPercent = 50.0f,
            displayHeight = 2400,
            overlaySize = 100
        )
        // center at 50% of 2400 = 1200, top edge = 1200 - 50 = 1150
        assertEquals(1150, yPx)
    }

    @Test
    fun `calculateOverlayXPx at 0 percent`() {
        val xPx = OverlayPositionCalculator.calculateXPx(
            xPercent = 0.0f,
            displayWidth = 1080,
            overlaySize = 100
        )
        // center at 0, left edge = 0 - 50 = -50
        assertEquals(-50, xPx)
    }

    @Test
    fun `default position produces expected pixel values for typical Pixel display`() {
        val pos = OverlayPosition.default()
        val displayWidth = 1080
        val displayHeight = 2400

        val sizePx = OverlayPositionCalculator.calculateSizePx(pos.sizePercent, displayWidth, displayHeight)
        val xPx = OverlayPositionCalculator.calculateXPx(pos.xPercent, displayWidth, sizePx)
        val yPx = OverlayPositionCalculator.calculateYPx(pos.yPercent, displayHeight, sizePx)

        // Size: 1080 * 11.5 / 100 = 124
        assertEquals(124, sizePx)
        // X: 1080 * 13.0 / 100 - 62 = 140 - 62 = 78
        assertEquals(78, xPx)
        // Y: 2400 * 91.5 / 100 - 62 = 2196 - 62 = 2134
        assertEquals(2134, yPx)
    }
}
