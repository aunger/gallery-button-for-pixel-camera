package com.gb4pc.data

import com.gb4pc.Constants

/**
 * Normalized overlay position as percentages (PS-01).
 * Values are clamped on construction.
 */
class OverlayPosition private constructor(
    val xPercent: Float,
    val yPercent: Float,
    val sizePercent: Float
) {
    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (other !is OverlayPosition) return false
        return xPercent == other.xPercent && yPercent == other.yPercent && sizePercent == other.sizePercent
    }

    override fun hashCode(): Int {
        var result = xPercent.hashCode()
        result = 31 * result + yPercent.hashCode()
        result = 31 * result + sizePercent.hashCode()
        return result
    }

    override fun toString(): String = "OverlayPosition(x=$xPercent%, y=$yPercent%, size=$sizePercent%)"

    companion object {
        fun default() = OverlayPosition(
            xPercent = Constants.DEFAULT_X_PERCENT,
            yPercent = Constants.DEFAULT_Y_PERCENT,
            sizePercent = Constants.DEFAULT_SIZE_PERCENT
        )

        operator fun invoke(xPercent: Float, yPercent: Float, sizePercent: Float): OverlayPosition {
            return OverlayPosition(
                xPercent = xPercent.coerceIn(0f, 100f),
                yPercent = yPercent.coerceIn(0f, 100f),
                sizePercent = sizePercent.coerceIn(Constants.MIN_SIZE_PERCENT, Constants.MAX_SIZE_PERCENT)
            )
        }
    }
}
