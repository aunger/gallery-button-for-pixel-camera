package com.gb4pc.overlay

import android.app.Application
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.drawable.BitmapDrawable
import androidx.test.core.app.ApplicationProvider
import com.gb4pc.e2e.visual.MaskData
import com.gb4pc.e2e.visual.PixelMask
import com.gb4pc.e2e.visual.ShapeTemplates
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.GraphicsMode
import kotlin.math.min

/**
 * Pixel-level regression tests for [SquircleDrawable]'s clip shape.
 *
 * Issue #767 follow-up: CENTER_CROP (introduced by #767 for photo thumbnails) leaves a wrapped
 * drawable's own `bounds` at its raw, unscaled intrinsic size, which is not square for a
 * non-square photo. [SquircleDrawable] must clip against the centered square sub-region of
 * those bounds ([centeredSquareRegion]), not the full bounds, or the squircle's curvature ends
 * up concentrated almost entirely outside CENTER_CROP's visible crop window, rendering the
 * button's corners essentially sharp instead of rounded.
 *
 * Each test draws the real drawable over a BLACK backdrop and inspects the pixels it actually
 * covered, so what is asserted is the shape a user would see rather than an internal field.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class SquircleDrawableRobolectricTest {
    @Test
    fun `wide drawable is clipped to a squircle filling its centered square crop window`() {
        assertClipsToCenteredSquareSquircle(160, 90)
    }

    @Test
    fun `tall drawable is clipped to a squircle filling its centered square crop window`() {
        assertClipsToCenteredSquareSquircle(90, 160)
    }

    @Test
    fun `square drawable is clipped to a squircle filling its whole bounds`() {
        assertClipsToCenteredSquareSquircle(64, 64)
    }

    /**
     * Asserts the drawn shape of a [w] x [h] [SquircleDrawable] is a squircle filling the
     * centered square sub-region of those bounds, which is exactly the window CENTER_CROP shows
     * inside this codebase's always-square overlay.
     */
    private fun assertClipsToCenteredSquareSquircle(
        w: Int,
        h: Int,
    ) {
        val side = min(w, h)
        val expected = centeredSquareRegion(w, h)
        val drawn = drawnShape(w, h)

        assertEquals(
            "Issue #767: the clip must start at the crop window's left edge for a $w x $h drawable.",
            expected.left,
            drawn.bboxLeft,
        )
        assertEquals(
            "Issue #767: the clip must start at the crop window's top edge for a $w x $h drawable.",
            expected.top,
            drawn.bboxTop,
        )
        assertEquals(
            "Issue #767: the clip must end at the crop window's right edge for a $w x $h drawable.",
            expected.left + side,
            drawn.bboxRight,
        )
        assertEquals(
            "Issue #767: the clip must end at the crop window's bottom edge for a $w x $h drawable.",
            expected.top + side,
            drawn.bboxBottom,
        )

        // Shape, not merely extent: a square clip over the same crop window would satisfy every
        // bbox check above.
        val template = squircleAt(w, h, expected.left, expected.top, side)
        val iou = PixelRender.alignedIoU(drawn, template)
        assertTrue(
            "Issue #767: a $w x $h drawable must be clipped to a superellipse filling its " +
                "${side}px crop window (IoU $iou, min $MIN_IOU).",
            iou >= MIN_IOU,
        )
    }

    /**
     * Draws a [w] x [h] [SquircleDrawable] wrapping a solid BLUE bitmap over BLACK and returns
     * the mask of the BLUE pixels it covered: the clip shape, made visible.
     */
    private fun drawnShape(
        w: Int,
        h: Int,
    ): MaskData {
        val context: Application = ApplicationProvider.getApplicationContext()
        val solidBlue = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
        Canvas(solidBlue).drawColor(Color.BLUE)
        val squircle = SquircleDrawable(BitmapDrawable(context.resources, solidBlue))
        squircle.setBounds(0, 0, w, h)

        val pixels = PixelRender.renderOverBlack(w, h) { canvas -> squircle.draw(canvas) }
        return PixelMask.scan(pixels, w, h, 0, 0, 255, PixelRender.TOLERANCE)
    }

    /** A [w] x [h] mask holding a [side]-sized squircle at ([left], [top]). */
    private fun squircleAt(
        w: Int,
        h: Int,
        left: Int,
        top: Int,
        side: Int,
    ): MaskData {
        val shape = ShapeTemplates.squircle(side, side)
        val bits = BooleanArray(w * h)
        for (y in 0 until side) {
            for (x in 0 until side) {
                if (shape.bits[y * side + x]) bits[(y + top) * w + (x + left)] = true
            }
        }
        return MaskData(
            bits = bits,
            width = w,
            height = h,
            bboxLeft = left,
            bboxTop = top,
            bboxRight = left + side,
            bboxBottom = top + side,
            centroidX = left + shape.centroidX,
            centroidY = top + shape.centroidY,
            pixelCount = shape.pixelCount,
        )
    }

    private companion object {
        /**
         * Minimum IoU between the drawn clip and the superellipse template. The drawable
         * approximates the superellipse with a 256-gon and the clip is not anti-aliased, so a
         * correct clip lands a little under a perfect match at these sizes; a square clip over
         * the same crop window scores about 0.9 against the same template.
         */
        const val MIN_IOU = 0.95f
    }
}
