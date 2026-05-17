package com.gb4pc.e2e

import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.PointF
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.gb4pc.data.AspectRatioUtil
import com.gb4pc.data.PrefsManager
import com.gb4pc.e2e.visual.BinaryMask
import com.gb4pc.e2e.visual.ColorMatch
import com.gb4pc.e2e.visual.Rgb
import com.gb4pc.e2e.visual.Screenshot
import com.gb4pc.e2e.visual.Shape
import com.gb4pc.e2e.visual.ShapeMatcher
import com.gb4pc.e2e.visual.toMaskData
import org.junit.Assert.fail
import org.junit.Before
import org.junit.FixMethodOrder
import org.junit.Test
import org.junit.runner.RunWith
import org.junit.runners.MethodSorters
import kotlin.math.sqrt

/**
 * Visual E2E tests for the gallery button overlay (Phase 5).
 *
 * Tests are ordered alphabetically by method name via [FixMethodOrder] with
 * [MethodSorters.NAME_ASCENDING]. The prefix scheme (`test0_`, `test1a_`, …, `test5a_`)
 * guarantees the correct numeric order.
 *
 * Each test is fully self-contained: it sets its own device state in per-test setup
 * (seeding prefs, clearing the camera roll, locking the screen, etc.) and does not
 * rely on state left by a previous test.
 *
 * All screenshots and binary masks used in assertions are saved via [Screenshot.saveForArtifact]
 * so CI artifact pickup provides reviewable PNGs on failure.
 *
 * Prerequisites:
 *   - Mock-camera APK installed under [com.google.android.GoogleCamera].
 *   - Mock-gallery APK (`com.gb4pc.mockgallery`) installed.
 *   - SYSTEM_ALERT_WINDOW and GET_USAGE_STATS permissions granted to `:app`.
 *   - Emulator or device configured with a GREEN (#00C853) camera feed (see Phase 2).
 */
@E2ETest
@RunWith(AndroidJUnit4::class)
@FixMethodOrder(MethodSorters.NAME_ASCENDING)
class GalleryButtonVisualE2ETest {

    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context = instrumentation.targetContext
    private val fixture = E2EFixture(
        context = context,
        uiAutomation = instrumentation.uiAutomation,
    )

    @Before
    fun setUp() = fixture.setUp()

    // ── test0 — Smoke: camera feed is GREEN ──────────────────────────────────

    /**
     * Verifies that the mock camera produces a visually GREEN feed by asserting that the
     * central 60% of the screen is at least 70% covered by GREEN (#00C853) pixels.
     *
     * Under Alternative 1, MockCameraActivity renders a solid-green (#00C853) View — no camera
     * hardware and no virtualscene poster are required. This is the first line of defence: if
     * this test fails, either the mock-camera APK is not installed under [MOCK_CAMERA_PACKAGE]
     * or MockCameraActivity is not in the foreground.
     */
    @Test
    fun test0_smokeGreenFeedVisible() {
        fixture.launchPixelCamera()

        // Poll up to 30 s for the camera feed to render — a fixed 1 s pause is too short
        // on a cold-started emulator. waitForGreenCoverage returns the last measured coverage.
        val coverage = fixture.waitForGreenCoverage(minCoverage = 0.70f, timeoutMs = 30_000L)

        val screen = Screenshot.captureScreen()
        Screenshot.saveForArtifact(screen, "0-screen.png")

        if (coverage <= 0.70f) {
            fail(
                "test0_smokeGreenFeedVisible: GREEN coverage in central 60% of screen is " +
                    "${coverage * 100f}% — expected > 70%. " +
                    "Check that the mock-camera APK is installed under $MOCK_CAMERA_PACKAGE " +
                    "and MockCameraActivity is in the foreground."
            )
        }
    }

    // ── test1a — Overlay BLUE pixel centroid is at the configured position ───

