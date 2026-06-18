package com.gb4pc.e2e

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Unit tests for [ScreenshotNaming] — the pure naming-logic helpers that associate
 * screenshot filenames with the test that produced them (issue #201).
 */
class ScreenshotNamingTest {
    // ── buildPrefix ───────────────────────────────────────────────────────────

    @Test
    fun `buildPrefix combines class and method name with underscore`() {
        val prefix =
            ScreenshotNaming.buildPrefix(
                simpleClassName = "PixelCameraOverlayE2ETest",
                methodName = "overlayAppearsWhenViewfinderOpens",
            )
        assertEquals(
            "PixelCameraOverlayE2ETest_overlayAppearsWhenViewfinderOpens",
            prefix,
        )
    }

    @Test
    fun `buildPrefix handles numbered method names`() {
        val prefix =
            ScreenshotNaming.buildPrefix(
                simpleClassName = "GalleryButtonVisualE2ETest",
                methodName = "test0_smokeGreenFeedVisible",
            )
        assertEquals("GalleryButtonVisualE2ETest_test0_smokeGreenFeedVisible", prefix)
    }

    @Test
    fun `buildPrefix handles UnknownClass placeholder`() {
        val prefix = ScreenshotNaming.buildPrefix("UnknownClass", "someMethod")
        assertEquals("UnknownClass_someMethod", prefix)
    }

    // ── resolvedName — with prefix ────────────────────────────────────────────

    @Test
    fun `resolvedName with prefix prepends prefix and underscore`() {
        val name =
            ScreenshotNaming.resolvedName(
                testPrefix = "PixelCameraOverlayE2ETest_overlayAppearsWhenViewfinderOpens",
                baseName = "failure.png",
            )
        assertEquals(
            "PixelCameraOverlayE2ETest_overlayAppearsWhenViewfinderOpens_failure.png",
            name,
        )
    }

    @Test
    fun `resolvedName with prefix matches issue example format`() {
        // Issue #201 example: "failure_screenshot.png" →
        //   "PixelCameraOverlayE2ETest_overlayAppearsWhenViewfinderOpens_failure_screenshot.png"
        val name =
            ScreenshotNaming.resolvedName(
                testPrefix = "PixelCameraOverlayE2ETest_overlayAppearsWhenViewfinderOpens",
                baseName = "failure_screenshot.png",
            )
        assertEquals(
            "PixelCameraOverlayE2ETest_overlayAppearsWhenViewfinderOpens_failure_screenshot.png",
            name,
        )
    }

    @Test
    fun `resolvedName with prefix on numbered screenshot name`() {
        val name =
            ScreenshotNaming.resolvedName(
                testPrefix = "GalleryButtonVisualE2ETest_test0_smokeGreenFeedVisible",
                baseName = "0-screen.png",
            )
        assertEquals(
            "GalleryButtonVisualE2ETest_test0_smokeGreenFeedVisible_0-screen.png",
            name,
        )
    }

    // ── resolvedName — without prefix ─────────────────────────────────────────

    @Test
    fun `resolvedName with null prefix returns base name unchanged`() {
        val name = ScreenshotNaming.resolvedName(testPrefix = null, baseName = "0-screen.png")
        assertEquals("0-screen.png", name)
    }

    @Test
    fun `resolvedName with null prefix does not add underscore`() {
        val name = ScreenshotNaming.resolvedName(testPrefix = null, baseName = "failure.png")
        assertEquals("failure.png", name)
    }

    // ── round-trip: buildPrefix + resolvedName ────────────────────────────────

    @Test
    fun `buildPrefix then resolvedName produces fully qualified filename`() {
        val prefix = ScreenshotNaming.buildPrefix("MyTest", "myMethod")
        val name = ScreenshotNaming.resolvedName(prefix, "screen.png")
        assertEquals("MyTest_myMethod_screen.png", name)
    }
}
