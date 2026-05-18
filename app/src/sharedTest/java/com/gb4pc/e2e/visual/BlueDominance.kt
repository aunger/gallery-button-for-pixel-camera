package com.gb4pc.e2e.visual

/**
 * Pure-JVM hue-dominance predicate for ARGB-packed pixel integers.
 *
 * Lives in the sharedTest source set so the same logic compiles into both the
 * androidTest source set (used by `ColorMatch.dominantBlueMask` against real
 * `android.graphics.Bitmap` data) and the JVM unit test source set (where it
 * is exercised directly, without an Android framework, by `BlueDominanceTest`).
 *
 * The ARGB packing convention matches `android.graphics.Color`: bits 31..24 = A,
 * 23..16 = R, 15..8 = G, 7..0 = B. The function reads those bits with plain shifts
 * so no `android.*` dependency leaks into the unit-test classpath.
 */
object BlueDominance {

    /**
     * Returns true when, in the ARGB-packed [pixel], the blue channel exceeds both
     * red and green by at least [minAdvantage].
     *
     * Used by `ColorMatch.dominantBlueMask` to detect the gallery-icon foreground
     * without committing to an exact shade of blue — the adaptive-icon rendering
     * pipeline (vector → bitmap → ImageView outline clip → screen capture) shifts
     * the final pixel values enough that a tight per-channel match against
     * `Rgb.BLUE` can return zero pixels even when the icon is clearly blue. The
     * hue-dominance check is invariant to those shifts.
     *
     * Alpha is ignored.
     */
    fun isBlueDominantArgb(pixel: Int, minAdvantage: Int): Boolean {
        val r = (pixel shr 16) and 0xFF
        val g = (pixel shr 8) and 0xFF
        val b = pixel and 0xFF
        return b - r >= minAdvantage && b - g >= minAdvantage
    }
}
