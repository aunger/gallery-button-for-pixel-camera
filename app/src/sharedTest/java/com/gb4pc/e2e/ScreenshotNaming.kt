package com.gb4pc.e2e

/**
 * Pure-logic helpers for associating screenshot filenames with their producing test.
 *
 * These functions contain no Android dependencies so they can be tested in the JVM
 * unit-test environment as well as used by the Android instrumented test infrastructure.
 *
 * All Android-specific I/O (directory resolution, file writing) lives in
 * [com.gb4pc.e2e.visual.Screenshot] and [ScreenshotTestRule], which delegate to this
 * object for filename computation.
 */
object ScreenshotNaming {

    /**
     * Builds the canonical test-prefix string for the given class and method names.
     *
     * Format: `"<simpleClassName>_<methodName>"`
     *
     * Example: `"PixelCameraOverlayE2ETest_overlayAppearsWhenViewfinderOpens"`
     */
    fun buildPrefix(simpleClassName: String, methodName: String): String =
        "${simpleClassName}_${methodName}"

    /**
     * Returns the artifact filename for [baseName] given an optional [testPrefix].
     *
     * - If [testPrefix] is non-null: `"<testPrefix>_<baseName>"`
     * - If [testPrefix] is null: `<baseName>` unchanged
     *
     * Examples:
     * ```
     * resolvedName("GalleryButtonVisualE2ETest_test0_smokeGreenFeedVisible", "0-screen.png")
     *   // → "GalleryButtonVisualE2ETest_test0_smokeGreenFeedVisible_0-screen.png"
     *
     * resolvedName(null, "0-screen.png")
     *   // → "0-screen.png"
     * ```
     */
    fun resolvedName(testPrefix: String?, baseName: String): String =
        if (testPrefix != null) "${testPrefix}_$baseName" else baseName
}
