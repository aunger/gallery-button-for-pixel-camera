package com.gb4pc.overlay

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import com.gb4pc.e2e.visual.MaskData

/**
 * Shared rendering helpers for the overlay's pixel-level Robolectric tests.
 *
 * These tests draw real pixels rather than inspecting framework or drawable internals, which
 * requires [org.robolectric.annotation.GraphicsMode.Mode.NATIVE]: Robolectric's default LEGACY
 * graphics mode stubs drawing out entirely, so every readback comes back untouched and pixel
 * assertions look impossible. Every caller of these helpers must carry that annotation.
 *
 * The scan side is [com.gb4pc.e2e.visual.PixelMask], the same pure-JVM colour-mask scanner the
 * on-device E2E visual suite uses, so a unit test and an E2E test describe a rendered button the
 * same way.
 */
internal object PixelRender {
    /** Per-channel match tolerance, matching `ColorMatch`'s on-device default. */
    const val TOLERANCE = 20

    /**
     * Runs [draw] against a [width] x [height] canvas pre-filled with opaque BLACK and returns
     * the result as row-major ARGB pixels.
     *
     * The BLACK fill stands in for whatever lies behind the overlay window: anything the drawing
     * fails to cover reads back as black instead of as an ambiguous transparent pixel.
     */
    fun renderOverBlack(
        width: Int,
        height: Int,
        draw: (Canvas) -> Unit,
    ): IntArray {
        val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        try {
            val canvas = Canvas(bitmap)
            canvas.drawColor(Color.BLACK)
            draw(canvas)
            val pixels = IntArray(width * height)
            bitmap.getPixels(pixels, 0, width, 0, 0, width, height)
            return pixels
        } finally {
            bitmap.recycle()
        }
    }

    /**
     * Intersection-over-union of [mask] and [template], which must be the same size and are
     * compared in place (no position or scale sweep, unlike
     * [com.gb4pc.e2e.visual.ShapeMatcher.classify]): a rendered overlay is already aligned with
     * the template generated at its own dimensions, and holding the alignment fixed keeps the
     * comparison sensitive to a shape that has shifted as well as one that has changed.
     */
    fun alignedIoU(
        mask: MaskData,
        template: MaskData,
    ): Float {
        require(mask.width == template.width && mask.height == template.height) {
            "alignedIoU needs equal sizes: ${mask.width}x${mask.height} vs " +
                "${template.width}x${template.height}"
        }
        var intersection = 0
        var union = 0
        for (i in mask.bits.indices) {
            val inMask = mask.bits[i]
            val inTemplate = template.bits[i]
            if (inMask && inTemplate) intersection++
            if (inMask || inTemplate) union++
        }
        return if (union == 0) 1f else intersection.toFloat() / union
    }
}
