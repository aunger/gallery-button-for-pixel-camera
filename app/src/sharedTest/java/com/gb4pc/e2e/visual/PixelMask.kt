package com.gb4pc.e2e.visual

import kotlin.math.abs

/**
 * Pure-JVM core of [com.gb4pc.e2e.visual.ColorMatch.mask]'s pixel scan.
 *
 * Deliberately free of android.* API dependencies (like [MaskData]) so it can be unit-tested
 * directly on the JVM (app/src/test) without an emulator, while [ColorMatch] (app/src/androidTest)
 * feeds it a row-major ARGB buffer read in one shot via [android.graphics.Bitmap.getPixels].
 * Reading the whole bitmap once and scanning the flat array replaces ~w*h per-pixel
 * `Bitmap.getPixel` JNI calls with a single bulk read (issue #731).
 */
object PixelMask {
    /**
     * True if every RGB channel of [pixel] (a packed ARGB int) is within [tolerance] of the
     * corresponding target channel. Alpha is ignored. Channel extraction mirrors
     * android.graphics.Color.red/green/blue so results match the on-device path exactly.
     */
    fun matches(
        pixel: Int,
        targetR: Int,
        targetG: Int,
        targetB: Int,
        tolerance: Int,
    ): Boolean {
        val r = (pixel shr 16) and 0xFF
        val g = (pixel shr 8) and 0xFF
        val b = pixel and 0xFF
        return abs(r - targetR) <= tolerance &&
            abs(g - targetG) <= tolerance &&
            abs(b - targetB) <= tolerance
    }

    /**
     * Scans the first [width]*[height] entries of [pixels] (row-major, packed ARGB) for pixels
     * matching the target color within [tolerance], computing bits, tight bbox, centroid, and
     * pixelCount in a single pass. The returned [MaskData] has the same shape [ColorMatch.mask]
     * produces: an empty result carries an all-false bits array of length width*height, a
     * (0,0,0,0) bbox, and a (0,0) centroid.
     *
     * [pixels] may be longer than width*height (e.g. a reused scratch buffer sized to the largest
     * bitmap seen so far); only the leading width*height entries are read.
     */
    fun scan(
        pixels: IntArray,
        width: Int,
        height: Int,
        targetR: Int,
        targetG: Int,
        targetB: Int,
        tolerance: Int,
    ): MaskData {
        val n = width * height
        require(pixels.size >= n) {
            "pixels buffer too small: size=${pixels.size} < width*height=$n"
        }
        val bits = BooleanArray(n)

        var minX = Int.MAX_VALUE
        var maxX = Int.MIN_VALUE
        var minY = Int.MAX_VALUE
        var maxY = Int.MIN_VALUE
        var sumX = 0L
        var sumY = 0L
        var count = 0

        for (y in 0 until height) {
            val row = y * width
            for (x in 0 until width) {
                if (matches(pixels[row + x], targetR, targetG, targetB, tolerance)) {
                    bits[row + x] = true
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

        if (count == 0) {
            return MaskData(bits, width, height, 0, 0, 0, 0, 0f, 0f, 0)
        }
        return MaskData(
            bits = bits,
            width = width,
            height = height,
            bboxLeft = minX,
            bboxTop = minY,
            bboxRight = maxX + 1,
            bboxBottom = maxY + 1,
            centroidX = sumX.toFloat() / count,
            centroidY = sumY.toFloat() / count,
            pixelCount = count,
        )
    }
}
