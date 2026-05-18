package com.gb4pc.e2e.visual

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * JVM unit tests for [BlueDominance.isBlueDominantArgb].
 *
 * Anchors the hue-dominance predicate used by `ColorMatch.dominantBlueMask` to detect the
 * gallery-icon foreground in [com.gb4pc.e2e.GalleryButtonVisualE2ETest.test1a_overlayShowsBlueAtConfiguredPosition].
 * The instrumented test cannot run without an emulator, so the unit test guards the algorithm.
 */
class BlueDominanceTest {

    /** Builds an ARGB packed int the same way `android.graphics.Color.argb` would. */
    private fun argb(r: Int, g: Int, b: Int): Int =
        (0xFF shl 24) or ((r and 0xFF) shl 16) or ((g and 0xFF) shl 8) or (b and 0xFF)

    @Test
    fun `mock-gallery foreground BLUE 1565C0 is dominant`() {
        // Exact icon foreground colour. With minAdvantage=30 the gap to red/green is
        // 0xC0 - 0x65 = 91 and 0xC0 - 0x15 = 171 — both well above the threshold.
        assertTrue(BlueDominance.isBlueDominantArgb(argb(0x15, 0x65, 0xC0), minAdvantage = 30))
    }

    @Test
    fun `anti-aliased edge pixel halfway to yellow is no longer dominant`() {
        // 50% blend between #1565C0 (icon) and #FFD600 (yellow background) — at the icon
        // edge after compositing. The blue channel sinks below red, so it must NOT match.
        val r = (0x15 + 0xFF) / 2
        val g = (0x65 + 0xD6) / 2
        val b = (0xC0 + 0x00) / 2
        assertFalse(BlueDominance.isBlueDominantArgb(argb(r, g, b), minAdvantage = 30))
    }

    @Test
    fun `pure yellow background is rejected`() {
        // YELLOW = #FFD600. Blue is the smallest channel — must never match.
        assertFalse(BlueDominance.isBlueDominantArgb(argb(0xFF, 0xD6, 0x00), minAdvantage = 30))
    }

    @Test
    fun `pure green camera feed is rejected`() {
        // GREEN = #00C853. Blue (0x53) is below green and only slightly above red — must
        // not satisfy a 30-point dominance gate (advantage over green is negative).
        assertFalse(BlueDominance.isBlueDominantArgb(argb(0x00, 0xC8, 0x53), minAdvantage = 30))
    }

    @Test
    fun `near-neutral grey is rejected`() {
        // Mid-grey with all channels equal — no hue dominance.
        assertFalse(BlueDominance.isBlueDominantArgb(argb(0x80, 0x80, 0x80), minAdvantage = 30))
    }

    @Test
    fun `pixel barely meeting threshold is accepted`() {
        // Blue lead is exactly minAdvantage over both other channels.
        assertTrue(BlueDominance.isBlueDominantArgb(argb(0x40, 0x40, 0x40 + 30), minAdvantage = 30))
    }

    @Test
    fun `pixel one short of threshold is rejected`() {
        assertFalse(BlueDominance.isBlueDominantArgb(argb(0x40, 0x40, 0x40 + 29), minAdvantage = 30))
    }

    @Test
    fun `tinted icon shifted toward cyan is still dominant`() {
        // Hypothetical theming that pushes #1565C0 toward cyan (boost green by 0x40, drop
        // red by 0x10). Blue still leads both — must match. Models the "icon theming or
        // tinting producing off-shade pixels" scenario called out in issue #179.
        assertTrue(BlueDominance.isBlueDominantArgb(argb(0x05, 0xA5, 0xD0), minAdvantage = 30))
    }

    @Test
    fun `alpha channel is ignored`() {
        // Identical RGB with different alpha must yield the same result.
        val opaque = argb(0x15, 0x65, 0xC0)
        val translucent = (0x80 shl 24) or (opaque and 0x00FFFFFF)
        assertTrue(BlueDominance.isBlueDominantArgb(opaque, minAdvantage = 30))
        assertTrue(BlueDominance.isBlueDominantArgb(translucent, minAdvantage = 30))
    }
}
