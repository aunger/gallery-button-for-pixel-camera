package com.gb4pc.e2e.visual

import android.graphics.Bitmap
import android.graphics.PointF
import android.graphics.Rect

/**
 * Pixel-color matching and binary-mask construction utilities.
 *
 * Uses per-channel RGB comparisons (not Euclidean) because anti-aliased edges and PNG
 * round-tripping shift channels near-independently, and per-channel gates are easier to
 * reason about. Default tolerance 20 is calibrated to the BLUE/YELLOW/GREEN palette with
 * takeScreenshot() PNG noise.
 */
object ColorMatch {
    // Scratch buffer reused across mask() calls, grown to the largest bitmap seen so far, so a
    // 1080x2400 screenshot poll does not allocate a fresh ~2.6M-int array on every iteration
    // (issue #731). ColorMatch is used single-threaded from the E2E test thread.
    private var pixelBuffer = IntArray(0)

    private fun pixelBuffer(size: Int): IntArray {
        if (pixelBuffer.size < size) {
            pixelBuffer = IntArray(size)
        }
        return pixelBuffer
    }

    /**
     * Returns true if every RGB channel of [pixel] (a packed ARGB int, e.g. from Bitmap.getPixels)
     * is within [tolerance] of the corresponding channel in [target].
     */
    fun matches(
        pixel: Int,
        target: Rgb,
        tolerance: Int = 20,
    ): Boolean = PixelMask.matches(pixel, target.r, target.g, target.b, tolerance)

    /**
     * Builds a [BinaryMask] by scanning [bmp] for pixels matching [target] within [tolerance].
     * Computes bbox, centroid, and pixelCount in a single pass.
     *
     * Reads the whole bitmap once into a reused [IntArray] via [Bitmap.getPixels] (one JNI call)
     * and scans that flat, row-major buffer, rather than paying a `getPixel` JNI call per pixel
     * (~2.6M for a 1080x2400 screenshot; issue #731). The scan itself lives in the pure-JVM,
     * unit-tested [PixelMask.scan]; here we only bridge the [MaskData] it returns to the
     * android.graphics-flavored [BinaryMask].
     */
    fun mask(
        bmp: Bitmap,
        target: Rgb,
        tolerance: Int = 20,
    ): BinaryMask {
        val w = bmp.width
        val h = bmp.height
        val pixels = pixelBuffer(w * h)
        // stride = w, offset = 0, from (0,0), reading the full w*h grid row-major.
        bmp.getPixels(pixels, 0, w, 0, 0, w, h)

        val data = PixelMask.scan(pixels, w, h, target.r, target.g, target.b, tolerance)
        // Wrap data's fields directly (no defensive copy of bits): data is freshly allocated by
        // scan() and discarded here, so BinaryMask can own its bits array. Empty results already
        // carry a (0,0,0,0) bbox and (0,0) centroid from scan(), matching the previous behavior.
        return BinaryMask(
            data.bits,
            w,
            h,
            Rect(data.bboxLeft, data.bboxTop, data.bboxRight, data.bboxBottom),
            PointF(data.centroidX, data.centroidY),
            data.pixelCount,
        )
    }

    /** Fraction of all pixels in [mask] that are true. */
    fun coverageFraction(mask: BinaryMask): Float {
        val total = mask.width * mask.height
        return if (total == 0) 0f else mask.pixelCount.toFloat() / total
    }

    /**
     * Fraction of pixels within [region] that are true in [mask].
     * [region] is clipped to the mask bounds before counting.
     */
    fun coverageFraction(
        mask: BinaryMask,
        region: Rect,
    ): Float {
        val left = region.left.coerceIn(0, mask.width)
        val right = region.right.coerceIn(0, mask.width)
        val top = region.top.coerceIn(0, mask.height)
        val bottom = region.bottom.coerceIn(0, mask.height)
        val total = (right - left) * (bottom - top)
        if (total <= 0) return 0f
        var trueCount = 0
        for (y in top until bottom) {
            for (x in left until right) {
                if (mask.bits[y * mask.width + x]) trueCount++
            }
        }
        return trueCount.toFloat() / total
    }

    /**
     * Returns a new [BinaryMask] tight-cropped to [mask]'s bounding box.
     * If the mask is empty, returns a 0×0 empty mask.
     */
    fun crop(mask: BinaryMask): BinaryMask {
        if (mask.pixelCount == 0) {
            return BinaryMask(BooleanArray(0), 0, 0, Rect(0, 0, 0, 0), PointF(0f, 0f), 0)
        }
        val bbox = mask.bbox
        val newW = bbox.width()
        val newH = bbox.height()
        val newBits = BooleanArray(newW * newH)
        for (y in 0 until newH) {
            for (x in 0 until newW) {
                newBits[y * newW + x] = mask.bits[(bbox.top + y) * mask.width + (bbox.left + x)]
            }
        }
        // Centroid in new (cropped) coordinates.
        val newCentroid = PointF(mask.centroid.x - bbox.left, mask.centroid.y - bbox.top)
        return BinaryMask(
            newBits,
            newW,
            newH,
            Rect(0, 0, newW, newH),
            newCentroid,
            mask.pixelCount,
        )
    }

    /**
     * Pixel-wise OR of two masks with the same dimensions.
     * Bbox, centroid, and pixelCount are recomputed from the union result.
     *
     * @throws IllegalArgumentException if the two masks have different dimensions.
     */
    fun union(
        a: BinaryMask,
        b: BinaryMask,
    ): BinaryMask {
        require(a.width == b.width && a.height == b.height) {
            "union: masks must have equal dimensions (${a.width}×${a.height} vs ${b.width}×${b.height})"
        }
        val w = a.width
        val h = a.height
        val newBits = BooleanArray(w * h)
        var minX = Int.MAX_VALUE
        var maxX = Int.MIN_VALUE
        var minY = Int.MAX_VALUE
        var maxY = Int.MIN_VALUE
        var sumX = 0L
        var sumY = 0L
        var count = 0
        for (y in 0 until h) {
            for (x in 0 until w) {
                val idx = y * w + x
                if (a.bits[idx] || b.bits[idx]) {
                    newBits[idx] = true
                    if (x < minX) minX = x
                    if (x > maxX) maxX = x
                    if (y < minY) minY = y
                    if (y > maxY) maxY = y
                    sumX += x
                    sumY += y
                    count++
                }
            }
        }
        val bbox =
            if (count == 0) {
                Rect(0, 0, 0, 0)
            } else {
                Rect(minX, minY, maxX + 1, maxY + 1)
            }
        val centroid =
            if (count == 0) {
                PointF(0f, 0f)
            } else {
                PointF(sumX.toFloat() / count, sumY.toFloat() / count)
            }
        return BinaryMask(newBits, w, h, bbox, centroid, count)
    }
}
