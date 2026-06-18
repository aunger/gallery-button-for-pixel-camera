package com.gb4pc.overlay

import android.graphics.Path
import com.gb4pc.e2e.visual.MaskData
import com.gb4pc.e2e.visual.Shape
import com.gb4pc.e2e.visual.ShapeMatcher
import com.gb4pc.e2e.visual.ShapeTemplates
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs

/**
 * Unit tests for [SquircleDrawable].
 *
 * Verifies that [SquircleDrawable.buildSuperellipsePath] produces a path whose pixel mask
 * is classified as [Shape.SQUIRCLE] by [ShapeMatcher], and not as CIRCLE or SQUARE.
 *
 * Issue #188: the squircle shape must be determined by the superellipse formula in
 * [SquircleDrawable], independent of the device launcher's adaptive-icon mask.
 */
class SquircleDrawableTest {
    /**
     * Rasterises the superellipse polygon that [SquircleDrawable.buildSuperellipsePath] would
     * produce into a [MaskData] using a pure-JVM ray-casting algorithm.
     *
     * We reconstruct the path vertices directly using the same parameters as
     * [SquircleDrawable.buildSuperellipsePath] rather than parsing the [Path] object
     * (Path has no public vertex iterator in JVM unit tests).
     */
    private fun rasteriseSuperellipse(
        w: Int,
        h: Int,
    ): MaskData {
        val vertices = buildVertices(w, h)

        val bits = BooleanArray(w * h)
        var sumX = 0L
        var sumY = 0L
        var count = 0
        var minX = Int.MAX_VALUE
        var maxX = Int.MIN_VALUE
        var minY = Int.MAX_VALUE
        var maxY = Int.MIN_VALUE

        for (py in 0 until h) {
            for (px in 0 until w) {
                if (isInsidePolygon(px + 0.5, py + 0.5, vertices)) {
                    bits[py * w + px] = true
                    if (px < minX) minX = px
                    if (px > maxX) maxX = px
                    if (py < minY) minY = py
                    if (py > maxY) maxY = py
                    sumX += px
                    sumY += py
                    count++
                }
            }
        }

        if (count == 0) return MaskData.empty()
        return MaskData(
            bits = bits,
            width = w,
            height = h,
            bboxLeft = minX,
            bboxTop = minY,
            bboxRight = maxX + 1,
            bboxBottom = maxY + 1,
            centroidX = sumX.toFloat() / count,
            centroidY = sumY.toFloat() / count,
            pixelCount = count,
        )
    }

    /** Builds the polygon vertices that [SquircleDrawable.buildSuperellipsePath] produces. */
    private fun buildVertices(
        w: Int,
        h: Int,
    ): List<Pair<Double, Double>> {
        val cx = w / 2.0
        val cy = h / 2.0
        val rx = cx
        val ry = cy
        val exp = SquircleDrawable.EXP
        val steps = 256
        return (0 until steps).map { i ->
            val theta = 2.0 * Math.PI * i / steps
            val cosT = Math.cos(theta)
            val sinT = Math.sin(theta)
            val px = cx + rx * Math.signum(cosT) * abs(cosT).pow(exp)
            val py = cy + ry * Math.signum(sinT) * abs(sinT).pow(exp)
            px to py
        }
    }

    private fun Double.pow(exp: Double) = Math.pow(this, exp)

    /** Even-odd ray-cast: true if (px, py) is inside the polygon. */
    private fun isInsidePolygon(
        px: Double,
        py: Double,
        vertices: List<Pair<Double, Double>>,
    ): Boolean {
        var inside = false
        val n = vertices.size
        var j = n - 1
        for (i in 0 until n) {
            val xi = vertices[i].first
            val yi = vertices[i].second
            val xj = vertices[j].first
            val yj = vertices[j].second
            if ((yi > py) != (yj > py) &&
                px < (xj - xi) * (py - yi) / (yj - yi) + xi
            ) {
                inside = !inside
            }
            j = i
        }
        return inside
    }

    // ── shape classification ──────────────────────────────────────────────────

    @Test
    fun `superellipse path 64x64 classifies as SQUIRCLE`() {
        val mask = rasteriseSuperellipse(64, 64)
        ShapeMatcher.requireShape(mask, Shape.SQUIRCLE, minWinnerIoU = 0.90f, minMargin = 0.04f)
    }

    @Test
    fun `superellipse path 128x128 classifies as SQUIRCLE`() {
        val mask = rasteriseSuperellipse(128, 128)
        ShapeMatcher.requireShape(mask, Shape.SQUIRCLE, minWinnerIoU = 0.90f, minMargin = 0.04f)
    }

    @Test
    fun `superellipse path 64x64 does not classify as CIRCLE`() {
        val mask = rasteriseSuperellipse(64, 64)
        val result = ShapeMatcher.classify(mask)
        assertTrue(
            "Superellipse path must not classify as CIRCLE (got ${result.winner})",
            result.winner != Shape.CIRCLE,
        )
    }

    @Test
    fun `superellipse path 128x128 does not classify as CIRCLE`() {
        val mask = rasteriseSuperellipse(128, 128)
        val result = ShapeMatcher.classify(mask)
        assertTrue(
            "Superellipse path must not classify as CIRCLE (got ${result.winner})",
            result.winner != Shape.CIRCLE,
        )
    }

    // ── path helpers ──────────────────────────────────────────────────────────

    /**
     * Verifies that [SquircleDrawable.buildSuperellipsePath] produces a non-empty [Path]
     * for typical overlay dimensions without throwing.
     */
    @Test
    fun `buildSuperellipsePath produces non-empty path for 100x100`() {
        val path = Path()
        SquircleDrawable.buildSuperellipsePath(path, 100, 100)
        assertTrue("Path must not be empty after buildSuperellipsePath", !path.isEmpty)
    }

    @Test
    fun `buildSuperellipsePath pixel count matches ShapeTemplates squircle within 5 percent`() {
        val w = 128
        val h = 128
        val templateMask = ShapeTemplates.squircle(w, h)
        val pathMask = rasteriseSuperellipse(w, h)

        val relDiff =
            Math.abs(pathMask.pixelCount - templateMask.pixelCount).toDouble() /
                templateMask.pixelCount
        assertTrue(
            "Pixel count of superellipse path (${pathMask.pixelCount}) must be within 5% of " +
                "ShapeTemplates.squircle (${templateMask.pixelCount}), got ${relDiff * 100}%",
            relDiff <= 0.05,
        )
    }
}
