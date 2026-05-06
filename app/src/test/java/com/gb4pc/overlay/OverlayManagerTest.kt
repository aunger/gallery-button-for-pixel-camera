package com.gb4pc.overlay

import com.gb4pc.Constants
import com.gb4pc.data.OverlayPosition
import org.junit.Assert.*
import org.junit.Test

/**
 * Unit tests for overlay positioning logic (pure Kotlin, no Android framework needed).
 *
 * -- Issue #45: thumbnail overwrite by repeated show() calls --
 *
 * OverlayManager.show() previously called updateIcon() when the overlay was already
 * showing, which reset the ImageView to the gallery app icon and discarded any thumbnail
 * already loaded by showLatestPhotoThumbnail(). The fix removes that updateIcon() call
 * so the icon set at view-creation time (or the thumbnail loaded later) is never
 * silently overwritten by a subsequent show() call.
 *
 * A direct unit test for this behaviour requires OverlayManager to be instantiated,
 * which in turn needs a real Android Context, WindowManager and KeyguardManager. Those
 * are not available in JVM unit tests without Robolectric. See
 * OverlayManagerRobolectricTest for a Robolectric test that verifies:
 *
 *   1. Call show() → overlay is created with the gallery icon drawable.
 *   2. Call showLatestPhotoThumbnail(...) → the ImageView now holds a Bitmap drawable.
 *   3. Call show() again (simulating a second onCameraUnavailable event) → the ImageView
 *      still holds the Bitmap drawable, NOT a Drawable (i.e. updateIcon() was NOT called).
 */
class OverlayManagerTest {

    // ── Issue #39: squircle constant ─────────────────────────────────────────

    /**
     * SQUIRCLE_CORNER_RADIUS_FRACTION must be in the range (0, 0.5] to produce a valid
     * squircle shape. Values outside this range result in either no visible rounding (≤ 0)
     * or a full pill/circle shape (> 0.5).
     *
     * The chosen value (0.30f, 30%) is documented to match Pixel Camera's rounded-square style.
     */
    @Test
    fun `SQUIRCLE_CORNER_RADIUS_FRACTION is within valid squircle range`() {
        val fraction = Constants.SQUIRCLE_CORNER_RADIUS_FRACTION
        assertTrue(
            "Corner radius fraction must be positive (got $fraction)",
            fraction > 0f
        )
        assertTrue(
            "Corner radius fraction must be ≤ 0.5 to avoid pill/circle shape (got $fraction)",
            fraction <= 0.5f
        )
    }

    /**
     * Spot-check: for a 200×200-pixel overlay the squircle corner radius must be exactly
     * 60 px (200 * 0.30).
     */
    @Test
    fun `squircle corner radius calculation is consistent`() {
        val viewSize = 200
        val radius = viewSize * Constants.SQUIRCLE_CORNER_RADIUS_FRACTION
        assertEquals(
            "Radius for a 200 px view must equal 60f (200 * 0.30)",
            60f,
            radius,
            0.001f
        )
    }

    @Test
    fun `calculateOverlaySizePx uses min of width and height`() {
        // size% = 11.5, min(1080, 2400) = 1080
        // expected = 1080 * 11.5 / 100 = 124.2
        val sizePx = calculateOverlaySizePx(
            sizePercent = 11.5f,
            displayWidth = 1080,
            displayHeight = 2400
        )
        assertEquals(124, sizePx)
    }

    @Test
    fun `calculateOverlaySizePx with landscape uses min dimension`() {
        val sizePx = calculateOverlaySizePx(
            sizePercent = 10.0f,
            displayWidth = 2400,
            displayHeight = 1080
        )
        // min(2400, 1080) = 1080, 1080 * 10 / 100 = 108
        assertEquals(108, sizePx)
    }

    @Test
    fun `calculateOverlayXPx positions center of overlay`() {
        val xPx = calculateOverlayXPx(
            xPercent = 50.0f,
            displayWidth = 1080,
            overlaySize = 100
        )
        // center at 50% of 1080 = 540, left edge = 540 - 50 = 490
        assertEquals(490, xPx)
    }

    @Test
    fun `calculateOverlayYPx positions center of overlay`() {
        val yPx = calculateOverlayYPx(
            yPercent = 50.0f,
            displayHeight = 2400,
            overlaySize = 100
        )
        // center at 50% of 2400 = 1200, top edge = 1200 - 50 = 1150
        assertEquals(1150, yPx)
    }

    @Test
    fun `calculateOverlayXPx at 0 percent`() {
        val xPx = calculateOverlayXPx(
            xPercent = 0.0f,
            displayWidth = 1080,
            overlaySize = 100
        )
        // center at 0, left edge = 0 - 50 = -50
        assertEquals(-50, xPx)
    }

    @Test
    fun `default position produces expected pixel values for typical Pixel display`() {
        val pos = OverlayPosition.default()
        val displayWidth = 1080
        val displayHeight = 2400

        val sizePx = calculateOverlaySizePx(pos.sizePercent, displayWidth, displayHeight)
        val xPx = calculateOverlayXPx(pos.xPercent, displayWidth, sizePx)
        val yPx = calculateOverlayYPx(pos.yPercent, displayHeight, sizePx)

        // Size: 1080 * 16.0 / 100 = 172.8 → 173
        assertEquals(173, sizePx)
        // X: 1080 * 20.0 / 100 - 86 = 216 - 86 = 130
        assertEquals(130, xPx)
        // Y: 2400 * 69.0 / 100 - 86 = 1656 - 86 = 1570
        assertEquals(1570, yPx)
    }
}
