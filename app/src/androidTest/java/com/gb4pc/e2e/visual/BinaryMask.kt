package com.gb4pc.e2e.visual

import android.graphics.PointF
import android.graphics.Rect

/**
 * A binary (true/false) pixel mask over a width×height grid.
 *
 * @param bits      Row-major boolean array, length = width * height.
 * @param width     Width of the mask in pixels.
 * @param height    Height of the mask in pixels.
 * @param bbox      Tight bounding box of the true pixels; Rect(0,0,0,0) if no true pixels.
 * @param centroid  Mean (x, y) of all true pixels in image coordinates; (0,0) if empty.
 * @param pixelCount Number of true pixels.
 */
data class BinaryMask(
    val bits: BooleanArray,
    val width: Int,
    val height: Int,
    val bbox: Rect,
    val centroid: PointF,
    val pixelCount: Int
) {
    // BooleanArray does not implement structural equals/hashCode; override here.
    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (other !is BinaryMask) return false
        return width == other.width &&
            height == other.height &&
            bbox == other.bbox &&
            centroid == other.centroid &&
            pixelCount == other.pixelCount &&
            bits.contentEquals(other.bits)
    }

    override fun hashCode(): Int {
        var result = bits.contentHashCode()
        result = 31 * result + width
        result = 31 * result + height
        result = 31 * result + bbox.hashCode()
        result = 31 * result + centroid.hashCode()
        result = 31 * result + pixelCount
        return result
    }
}
