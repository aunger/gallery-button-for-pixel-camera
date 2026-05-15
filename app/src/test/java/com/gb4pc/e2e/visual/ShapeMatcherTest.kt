package com.gb4pc.e2e.visual

import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.random.Random

/**
 * JVM unit tests for [ShapeMatcher].
 *
 * Uses [ShapeTemplates] to generate synthetic ground-truth masks and verifies that
 * the classifier correctly identifies each shape, with sufficient IoU and margin, at
 * multiple resolutions. Also confirms robustness to ~2% random edge-pixel dropout
 * (simulating anti-aliasing / screenshot softening).
 */
class ShapeMatcherTest {

    // ── synthetic inputs ──────────────────────────────────────────────────────

    /**
     * Generates a candidate mask for [shape] at the given size, then applies edge-pixel
     * dropout at [noiseFraction] probability to simulate anti-aliasing.
     *
     * A pixel is considered "on the edge" if at least one of its four cardinal neighbours
     * is false (outside the shape). For purely interior pixels we never flip, so the
     * majority of the mask content remains faithful even at non-trivial noise fractions.
     */
    private fun makeNoisy(
        shape: Shape, w: Int, h: Int,
        noiseFraction: Double = 0.0,
        rng: Random = Random(42)
    ): MaskData {
        val base = when (shape) {
            is Shape.SQUARE   -> ShapeTemplates.square(w, h)
            is Shape.CIRCLE   -> ShapeTemplates.circle(w, h)
            is Shape.SQUIRCLE -> ShapeTemplates.squircle(w, h)
        }
        if (noiseFraction <= 0.0) return base

        val bits = base.bits.copyOf()
        var sumX = 0L; var sumY = 0L; var count = 0
        var minX = Int.MAX_VALUE; var maxX = Int.MIN_VALUE
        var minY = Int.MAX_VALUE; var maxY = Int.MIN_VALUE

        for (y in 0 until h) {
            for (x in 0 until w) {
                val idx = y * w + x
                var v = bits[idx]
                if (v && isEdgePixel(bits, x, y, w, h) && rng.nextDouble() < noiseFraction) {
                    v = false
                }
                bits[idx] = v
                if (v) {
                    if (x < minX) minX = x; if (x > maxX) maxX = x
                    if (y < minY) minY = y; if (y > maxY) maxY = y
                    sumX += x; sumY += y; count++
                }
            }
        }

        if (count == 0) return MaskData.empty()
        return MaskData(
            bits = bits, width = w, height = h,
            bboxLeft = minX, bboxTop = minY, bboxRight = maxX + 1, bboxBottom = maxY + 1,
            centroidX = sumX.toFloat() / count, centroidY = sumY.toFloat() / count,
            pixelCount = count
        )
    }

    private fun isEdgePixel(bits: BooleanArray, x: Int, y: Int, w: Int, h: Int): Boolean {
        fun at(px: Int, py: Int) = if (px in 0 until w && py in 0 until h) bits[py * w + px] else false
        return !at(x - 1, y) || !at(x + 1, y) || !at(x, y - 1) || !at(x, y + 1)
    }

    // ── helpers ───────────────────────────────────────────────────────────────

    private fun assertClassifiesAs(
        label: String,
        mask: MaskData,
        expected: Shape,
        minIoU: Float = 0.95f,
        minMargin: Float = 0.05f
    ) {
        val result = ShapeMatcher.classify(mask)
        assertTrue(
            "$label: expected winner=$expected but got winner=${result.winner} " +
            "(winnerIoU=${result.winnerIoU}, runnerUpIoU=${result.runnerUpIoU})",
            result.winner == expected
        )
        assertTrue(
            "$label: winnerIoU ${result.winnerIoU} < $minIoU",
            result.winnerIoU >= minIoU
        )
        val margin = result.winnerIoU - result.runnerUpIoU
        assertTrue(
            "$label: margin $margin < $minMargin " +
            "(winnerIoU=${result.winnerIoU}, runnerUpIoU=${result.runnerUpIoU})",
            margin >= minMargin
        )
    }

    private fun assertNotClassifiedAs(label: String, mask: MaskData, excluded: Shape) {
        val result = ShapeMatcher.classify(mask)
        assertTrue(
            "$label: expected winner != $excluded but it was " +
            "(winnerIoU=${result.winnerIoU}, runnerUpIoU=${result.runnerUpIoU})",
            result.winner != excluded
        )
    }

    // ── square tests ──────────────────────────────────────────────────────────

    @Test fun `square 64x64 classifies as SQUARE`() =
        assertClassifiesAs("square 64x64", makeNoisy(Shape.SQUARE, 64, 64), Shape.SQUARE)

    @Test fun `square 128x128 classifies as SQUARE`() =
        assertClassifiesAs("square 128x128", makeNoisy(Shape.SQUARE, 128, 128), Shape.SQUARE)

    @Test fun `square 256x256 classifies as SQUARE`() =
        assertClassifiesAs("square 256x256", makeNoisy(Shape.SQUARE, 256, 256), Shape.SQUARE)

    // ── circle tests ──────────────────────────────────────────────────────────

    @Test fun `circle 64x64 classifies as CIRCLE`() =
        assertClassifiesAs("circle 64x64", makeNoisy(Shape.CIRCLE, 64, 64), Shape.CIRCLE)