    /**
     * Verifies that the gallery icon's BLUE foreground square is centred at the configured
     * overlay position. The centroid of all BLUE pixels in the screenshot must land within
     * one icon radius of (xPercent, yPercent).
     *
     * xPercent / yPercent refer to the **centre** of the overlay (confirmed from OverlayManager
     * — it computes `centerX = displayWidth * xPercent / 100f` and subtracts half the icon size
     * to get the left edge). No correction is needed.
     */
    @Test
    fun test1a_overlayShowsBlueAtConfiguredPosition() {
        fixture.seedGalleryPrefs(MOCK_GALLERY_PACKAGE)
        fixture.clearCameraRoll()
        fixture.launchPixelCamera()
        // Wait for UsageStats-based foreground detection to activate the overlay, then
        // allow one additional frame for the WM to composite the overlay window on screen.
        fixture.waitForOverlayActive()
        fixture.pause(500)

        val screen = Screenshot.captureScreen()
        val blue = ColorMatch.mask(screen, Rgb.BLUE)
        Screenshot.saveForArtifact(screen, "1a-screen.png")
        Screenshot.saveForArtifact(maskToBitmap(blue), "1a-blue-mask.png")

        val (displayWidth, displayHeight) = fixture.displaySize()
        val aspectRatio = AspectRatioUtil.quantize(displayWidth, displayHeight)
        val pos = PrefsManager(context).getOverlayPosition(aspectRatio)

        val minDim = minOf(screen.width, screen.height).toFloat()
        // sizePercent is the icon's edge length as a fraction of minDim.
        // iconRadiusPx = half the icon's pixel diameter — the centroid should be within this.
        val iconRadiusPx = (pos.sizePercent / 200f) * minDim

        // xPercent / yPercent are the overlay's centre (see OverlayManager.calculateOverlayXPx).
        val expectedX = pos.xPercent / 100f * screen.width
        val expectedY = pos.yPercent / 100f * screen.height

        val dist = distance(blue.centroid, PointF(expectedX, expectedY))
        if (dist >= iconRadiusPx) {
            fail(
                "test1a_overlayShowsBlueAtConfiguredPosition: BLUE centroid at " +
                    "(${blue.centroid.x}, ${blue.centroid.y}) is $dist px from expected " +
                    "($expectedX, $expectedY). Tolerance = iconRadiusPx = $iconRadiusPx px. " +
                    "pos=$pos, screen=${screen.width}x${screen.height}, " +
                    "aspect=$aspectRatio, pixelCount=${blue.pixelCount}"
            )
        }
    }

    // ── test1b — Overlay BLUE region is square ───────────────────────────────

    /**
     * Verifies that the gallery icon's BLUE foreground square renders as a square shape,
     * as expected from the mock-gallery adaptive icon design (a centered square foreground).
     */
    @Test
    fun test1b_overlayBlueIsSquare() {
        fixture.seedGalleryPrefs(MOCK_GALLERY_PACKAGE)
        fixture.clearCameraRoll()
        fixture.launchPixelCamera()
        // Wait for UsageStats-based foreground detection to activate the overlay, then
        // allow one additional frame for the WM to composite the overlay window on screen.
        fixture.waitForOverlayActive()
        fixture.pause(500)

        val screen = Screenshot.captureScreen()
        val blue = ColorMatch.mask(screen, Rgb.BLUE)
        Screenshot.saveForArtifact(screen, "1b-screen.png")
        Screenshot.saveForArtifact(maskToBitmap(blue), "1b-blue-mask.png")

        val croppedBlue = ColorMatch.crop(blue)
        ShapeMatcher.requireShape(croppedBlue.toMaskData(), Shape.SQUARE)
    }

    // ── test1c — Overlay outer silhouette (BLUE ∪ YELLOW) is a squircle ─────

    /**
     * Verifies that the outer silhouette of the gallery icon (the union of BLUE foreground
     * and YELLOW background pixels) renders as a squircle, matching Android's adaptive-icon
     * mask shape.
     */
    @Test
    fun test1c_overlayOuterIsSquircle() {
        fixture.seedGalleryPrefs(MOCK_GALLERY_PACKAGE)
        fixture.clearCameraRoll()
        fixture.launchPixelCamera()
        // Wait for UsageStats-based foreground detection to activate the overlay, then
        // allow one additional frame for the WM to composite the overlay window on screen.
        fixture.waitForOverlayActive()
        fixture.pause(500)

        val screen = Screenshot.captureScreen()
        val outer = ColorMatch.union(
            ColorMatch.mask(screen, Rgb.BLUE),
            ColorMatch.mask(screen, Rgb.YELLOW)
        )
        Screenshot.saveForArtifact(screen, "1c-screen.png")
        Screenshot.saveForArtifact(maskToBitmap(outer), "1c-outer-mask.png")

        ShapeMatcher.requireShape(ColorMatch.crop(outer).toMaskData(), Shape.SQUIRCLE)
    }

    // ── test2a — Empty gallery: tapping overlay shows no GREEN ───────────────

