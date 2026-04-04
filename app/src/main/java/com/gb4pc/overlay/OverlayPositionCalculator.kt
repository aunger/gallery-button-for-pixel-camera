package com.gb4pc.overlay

import kotlin.math.min
import kotlin.math.roundToInt

/**
 * Converts normalized overlay position percentages to pixel coordinates (PS-01).
 */
object OverlayPositionCalculator {

    /**
     * Calculate overlay size in pixels.
     * Size is [sizePercent]% of min(displayWidth, displayHeight).
     */
    fun calculateSizePx(sizePercent: Float, displayWidth: Int, displayHeight: Int): Int {
        val minDimension = min(displayWidth, displayHeight)
        return (minDimension * sizePercent / 100f).roundToInt()
    }

    /**
     * Calculate overlay left edge X coordinate.
     * [xPercent] positions the center of the overlay.
     */
    fun calculateXPx(xPercent: Float, displayWidth: Int, overlaySize: Int): Int {
        val centerX = (displayWidth * xPercent / 100f).roundToInt()
        return centerX - overlaySize / 2
    }

    /**
     * Calculate overlay top edge Y coordinate.
     * [yPercent] positions the center of the overlay.
     */
    fun calculateYPx(yPercent: Float, displayHeight: Int, overlaySize: Int): Int {
        val centerY = (displayHeight * yPercent / 100f).roundToInt()
        return centerY - overlaySize / 2
    }
}