    @Test fun `circle 128x128 classifies as CIRCLE`() =
        assertClassifiesAs("circle 128x128", makeNoisy(Shape.CIRCLE, 128, 128), Shape.CIRCLE)

    @Test fun `circle 256x256 classifies as CIRCLE`() =
        assertClassifiesAs("circle 256x256", makeNoisy(Shape.CIRCLE, 256, 256), Shape.CIRCLE)

    // ── squircle tests ────────────────────────────────────────────────────────

    @Test fun `squircle 64x64 classifies as SQUIRCLE`() =
        assertClassifiesAs("squircle 64x64", makeNoisy(Shape.SQUIRCLE, 64, 64), Shape.SQUIRCLE)

    @Test fun `squircle 128x128 classifies as SQUIRCLE`() =
        assertClassifiesAs("squircle 128x128", makeNoisy(Shape.SQUIRCLE, 128, 128), Shape.SQUIRCLE)

    @Test fun `squircle 256x256 classifies as SQUIRCLE`() =
        assertClassifiesAs("squircle 256x256", makeNoisy(Shape.SQUIRCLE, 256, 256), Shape.SQUIRCLE)

    // ── squircle != SQUARE and != CIRCLE ─────────────────────────────────────

    @Test fun `squircle 64x64 does not classify as SQUARE`() =
        assertNotClassifiedAs("squircle 64x64", makeNoisy(Shape.SQUIRCLE, 64, 64), Shape.SQUARE)

    @Test fun `squircle 64x64 does not classify as CIRCLE`() =
        assertNotClassifiedAs("squircle 64x64", makeNoisy(Shape.SQUIRCLE, 64, 64), Shape.CIRCLE)

    @Test fun `squircle 128x128 does not classify as SQUARE`() =
        assertNotClassifiedAs("squircle 128x128", makeNoisy(Shape.SQUIRCLE, 128, 128), Shape.SQUARE)

    @Test fun `squircle 128x128 does not classify as CIRCLE`() =
        assertNotClassifiedAs("squircle 128x128", makeNoisy(Shape.SQUIRCLE, 128, 128), Shape.CIRCLE)

    @Test fun `squircle 256x256 does not classify as SQUARE`() =
        assertNotClassifiedAs("squircle 256x256", makeNoisy(Shape.SQUIRCLE, 256, 256), Shape.SQUARE)

    @Test fun `squircle 256x256 does not classify as CIRCLE`() =
        assertNotClassifiedAs("squircle 256x256", makeNoisy(Shape.SQUIRCLE, 256, 256), Shape.CIRCLE)

    // ── anti-aliased / noisy inputs (2% edge dropout) ────────────────────────

    @Test fun `noisy square 64x64 still classifies as SQUARE`() =
        assertClassifiesAs(
            "noisy square 64x64",
            makeNoisy(Shape.SQUARE, 64, 64, noiseFraction = 0.02),
            Shape.SQUARE
        )

    @Test fun `noisy circle 64x64 still classifies as CIRCLE`() =
        assertClassifiesAs(
            "noisy circle 64x64",
            makeNoisy(Shape.CIRCLE, 64, 64, noiseFraction = 0.02),
            Shape.CIRCLE
        )

    @Test fun `noisy squircle 64x64 still classifies as SQUIRCLE`() =
        assertClassifiesAs(
            "noisy squircle 64x64",
            makeNoisy(Shape.SQUIRCLE, 64, 64, noiseFraction = 0.02),
            Shape.SQUIRCLE
        )

    @Test fun `noisy square 128x128 still classifies as SQUARE`() =
        assertClassifiesAs(
            "noisy square 128x128",
            makeNoisy(Shape.SQUARE, 128, 128, noiseFraction = 0.02),
            Shape.SQUARE
        )

    @Test fun `noisy circle 128x128 still classifies as CIRCLE`() =
        assertClassifiesAs(
            "noisy circle 128x128",
            makeNoisy(Shape.CIRCLE, 128, 128, noiseFraction = 0.02),
            Shape.CIRCLE
        )

    @Test fun `noisy squircle 128x128 still classifies as SQUIRCLE`() =
        assertClassifiesAs(
            "noisy squircle 128x128",
            makeNoisy(Shape.SQUIRCLE, 128, 128, noiseFraction = 0.02),
            Shape.SQUIRCLE
        )

    @Test fun `noisy square 256x256 still classifies as SQUARE`() =
        assertClassifiesAs(
            "noisy square 256x256",
            makeNoisy(Shape.SQUARE, 256, 256, noiseFraction = 0.02),
            Shape.SQUARE
        )

    @Test fun `noisy circle 256x256 still classifies as CIRCLE`() =
        assertClassifiesAs(
            "noisy circle 256x256",
            makeNoisy(Shape.CIRCLE, 256, 256, noiseFraction = 0.02),
            Shape.CIRCLE
        )

    @Test fun `noisy squircle 256x256 still classifies as SQUIRCLE`() =
        assertClassifiesAs(
            "noisy squircle 256x256",
            makeNoisy(Shape.SQUIRCLE, 256, 256, noiseFraction = 0.02),
            Shape.SQUIRCLE
        )
}