    /**
     * Verifies that tapping the overlay when the camera roll is empty does not open a
     * GREEN-filled gallery screen. GREEN coverage after tap must be below 10%.
     *
     * An empty gallery should show a black empty state (per mock-gallery's design), not GREEN.
     */
    @Test
    fun test2a_emptyGalleryNoGreenAfterTap() {
        fixture.seedGalleryPrefs(MOCK_GALLERY_PACKAGE)
        fixture.clearCameraRoll()
        fixture.launchPixelCamera()
        // Wait for the overlay to be active before tapping — a fixed 1 s pause is too short.
        fixture.waitForOverlayActive()

        val s1 = Screenshot.captureScreen()
        Screenshot.saveForArtifact(s1, "2a-s1.png")

        fixture.tapOverlay()
        fixture.pause(1000)

        val s2 = Screenshot.captureScreen()
        Screenshot.saveForArtifact(s2, "2a-s2.png")

        val greenMask = ColorMatch.mask(s2, Rgb.GREEN)
        Screenshot.saveForArtifact(maskToBitmap(greenMask), "2a-green-mask.png")

        val coverage = ColorMatch.coverageFraction(greenMask)
        if (coverage >= 0.10f) {
            fail(
                "test2a_emptyGalleryNoGreenAfterTap: GREEN coverage after tap is " +
                    "${coverage * 100f}% — expected < 10%. " +
                    "The gallery opened showing unexpected green content with an empty roll."
            )
        }
    }

    // ── test3a — Populated gallery: tapping overlay shows GREEN ──────────────

    /**
     * Verifies that tapping the overlay when the camera roll contains one GREEN photo opens
     * the gallery and shows the GREEN content. Coverage after tap must exceed 40%.
     */
    @Test
    fun test3a_populatedGalleryShowsGreenAfterTap() {
        fixture.seedGalleryPrefs(MOCK_GALLERY_PACKAGE)
        fixture.clearCameraRoll()
        // Launch the camera first so MockCameraActivity's BroadcastReceiver is registered
        // before the ACTION_SHUTTER broadcast is sent — sending it before onResume() means
        // the receiver is not yet registered and the broadcast is silently dropped.
        fixture.launchPixelCamera()
        fixture.waitForOverlayActive()
        fixture.captureOnePhoto()

        val s1 = Screenshot.captureScreen()
        Screenshot.saveForArtifact(s1, "3a-s1.png")

        fixture.tapOverlay()
        fixture.pause(1000)

        val s2 = Screenshot.captureScreen()
        Screenshot.saveForArtifact(s2, "3a-s2.png")

        val greenMask = ColorMatch.mask(s2, Rgb.GREEN)
        Screenshot.saveForArtifact(maskToBitmap(greenMask), "3a-green-mask.png")

        val coverage = ColorMatch.coverageFraction(greenMask)
        if (coverage <= 0.40f) {
            fail(
                "test3a_populatedGalleryShowsGreenAfterTap: GREEN coverage after tap is " +
                    "${coverage * 100f}% — expected > 40%. " +
                    "The gallery did not open, or the captured photo is not green."
            )
        }
    }

    // ── test4a — Secure camera + empty gallery: no GREEN after tap ───────────

    /**
     * Verifies that tapping the overlay in secure-camera mode (screen locked) with an empty
     * camera roll does not show GREEN content (coverage < 10%).
     *
     * **Alternative 1 note — this is a RED-LIGHT test, not a baseline pass.**
     *
     * The original plan claimed this test "passes regardless of the regression" because an
     * empty gallery produces a black empty state → no GREEN. That reasoning assumed the camera
     * background was black (e.g. real hardware or a virtualscene with a dark scene). Under
     * Alternative 1, MockCameraActivity's background is solid green (#00C853).
     *
     * When the secure-camera overlay regression is present (overlay is blocked / not tappable),
     * `tapOverlay()` is a no-op and the green MockCameraActivity stays on screen — giving ~100%
     * GREEN coverage. This means `coverage >= 0.10f` and the assertion FAILS.
     *
     * Conversely, once the regression is fixed (overlay renders correctly in secure-camera mode),
     * the tap succeeds, the gallery opens, and the empty-state screen is black → coverage < 10%
     * → assertion PASSES.
     *
     * The assertion itself (`coverage(GREEN) < 10%`) is correct and produces the right signal:
     * it fails when the regression is present and MockCameraActivity is green, and passes when
     * the overlay works and the empty gallery is shown. Do not change the assertion.
     *
     * Tracking issue: #156 — same as test5a. Do NOT skip, ignore, or quarantine this test.
     */
    @Test
    fun test4a_secureCameraLockedEmptyGalleryNoGreen() {
        fixture.seedGalleryPrefs(MOCK_GALLERY_PACKAGE)
        fixture.clearCameraRoll()
        fixture.lockScreen()
        fixture.launchSecureCamera()
        fixture.pause(1000)

        val s1 = Screenshot.captureScreen()
        Screenshot.saveForArtifact(s1, "4a-s1.png")

        fixture.tapOverlay()
        fixture.pause(1000)

        val s2 = Screenshot.captureScreen()
        Screenshot.saveForArtifact(s2, "4a-s2.png")

        val greenMask = ColorMatch.mask(s2, Rgb.GREEN)
        Screenshot.saveForArtifact(maskToBitmap(greenMask), "4a-green-mask.png")

        val coverage = ColorMatch.coverageFraction(greenMask)
        if (coverage >= 0.10f) {
            fail(
                "test4a_secureCameraLockedEmptyGalleryNoGreen: GREEN coverage after tap is " +
                    "${coverage * 100f}% — expected < 10%. " +
                    "The gallery opened showing unexpected green content with an empty roll."
            )
        }
    }

