package com.gb4pc.e2e.visual

import kotlin.math.abs
import kotlin.math.pow

/**
 * Generates ground-truth [MaskData]s for shape-classification templates.
 *
 * All methods are pure JVM — no android.* dependencies — so they run in both
 * JVM unit tests and Android instrumented tests.
 */
object ShapeTemplates {

    /**
     * Returns a [MaskData] of size [w]×[h] with every pixel set to true (a filled square).
     */
    fun square(w: Int, h: Int): MaskData {
        require(w > 0 && h > 0) { "square: dimensions must be positive (got $w×$h)" }
        val bits = BooleanArray(w * h) { true }
        val cx = (w - 1) / 2f
        val cy = (h - 1) / 2f
        return MaskData(
            bits = bits,
            width = w,
            height = h,
            bboxLeft = 0, bboxTop = 0, bboxRight = w, bboxBottom = h,
            centroidX = cx, centroidY = cy,
            pixelCount = w * h
        )
    }

    /**
     * Returns a [MaskData] of size [w]×[h] with pixels inside the inscribed ellipse set to true.
     *
     * A pixel at (x, y) is inside if:
     *   ((x + 0.5 - w/2) / (w/2))² + ((y + 0.5 - h/2) / (h/2))² ≤ 1
     * Using the pixel center (x + 0.5) produces a more accurate circular/elliptical boundary
     * than rounding to the grid corner.
     */
    fun circle(w: Int, h: Int): MaskData {
        require(w > 0 && h > 0) { "circle: dimensions must be positive (got $w×$h)" }
        val bits = BooleanArray(w * h)
        val rx = w / 2.0; val ry = h / 2.0
        val cx = w / 2.0; val cy = h / 2.0
        var sumX = 0L; var sumY = 0L; var count = 0
        var minX = Int.MAX_VALUE; var maxX = Int.MIN_VALUE
        var minY = Int.MAX_VALUE; var maxY = Int.MIN_VALUE
        for (y in 0 until h) {
            for (x in 0 until w) {
                val nx = (x + 0.5 - cx) / rx
                val ny = (y + 0.5 - cy) / ry
                if (nx * nx + ny * ny <= 1.0) {
                    bits[y * w + x] = true
                    if (x < minX) minX = x
                    if (x > maxX) maxX = x
                    if (y < minY) minY = y
                    if (y > maxY) maxY = y
                    sumX += x; sumY += y; count++
                }
            }
        }
        return buildMaskData(bits, w, h, sumX, sumY, count, minX, maxX, minY, maxY)
    }

    /**
     * Returns a [MaskData] of size [w]×[h] with pixels inside the superellipse (squircle) set
     * to true.
     *
     * Formula: |2x/w − 1|^n + |2y/h − 1|^n ≤ 1, with n = 4.
     * This approximates Android's adaptive-icon mask.
     */
    fun squircle(w: Int, h: Int): MaskData {
        require(w > 0 && h > 0) { "squircle: dimensions must be positive (got $w×$h)" }
        val n = 4.0
        val bits = BooleanArray(w * h)
        var sumX = 0L; var sumY = 0L; var count = 0
        var minX = Int.MAX_VALUE; var maxX = Int.MIN_VALUE
        var minY = Int.MAX_VALUE; var maxY = Int.MIN_VALUE
        for (y in 0 until h) {
            for (x in 0 until w) {
                // Use pixel center for sub-pixel accuracy.
                val nx = abs((x + 0.5) * 2.0 / w - 1.0)
                val ny = abs((y + 0.5) * 2.0 / h - 1.0)
                if (nx.pow(n) + ny.pow(n) <= 1.0) {
                    bits[y * w + x] = true
                    if (x < minX) minX = x
                    if (x > maxX) maxX = x
                    if (y < minY) minY = y
                    if (y > maxY) maxY = y
                    sumX += x; sumY += y; count++
                }
            }
        }
        return buildMaskData(bits, w, h, sumX, sumY, count, minX, maxX, minY, maxY)
    }

    // ── internal helpers ─────────────────────────────────────────────────────

    private fun buildMaskData(
        bits: BooleanArray, w: Int, h: Int,
        sumX: Long, sumY: Long, count: Int,
        minX: Int, maxX: Int, minY: Int, maxY: Int
    ): MaskData {
        if (count == 0) return MaskData(bits, w, h, 0, 0, 0, 0, 0f, 0f, 0)
        return MaskData(
            bits = bits,
            width = w,
            height = h,
            bboxLeft = minX, bboxTop = minY, bboxRight = maxX + 1, bboxBottom = maxY + 1,
            centroidX = sumX.toFloat() / count,
            centroidY = sumY.toFloat() / count,
            pixelCount = count
        )
    }
}