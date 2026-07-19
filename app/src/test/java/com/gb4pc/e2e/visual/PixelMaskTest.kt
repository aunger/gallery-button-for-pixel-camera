package com.gb4pc.e2e.visual

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs
import kotlin.random.Random

/**
 * JVM unit tests for [PixelMask], the pure-JVM core of [ColorMatch.mask]'s pixel scan (issue #731).
 *
 * These run without an emulator: they feed [PixelMask.scan] a hand-built row-major ARGB buffer,
 * exactly as [ColorMatch.mask] feeds it the buffer it reads via Bitmap.getPixels, and assert the
 * resulting [MaskData] (bits, bbox, centroid, pixelCount). A cross-check against an independent
 * reference implementation guards the single-pass bbox/centroid accumulation and, crucially, the
 * row-major indexing the bulk-read path depends on.
 */
class PixelMaskTest {
    private val tR = 100
    private val tG = 150
    private val tB = 200
    private val tol = 20

    /** Packs channels into an ARGB int the way android.graphics.Color does. */
    private fun argb(
        r: Int,
        g: Int,
        b: Int,
        a: Int = 0xFF,
    ): Int = (a shl 24) or (r shl 16) or (g shl 8) or b

    /** A color that matches the target exactly. */
    private fun onTarget(): Int = argb(tR, tG, tB)

    /** A color far outside tolerance on every channel. */
    private fun offTarget(): Int = argb(0, 0, 0)

    // ── matches ────────────────────────────────────────────────────────────────

    @Test
    fun `matches is true for an exact hit`() {
        assertTrue(PixelMask.matches(argb(tR, tG, tB), tR, tG, tB, tol))
    }

    @Test
    fun `matches ignores the alpha channel`() {
        assertTrue(PixelMask.matches(argb(tR, tG, tB, a = 0x00), tR, tG, tB, tol))
    }

    @Test
    fun `matches is inclusive at exactly tolerance on each channel`() {
        assertTrue(PixelMask.matches(argb(tR + tol, tG, tB), tR, tG, tB, tol))
        assertTrue(PixelMask.matches(argb(tR, tG - tol, tB), tR, tG, tB, tol))
        assertTrue(PixelMask.matches(argb(tR, tG, tB + tol), tR, tG, tB, tol))
    }

    @Test
    fun `matches is false one step beyond tolerance on any channel`() {
        assertFalse(PixelMask.matches(argb(tR + tol + 1, tG, tB), tR, tG, tB, tol))
        assertFalse(PixelMask.matches(argb(tR, tG - tol - 1, tB), tR, tG, tB, tol))
        assertFalse(PixelMask.matches(argb(tR, tG, tB + tol + 1), tR, tG, tB, tol))
    }

    // ── scan: shape and empties ──────────────────────────────────────────────────

    @Test
    fun `scan with no matches yields an all-false full-size mask`() {
        val w = 4
        val h = 3
        val pixels = IntArray(w * h) { offTarget() }

        val data = PixelMask.scan(pixels, w, h, tR, tG, tB, tol)

        assertEquals(0, data.pixelCount)
        assertEquals(w * h, data.bits.size)
        assertFalse(data.bits.any { it })
        assertEquals(0, data.bboxLeft)
        assertEquals(0, data.bboxTop)
        assertEquals(0, data.bboxRight)
        assertEquals(0, data.bboxBottom)
        assertEquals(0f, data.centroidX, 0f)
        assertEquals(0f, data.centroidY, 0f)
    }

    @Test
    fun `scan of a single matching pixel reports a 1x1 bbox and that pixel's centroid`() {
        val w = 5
        val h = 4
        val hitX = 3
        val hitY = 1
        val pixels = IntArray(w * h) { offTarget() }
        pixels[hitY * w + hitX] = onTarget()

        val data = PixelMask.scan(pixels, w, h, tR, tG, tB, tol)

        assertEquals(1, data.pixelCount)
        assertTrue(data.bits[hitY * w + hitX])
        assertEquals(1, data.bits.count { it })
        assertEquals(hitX, data.bboxLeft)
        assertEquals(hitY, data.bboxTop)
        assertEquals(hitX + 1, data.bboxRight)
        assertEquals(hitY + 1, data.bboxBottom)
        assertEquals(hitX.toFloat(), data.centroidX, 0f)
        assertEquals(hitY.toFloat(), data.centroidY, 0f)
    }

    @Test
    fun `scan uses row-major stride, not a transposed layout`() {
        // A non-square grid with hits at (col=3,row=0) and (col=0,row=1). If scan mixed up the
        // stride (e.g. indexed x*h+y instead of y*w+x) the wrong flat cells would light up and
        // the bbox would come out transposed. This is the exact failure mode of a bulk-read bug.
        val w = 4
        val h = 2
        val pixels = IntArray(w * h) { offTarget() }
        pixels[0 * w + 3] = onTarget() // (x=3, y=0)
        pixels[1 * w + 0] = onTarget() // (x=0, y=1)

        val data = PixelMask.scan(pixels, w, h, tR, tG, tB, tol)

        assertEquals(2, data.pixelCount)
        assertTrue(data.bits[0 * w + 3])
        assertTrue(data.bits[1 * w + 0])
        assertEquals(0, data.bboxLeft)
        assertEquals(0, data.bboxTop)
        assertEquals(4, data.bboxRight)
        assertEquals(2, data.bboxBottom)
        // Centroid is the mean of (3,0) and (0,1).
        assertEquals(1.5f, data.centroidX, 0f)
        assertEquals(0.5f, data.centroidY, 0f)
    }

