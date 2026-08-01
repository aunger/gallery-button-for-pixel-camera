package com.gb4pc.overlay

import android.app.Application
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.drawable.BitmapDrawable
import android.net.Uri
import android.view.View
import android.view.WindowManager
import android.widget.ImageView
import androidx.test.core.app.ApplicationProvider
import com.gb4pc.data.OverlayPosition
import com.gb4pc.data.PrefsManager
import com.gb4pc.e2e.visual.PixelMask
import com.gb4pc.e2e.visual.ShapeTemplates
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import org.junit.runner.RunWith
import org.mockito.kotlin.any
import org.mockito.kotlin.doReturn
import org.mockito.kotlin.mock
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import org.robolectric.shadows.ShadowLooper
import org.robolectric.shadows.ShadowWindowManagerImpl
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * Pixel-level acceptance test for Issue #767, built to the test design in that issue.
 *
 * For each of the issue's four edge ratios (16:9, 4:3, 9:16, 3:4) a fixture photo is built by
 * the issue's rules: a solid BLUE `s x s` square (where `s` is the photo's short edge) centred
 * on a RED background, at a pixel size on the order of a Pixel Camera output photo. The fixture
 * is served through the content resolver and displayed by the real
 * [OverlayManager.showLatestPhotoThumbnail] path, then the overlay button is rendered over a
 * BLACK backdrop and scanned.
 *
 * Acceptance, quoting the issue: "the entire thumbnail should appear BLUE, with no black, and
 * no RED appearing". Concretely:
 *  - no pixel of the button matches RED, so the fixture's reverse-letterbox bars were cropped
 *    away rather than shown,
 *  - every pixel inside the button's squircle is BLUE, so no BLACK letterbox gap survives,
 *  - the BLUE region spans the button edge to edge on both axes,
 *  - and the BLUE region is still squircle-shaped, so filling the button did not cost the
 *    corner rounding.
 *
 * Verified to fail on both halves of this PR's fix: with `FIT_CENTER` restored, ~9.5k px (4:3)
 * to ~17k px (16:9) inside the button render BLACK; with the squircle clipped against the
 * photo's raw bounds again, the BLUE region's superellipse IoU drops from ~0.99 to ~0.94 as the
 * corners go square.
 *
 * No emulator, no camera app, and no device: the whole path runs on the JVM under Robolectric's
 * native graphics (see [PixelRender]).
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(qualifiers = "w411dp-h891dp-xxhdpi")
class OverlayThumbnailFitPixelTest {
    private companion object {
        /**
         * Fraction of the button size excluded from the "all BLUE" scan at the squircle's
         * boundary. The fixture's BLUE/RED edge lands exactly on the crop window's edge, so the
         * bitmap filtering that downscales a multi-megapixel photo into a ~185 px button blends
         * the two colours across a hairline right at that boundary. The band is far thinner
         * than any letterbox bar this test exists to catch: the narrowest of those, for 4:3,
         * covers 12.5% of the button.
         */
        const val EDGE_INSET_FRACTION = 0.04f

        /** Slack, in pixels, allowed when asserting the BLUE region reaches the button edges. */
        const val EDGE_SLACK_PX = 2

        /**
         * Gates for the squircle-shape check. Measured values are ~0.99 against the superellipse
         * template and ~0.92 against the square one, so these leave headroom for rasterisation
         * noise while still failing a button whose corners have gone square.
         */
        const val MIN_SQUIRCLE_IOU = 0.97f
        const val MIN_SQUIRCLE_MARGIN = 0.03f

        /** Photo sizes for the issue's four edge ratios, at Pixel Camera output resolutions. */
        const val WIDE_16_9_W = 4032
        const val WIDE_16_9_H = 2268
        const val WIDE_4_3_W = 4032
        const val WIDE_4_3_H = 3024
    }

    @Test
    fun `16 by 9 photo fills the overlay thumbnail with no letterbox and no red`() {
        assertThumbnailIsAllBlue(WIDE_16_9_W, WIDE_16_9_H)
    }

    @Test
    fun `4 by 3 photo fills the overlay thumbnail with no letterbox and no red`() {
        assertThumbnailIsAllBlue(WIDE_4_3_W, WIDE_4_3_H)
    }

    @Test
    fun `9 by 16 photo fills the overlay thumbnail with no letterbox and no red`() {
        assertThumbnailIsAllBlue(WIDE_16_9_H, WIDE_16_9_W)
    }