    // ── test5a — Secure camera + populated gallery: EXPECTED TO FAIL ─────────

    /**
     * RED-LIGHT TEST — intentional failure at baseline.
     *
     * Verifies that tapping the overlay in secure-camera mode (screen locked) when the camera
     * roll contains a GREEN photo opens the gallery and displays the photo (coverage > 40%).
     *
     * This test is a regression marker for the known secure-camera overlay issue: the overlay
     * is currently not rendered (or not tappable) in secure-camera mode, so the tap is a no-op
     * and the screen stays on the camera feed, failing the GREEN coverage assertion.
     *
     * Tracking issue: #156 — Do NOT skip, ignore, or quarantine this test. It must remain
     * a visible red until the secure-camera overlay path is restored in a separate PR.
     */
    @Test
    fun test5a_secureCameraLockedPopulatedGalleryShowsGreen() {
        fixture.seedGalleryPrefs(MOCK_GALLERY_PACKAGE)
        fixture.clearCameraRoll()
        // Capture the photo while the camera activity is running — MockCameraActivity's
        // BroadcastReceiver is only registered in onResume(), so launching the camera first
        // is required. After capture, return to home and stop the camera before locking.
        fixture.launchPixelCamera()
        fixture.waitForOverlayActive()
        fixture.captureOnePhoto()   // GREEN JPEG now in MediaStore
        fixture.goHome()
        fixture.stopPixelCamera()
        fixture.lockScreen()
        fixture.launchSecureCamera()
        fixture.pause(1000)

        val s1 = Screenshot.captureScreen()
        Screenshot.saveForArtifact(s1, "5a-s1.png")

        fixture.tapOverlay()   // taps overlay position; no-op at baseline (overlay blocked)
        fixture.pause(1000)

        val s2 = Screenshot.captureScreen()
        Screenshot.saveForArtifact(s2, "5a-s2.png")

        val greenMask = ColorMatch.mask(s2, Rgb.GREEN)
        Screenshot.saveForArtifact(maskToBitmap(greenMask), "5a-green-mask.png")

        val coverage = ColorMatch.coverageFraction(greenMask)
        if (coverage <= 0.40f) {
            fail(
                "test5a_secureCameraLockedPopulatedGalleryShowsGreen: GREEN coverage after tap " +
                    "is ${coverage * 100f}% — expected > 40%. " +
                    "This is the expected baseline failure for the secure-camera overlay regression " +
                    "(issue #156). The overlay is not rendered or not tappable while the keyguard " +
                    "is active, so the gallery did not open."
            )
        }
    }

    // ── Private helpers ───────────────────────────────────────────────────────

    /**
     * Returns the Euclidean distance between two [PointF]s.
     */
    private fun distance(a: PointF, b: PointF): Float {
        val dx = a.x - b.x
        val dy = a.y - b.y
        return sqrt(dx * dx + dy * dy)
    }

    /**
     * Converts a [BinaryMask] to a [Bitmap] for artifact saving.
     *
     * True pixels are rendered as white (#FFFFFF), false pixels as black (#000000), with full
     * opacity. Useful for debugging: uploading the mask bitmap alongside the screenshot makes
     * the color-matching result immediately reviewable.
     */
    private fun maskToBitmap(mask: BinaryMask): Bitmap {
        val bmp = Bitmap.createBitmap(mask.width, mask.height, Bitmap.Config.ARGB_8888)
        for (y in 0 until mask.height) {
            for (x in 0 until mask.width) {
                bmp.setPixel(x, y, if (mask.bits[y * mask.width + x]) Color.WHITE else Color.BLACK)
            }
        }
        return bmp
    }

    companion object {
        /** Package name of the mock gallery APK (see `:e2e-mock-gallery` module). */
        private const val MOCK_GALLERY_PACKAGE = "com.gb4pc.mockgallery"

        /** Package name of the mock camera APK (installed under the Pixel Camera package). */
        private const val MOCK_CAMERA_PACKAGE = "com.google.android.GoogleCamera"
    }
}