    @Test
    fun `scan spans a bbox and centroid over several matches`() {
        val w = 6
        val h = 6
        val pixels = IntArray(w * h) { offTarget() }
        val hits = listOf(1 to 2, 4 to 2, 2 to 5)
        for ((x, y) in hits) pixels[y * w + x] = onTarget()

        val data = PixelMask.scan(pixels, w, h, tR, tG, tB, tol)

        assertEquals(hits.size, data.pixelCount)
        assertEquals(1, data.bboxLeft)
        assertEquals(2, data.bboxTop)
        assertEquals(5, data.bboxRight) // maxX(4) + 1
        assertEquals(6, data.bboxBottom) // maxY(5) + 1
        assertEquals(hits.sumOf { it.first }.toFloat() / hits.size, data.centroidX, 0f)
        assertEquals(hits.sumOf { it.second }.toFloat() / hits.size, data.centroidY, 0f)
    }

    @Test
    fun `scan reads only the leading width times height entries of an oversized buffer`() {
        // Mirrors the reused scratch buffer, which can be larger than the current bitmap.
        val w = 3
        val h = 2
        val pixels = IntArray(w * h + 5) { offTarget() }
        pixels[1 * w + 2] = onTarget() // in range
        pixels[w * h] = onTarget() // just past the region: must be ignored
        pixels[w * h + 4] = onTarget() // trailing slot: must be ignored

        val data = PixelMask.scan(pixels, w, h, tR, tG, tB, tol)

        assertEquals(1, data.pixelCount)
        assertTrue(data.bits[1 * w + 2])
        assertEquals(w * h, data.bits.size)
    }

    @Test
    fun `scan rejects a buffer smaller than width times height`() {
        val w = 4
        val h = 4
        val tooSmall = IntArray(w * h - 1)
        assertThrows(IllegalArgumentException::class.java) {
            PixelMask.scan(tooSmall, w, h, tR, tG, tB, tol)
        }
    }

    @Test
    fun `scan of a zero-size grid is empty`() {
        val data = PixelMask.scan(IntArray(0), 0, 0, tR, tG, tB, tol)
        assertEquals(0, data.pixelCount)
        assertEquals(0, data.bits.size)
    }

    // ── scan: equivalence to an independent reference ────────────────────────────

    @Test
    fun `scan matches an independent reference over pseudo-random buffers`() {
        val rng = Random(731)
        repeat(20) {
            val w = 1 + rng.nextInt(40)
            val h = 1 + rng.nextInt(40)
            val pixels =
                IntArray(w * h) {
                    if (rng.nextInt(100) < 40) {
                        // Near the target, straddling the tolerance boundary in both directions.
                        argb(
                            (tR + rng.nextInt(-tol - 3, tol + 3)).coerceIn(0, 255),
                            (tG + rng.nextInt(-tol - 3, tol + 3)).coerceIn(0, 255),
                            (tB + rng.nextInt(-tol - 3, tol + 3)).coerceIn(0, 255),
                        )
                    } else {
                        argb(rng.nextInt(256), rng.nextInt(256), rng.nextInt(256))
                    }
                }

            val expected = referenceScan(pixels, w, h, tR, tG, tB, tol)
            val actual = PixelMask.scan(pixels, w, h, tR, tG, tB, tol)
            assertEquals("mismatch for ${w}x$h buffer", expected, actual)
        }
    }

    /**
     * Independent, deliberately different implementation of the scan: it collects matched
     * coordinates into lists and derives the bbox/centroid from those, rather than accumulating
     * min/max/sum inline. Any bug in [PixelMask.scan]'s single-pass accumulation shows up as a
     * disagreement with this reference.
     */
    private fun referenceScan(
        pixels: IntArray,
        w: Int,
        h: Int,
        tr: Int,
        tg: Int,
        tb: Int,
        tolerance: Int,
    ): MaskData {
        val bits = BooleanArray(w * h)
        val xs = ArrayList<Int>()
        val ys = ArrayList<Int>()
        for (y in 0 until h) {
            for (x in 0 until w) {
                val p = pixels[y * w + x]
                val r = (p and 0x00FF0000) ushr 16
                val g = (p and 0x0000FF00) ushr 8
                val b = p and 0x000000FF
                if (abs(r - tr) <= tolerance && abs(g - tg) <= tolerance && abs(b - tb) <= tolerance) {
                    bits[y * w + x] = true
                    xs.add(x)
                    ys.add(y)
                }
            }
        }
        if (xs.isEmpty()) return MaskData(bits, w, h, 0, 0, 0, 0, 0f, 0f, 0)
        val count = xs.size
        return MaskData(
            bits = bits,
            width = w,
            height = h,
            bboxLeft = xs.min(),
            bboxTop = ys.min(),
            bboxRight = xs.max() + 1,
            bboxBottom = ys.max() + 1,
            centroidX = xs.sum().toFloat() / count,
            centroidY = ys.sum().toFloat() / count,
            pixelCount = count,
        )
    }
}