    @Test
    fun `3 by 4 photo fills the overlay thumbnail with no letterbox and no red`() {
        assertThumbnailIsAllBlue(WIDE_4_3_H, WIDE_4_3_W)
    }

    // ── the acceptance check ─────────────────────────────────────────────────

    /**
     * Shows the overlay, feeds it a [photoW] x [photoH] fixture photo through the production
     * thumbnail path, renders the button, and asserts the issue's acceptance criteria.
     */
    private fun assertThumbnailIsAllBlue(
        photoW: Int,
        photoH: Int,
    ) {
        val photo = "$photoW x $photoH"
        val context: Application = ApplicationProvider.getApplicationContext()
        val prefsManager: PrefsManager =
            mock {
                on { galleryPackage } doReturn null
                on { getOverlayPosition(any()) } doReturn OverlayPosition.default()
                on { focusableOverlay } doReturn false
            }

        val overlayManager = OverlayManager(context, prefsManager)
        overlayManager.show()

        val windowManager = context.getSystemService(WindowManager::class.java)
        val shadowWm = shadowOf(windowManager) as ShadowWindowManagerImpl
        val overlayView = shadowWm.views.single() as ImageView
        val params = overlayView.layoutParams as WindowManager.LayoutParams
        val size = params.width
        assertEquals("The overlay window must be square for this test to mean anything.", size, params.height)
        assertTrue("Overlay window is too small to scan: $size px", size >= 32)

        // Serve the fixture from the content resolver and drive the real thumbnail path
        // (background decode, main-looper hand-off, SquircleDrawable(BitmapDrawable(...))
        // wrapping) rather than handing the view a drawable directly.
        val uri = Uri.parse("content://com.gb4pc.test.photos/$photoW-$photoH")
        val png = fixturePhotoPng(photoW, photoH)
        shadowOf(context.contentResolver).registerInputStreamSupplier(uri) { ByteArrayInputStream(png) }
        overlayManager.showLatestPhotoThumbnail(uri.toString())
        val thumbnail = awaitThumbnailBitmap(overlayView, photo)

        // Sanity: the loaded thumbnail still carries the fixture's edge ratio, so what the
        // ImageView is asked to fit is genuinely non-square.
        assertEquals(
            "Loaded thumbnail for $photo must keep the fixture's edge ratio " +
                "(got ${thumbnail.width} x ${thumbnail.height}).",
            photoW.toFloat() / photoH,
            thumbnail.width.toFloat() / thumbnail.height,
            0.02f,
        )

        overlayView.measure(
            View.MeasureSpec.makeMeasureSpec(size, View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(size, View.MeasureSpec.EXACTLY),
        )
        overlayView.layout(0, 0, size, size)
        val pixels = PixelRender.renderOverBlack(size, size) { canvas -> overlayView.draw(canvas) }

        // 1. "no RED appearing": the fixture's reverse-letterbox bars must be cropped away.
        val red = PixelMask.scan(pixels, size, size, 255, 0, 0, PixelRender.TOLERANCE)
        assertEquals(
            "Issue #767: photo $photo, no RED may appear in the overlay thumbnail, but " +
                "${red.pixelCount} of ${size * size} px matched RED (bbox " +
                "${red.bboxLeft},${red.bboxTop}-${red.bboxRight},${red.bboxBottom}). The " +
                "thumbnail is fitting the long edge, so the fixture's RED bars are on screen.",
            0,
            red.pixelCount,
        )

        // 2. "the entire thumbnail should appear BLUE, with no black": every pixel inside the
        // button's squircle must be BLUE. Whatever the photo fails to cover shows the BLACK
        // backdrop it was rendered over.
        val inset = maxOf(2, (size * EDGE_INSET_FRACTION).roundToInt())
        val notBlue = countNonBlueInsideSquircle(pixels, size, inset)
        assertEquals(
            "Issue #767: photo $photo, the whole thumbnail must be BLUE, but $notBlue px " +
                "inside the button's squircle (inset $inset px) were not BLUE. Letterbox bars " +
                "render as the BLACK backdrop.",
            0,
            notBlue,
        )

        // 3. The BLUE region reaches all four edges: the photo fills the button rather than
        // sitting centred inside it.
        val blue = PixelMask.scan(pixels, size, size, 0, 0, 255, PixelRender.TOLERANCE)
        assertTrue(
            "Issue #767: photo $photo, BLUE must reach the left and top edges of the " +
                "${size}px button (bbox left=${blue.bboxLeft}, top=${blue.bboxTop}).",
            blue.bboxLeft <= EDGE_SLACK_PX && blue.bboxTop <= EDGE_SLACK_PX,
        )
        assertTrue(
            "Issue #767: photo $photo, BLUE must reach the right and bottom edges of the " +
                "${size}px button (bbox right=${blue.bboxRight}, bottom=${blue.bboxBottom}).",
            blue.bboxRight >= size - EDGE_SLACK_PX && blue.bboxBottom >= size - EDGE_SLACK_PX,
        )

        // 4. The button is still a squircle: cropping the photo must not cost the corner
        // rounding. The BLUE region has to match the superellipse template and beat the square
        // template by a clear margin.
        val squircleIoU = PixelRender.alignedIoU(blue, ShapeTemplates.squircle(size, size))
        val squareIoU = PixelRender.alignedIoU(blue, ShapeTemplates.square(size, size))
        assertTrue(
            "Issue #767: photo $photo, the BLUE thumbnail must fill the button's squircle " +
                "(IoU $squircleIoU against the superellipse template, min $MIN_SQUIRCLE_IOU).",
            squircleIoU >= MIN_SQUIRCLE_IOU,
        )
        assertTrue(
            "Issue #767: photo $photo, the thumbnail must be squircle-shaped, not square " +
                "(squircle IoU $squircleIoU vs square IoU $squareIoU, min margin " +
                "$MIN_SQUIRCLE_MARGIN). A square result means the squircle clip was built " +
                "against the photo's own non-square bounds instead of the crop window.",
            squircleIoU - squareIoU >= MIN_SQUIRCLE_MARGIN,
        )

        overlayManager.hide()
    }

    // ── fixture and helpers ──────────────────────────────────────────────────

    /**
     * Builds the issue's fixture photo as PNG bytes: a solid BLUE square of the photo's short
     * edge, centred, with every remaining pixel RED. PNG rather than JPEG so the two colours
     * stay exact and the scan measures the overlay's scaling, not codec noise.
     */
    private fun fixturePhotoPng(
        w: Int,
        h: Int,
    ): ByteArray {
        val bitmap = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
        try {
            val canvas = Canvas(bitmap)
            canvas.drawColor(Color.RED)
            val side = min(w, h).toFloat()
            val left = (w - side) / 2f
            val top = (h - side) / 2f
            canvas.drawRect(left, top, left + side, top + side, Paint().apply { color = Color.BLUE })
            val out = ByteArrayOutputStream()
            bitmap.compress(Bitmap.CompressFormat.PNG, 100, out)
            return out.toByteArray()
        } finally {
            bitmap.recycle()
        }
    }

    /**
     * Counts pixels inside the button's squircle, shrunk by [inset] px on every side, that do
     * not match BLUE.
     */
    private fun countNonBlueInsideSquircle(
        pixels: IntArray,
        size: Int,
        inset: Int,
    ): Int {
        val inner = ShapeTemplates.squircle(size - 2 * inset, size - 2 * inset)
        var notBlue = 0
        for (y in 0 until inner.height) {
            for (x in 0 until inner.width) {
                if (!inner.bits[y * inner.width + x]) continue
                val pixel = pixels[(y + inset) * size + (x + inset)]
                if (!PixelMask.matches(pixel, 0, 0, 255, PixelRender.TOLERANCE)) notBlue++
            }
        }
        return notBlue
    }

    /**
     * Waits for [OverlayManager.showLatestPhotoThumbnail]'s background decode to hand its
     * bitmap to the main looper and land on [view], then returns that bitmap.
     */
    private fun awaitThumbnailBitmap(
        view: ImageView,
        photo: String,
    ): Bitmap {
        val deadline = System.currentTimeMillis() + 30_000
        while (System.currentTimeMillis() < deadline) {
            ShadowLooper.idleMainLooper()
            val inner = (view.drawable as? SquircleDrawable)?.inner
            // The gallery-icon placeholder this overlay starts with is a vector/layer drawable,
            // never a BitmapDrawable, so this only matches once the photo has arrived.
            if (inner is BitmapDrawable) return inner.bitmap
            Thread.sleep(20)
        }
        fail("Thumbnail for photo $photo never reached the overlay ImageView.")
        error("unreachable")
    }
}
