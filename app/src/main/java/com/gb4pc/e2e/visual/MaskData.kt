package com.gb4pc.e2e.visual

/**
 * JVM-compatible binary pixel mask used by [ShapeTemplates] and [ShapeMatcher].
 *
 * Deliberately free of android.* API dependencies so it can be used in both JVM
 * unit tests (app/src/test) and Android instrumented tests (app/src/androidTest).
 * Instrumented-test code converts to/from the richer [com.gb4pc.e2e.visual.BinaryMask]
 * (which wraps android.graphics.Rect / PointF) via [com.gb4pc.e2e.visual.BinaryMask.toMaskData]
 * and [MaskData.toBinaryMask].
 *
 * @param bits       Row-major boolean array, length = width * height.
 * @param width      Width of the mask in pixels.
 * @param height     Height of the mask in pixels.
 * @param bboxLeft   Left edge (inclusive) of tight bounding box; 0 if empty.
 * @param bboxTop    Top edge (inclusive) of tight bounding box; 0 if empty.
 * @param bboxRight  Right edge (exclusive) of tight bounding box; 0 if empty.
 * @param bboxBottom Bottom edge (exclusive) of tight bounding box; 0 if empty.
 * @param centroidX  Mean x of true pixels; 0f if empty.
 * @param centroidY  Mean y of true pixels; 0f if empty.
 * @param pixelCount Number of true pixels.
 */
data class MaskData(
    val bits: BooleanArray,
    val width: Int,
    val height: Int,
    val bboxLeft: Int,
    val bboxTop: Int,
    val bboxRight: Int,
    val bboxBottom: Int,
    val centroidX: Float,
    val centroidY: Float,
    val pixelCount: Int
) {
    val bboxWidth: Int get() = bboxRight - bboxLeft
    val bboxHeight: Int get() = bboxBottom - bboxTop

    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (other !is MaskData) return false
        return width == other.width &&
            height == other.height &&
            bboxLeft == other.bboxLeft &&
            bboxTop == other.bboxTop &&
            bboxRight == other.bboxRight &&
            bboxBottom == other.bboxBottom &&
            centroidX == other.centroidX &&
            centroidY == other.centroidY &&
            pixelCount == other.pixelCount &&
            bits.contentEquals(other.bits)
    }

    override fun hashCode(): Int {
        var result = bits.contentHashCode()
        result = 31 * result + width
        result = 31 * result + height
        result = 31 * result + bboxLeft
        result = 31 * result + bboxTop
        result = 31 * result + bboxRight
        result = 31 * result + bboxBottom
        result = 31 * result + centroidX.hashCode()
        result = 31 * result + centroidY.hashCode()
        result = 31 * result + pixelCount
        return result
    }

    companion object {
        /** Creates an empty (0×0) MaskData. */
        fun empty(): MaskData =
            MaskData(BooleanArray(0), 0, 0, 0, 0, 0, 0, 0f, 0f, 0)
    }
}
