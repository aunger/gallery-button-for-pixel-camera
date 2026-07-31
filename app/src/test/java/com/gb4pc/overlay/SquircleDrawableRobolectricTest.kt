package com.gb4pc.overlay

import android.app.Application
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.drawable.BitmapDrawable
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

/**
 * Robolectric regression tests for [SquircleDrawable]'s clip-path sizing.
 *
 * Issue #767 follow-up: CENTER_CROP (introduced by #767 for photo thumbnails) leaves a wrapped
 * drawable's own `bounds` at its raw, unscaled intrinsic size, which is not square for a
 * non-square photo. [SquircleDrawable] must clip against the centered square sub-region of
 * those bounds ([centeredSquareRegion]), not the full bounds, or the squircle's curvature ends
 * up concentrated almost entirely outside CENTER_CROP's visible crop window, rendering the
 * button's corners essentially sharp instead of rounded.
 *
 * These tests read [SquircleDrawable]'s private `pathWidth` / `pathHeight` cache fields
 * (populated by plain field assignment, not native graphics) after a real `draw()` call,
 * rather than asserting on rendered pixels: OverlayManagerRobolectricTest documents that this
 * Robolectric setup does not apply a translated `Canvas.concat`/`translate` to a subsequent
 * bitmap draw, which would make a pixel-level assertion unreliable here.
 */
@RunWith(RobolectricTestRunner::class)
class SquircleDrawableRobolectricTest {
    private fun pathDimensionsAfterDraw(
        w: Int,
        h: Int,
    ): Pair<Int, Int> {
        val context: Application = ApplicationProvider.getApplicationContext()
        val bitmap = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
        val squircle = SquircleDrawable(BitmapDrawable(context.resources, bitmap))
        squircle.setBounds(0, 0, w, h)

        val canvasSide = maxOf(w, h)
        squircle.draw(Canvas(Bitmap.createBitmap(canvasSide, canvasSide, Bitmap.Config.ARGB_8888)))

        val widthField = SquircleDrawable::class.java.getDeclaredField("pathWidth").apply { isAccessible = true }
        val heightField = SquircleDrawable::class.java.getDeclaredField("pathHeight").apply { isAccessible = true }
        return (widthField.get(squircle) as Int) to (heightField.get(squircle) as Int)
    }

    @Test
    fun `clip path is built from the square crop window for a wide non-square drawable`() {
        val (pathW, pathH) = pathDimensionsAfterDraw(160, 90)
        assertEquals(
            "Issue #767: clip path width must equal the short edge (CENTER_CROP's crop " +
                "window), not the raw drawable width.",
            90,
            pathW,
        )
        assertEquals(
            "Issue #767: clip path height must equal the short edge.",
            90,
            pathH,
        )
    }

    @Test
    fun `clip path is built from the square crop window for a tall non-square drawable`() {
        val (pathW, pathH) = pathDimensionsAfterDraw(90, 160)
        assertEquals(
            "Issue #767: clip path width must equal the short edge.",
            90,
            pathW,
        )
        assertEquals(
            "Issue #767: clip path height must equal the short edge (CENTER_CROP's crop " +
                "window), not the raw drawable height.",
            90,
            pathH,
        )
    }

    @Test
    fun `clip path matches the full bounds for an already-square drawable`() {
        val (pathW, pathH) = pathDimensionsAfterDraw(64, 64)
        assertEquals("Square bounds must be unaffected by the Issue #767 follow-up.", 64, pathW)
        assertEquals("Square bounds must be unaffected by the Issue #767 follow-up.", 64, pathH)
    }
}
