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
import org.junit.Rule
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
    @get:Rule
    val screenshotRule = ScreenshotTestRule()

    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context = instrumentation.targetContext
    private val fixture =
        E2EFixture(
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
                    "and MockCameraActivity is in the foreground.",
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
                    "aspect=$aspectRatio, pixelCount=${blue.pixelCount}",
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
        val outer =
            ColorMatch.union(
                ColorMatch.mask(screen, Rgb.BLUE),
                ColorMatch.mask(screen, Rgb.YELLOW),
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
                    "The gallery opened showing unexpected green content with an empty roll.",
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

        // Poll up to 15 s for the gallery's photo to render — a fixed 1 s pause races
        // LastPhotoActivity's cold start (process spawn + MediaStore query + JPEG decode),
        // which the CI logcat shows can take ~1.4 s. waitForGreenCoverage's return value is
        // discarded: it only gates the wait, and the assertion below re-measures full-screen
        // coverage on a fresh screenshot against the original 40% threshold.
        fixture.waitForGreenCoverage(minCoverage = 0.40f, timeoutMs = 15_000L)

        val s2 = Screenshot.captureScreen()
        Screenshot.saveForArtifact(s2, "3a-s2.png")

        val greenMask = ColorMatch.mask(s2, Rgb.GREEN)
        Screenshot.saveForArtifact(maskToBitmap(greenMask), "3a-green-mask.png")

        val coverage = ColorMatch.coverageFraction(greenMask)
        if (coverage <= 0.40f) {
            fail(
                "test3a_populatedGalleryShowsGreenAfterTap: GREEN coverage after tap is " +
                    "${coverage * 100f}% — expected > 40%. " +
                    "The gallery did not open, or the captured photo is not green.",
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
     * **Cross-package roll cleanup (issue #406 — resolves the PR #400 caveat).**
     *
     * `test3a` (which runs alphabetically before this test) captures a GREEN photo owned by
     * `com.google.android.GoogleCamera` (the mock camera). Earlier, [E2EFixture.clearCameraRoll]
     * could only delete rows owned by `com.gb4pc`, so that cross-package photo survived into
     * this test and would have made the "empty gallery" assumption false once #156 was fixed.
     * [E2EFixture.clearCameraRoll] now also runs a `content delete` shell command under the
     * `shell` UID, which removes rows owned by *any* package, so this test's `clearCameraRoll()`
     * genuinely empties the roll before the tap. The empty-gallery assumption therefore holds
     * independently of `test3a`, and a failure here after #156 lands reflects the secure-camera
     * path itself, not a leftover MediaStore row.
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
                    "The gallery opened showing unexpected green content with an empty roll.",
            )
        }
    }

    // ── test5a — Secure camera + populated session: SecureViewer shows GREEN ─

    /**
     * Verifies the real locked-path product behaviour: tapping the overlay in secure-camera
     * mode (screen locked) when the in-progress secure session contains a GREEN photo opens
     * the app's own [com.gb4pc.viewer.SecureViewerActivity] and displays that photo.
     *
     * **Why the capture happens after the session starts (issue #486 — Option A).**
     *
     * A locked tap does NOT launch the configured gallery package; production
     * [com.gb4pc.overlay.TapActionResolver] resolves the locked case to
     * `TapAction.LaunchSecureViewer`, so the screen that opens is `SecureViewerActivity`, not
     * the mock gallery. `SecureViewerActivity` renders only the current secure session's media
     * ([com.gb4pc.viewer.SessionTracker.getSessionMedia]); it does not query MediaStore directly.
     *
     * The secure session is (re)started when the overlay activates while locked
     * ([com.gb4pc.service.OverlayServiceLogic.showOverlay] → `SessionTracker.startSession()`),
     * and `startSession()` clears any prior media. The session is then populated only by the
     * OverlayService `ContentObserver`, which adds MediaStore rows whose `DATE_ADDED` is at or
     * after session start. A photo captured *before* locking is therefore structurally excluded
     * from the session — which is why the earlier version of this test (capture-then-lock) could
     * never make the assertion pass and was filed as a red-light test against the mis-cited
     * issue #156 (a plan-tracking checklist, not a bug report).
     *
     * This version mirrors the genuine secure-camera use case: lock, launch the secure camera so
     * the overlay activates and the session begins, then capture the GREEN photo *inside* that
     * session. The ContentObserver adds it to the session, so the locked tap's `SecureViewer`
     * has a green item to show.
     *
     * **Why this asserts a letterboxed GREEN *band*, not full-screen 40% like test3a.**
     *
     * test3a opens the mock gallery, whose `LastPhotoActivity` ImageView uses `centerCrop`
     * (`e2e-mock-gallery/.../activity_last_photo.xml`), so the green photo fills ~100% of the
     * screen and full-screen coverage > 40% is reachable. The locked path here opens
     * `SecureViewerActivity`, which renders with `SubsamplingScaleImageView` and never calls
     * `setMinimumScaleType(...)`, so it uses the library default `SCALE_TYPE_CENTER_INSIDE`
     * (letterbox-fit, not crop). The capture is 1920×1080 (16:9 landscape;
     * `MockCameraActivity.CAPTURE_WIDTH/HEIGHT`); on the Pixel 6 CI device (1080×2400 portrait)
     * it fits to full width with a band ≈ 1080×608 px centred against the viewer's solid-black
     * background, i.e. ≈ 100% of the width but only ≈ 25% of the height (≈ 25% of the full
     * screen). A full-screen > 40% assertion is therefore structurally unreachable on this render
     * path regardless of overlay/session correctness.
     *
     * **Why the assertion must check the band *height*, not just its width and solidity.**
     *
     * The screen on view at the moment of the tap is *not* the lock screen or a black screen — it
     * is the solid-green `MockCameraActivity` (the secure camera, foregrounded above), which is the
     * same `Rgb.GREEN` as the captured photo. So the relevant failure case for this flow is a
     * *no-op tap*: if the overlay is not composited / not tappable over the secure-camera keyguard,
     * `tapOverlay()` silently does nothing, `SecureViewerActivity` never opens, and the green mock
     * camera stays on screen — full width *and* full height. Width-only + within-bbox-solidity
     * checks would both pass on that full-screen green, so the test would go green for a tap that
     * did nothing. The discriminator is the band *height*: the real SecureViewer render is
     * letterboxed to ≈ 25% of the screen height with black bars above and below, whereas the
     * leftover mock-camera green fills ≈ 100% of the height. We therefore additionally require the
     * green band to occupy only a minority of the screen height (a letterbox, not a fill) and
     * confirm the letterbox positively by checking that the strip above the band is essentially
     * green-free (SecureViewer's black background, not more mock-camera green).
     *
     * The three geometry-justified checks, all satisfied with margin by a correct letterboxed
     * render and failed by the no-op / empty cases:
     *  1. The green region spans most of the screen *width* (the letterbox fits to full width).
     *  2. Within the green region's own bounding box, coverage is high (the photo is solid green).
     *  3. The green region occupies only a minority of the screen *height* (it is letterboxed, not
     *     a full-screen fill), and the strip above it is green-free (the black letterbox bar).
     * An empty/black SecureViewer produces an empty mask, failing 1 and 2. A no-op tap leaving the
     * full-screen mock-camera green up fails 3 (full height, and no black bar above the band).
     */
    @Test
    fun test5a_secureCameraLockedPopulatedGalleryShowsGreen() {
        fixture.seedGalleryPrefs(MOCK_GALLERY_PACKAGE)
        fixture.clearCameraRoll()
        fixture.lockScreen()
        // Launch the secure camera so the overlay activates while locked. showOverlay() starts
        // the secure session and registers the MediaStore ContentObserver, so the capture below
        // lands inside the session. waitForOverlayActive() confirms the session has begun before
        // capturing — MockCameraActivity also only registers its shutter receiver in onResume(),
        // so it must be foregrounded first.
        fixture.launchSecureCamera()
        fixture.waitForOverlayActive()
        fixture.captureOnePhoto() // GREEN JPEG captured during the secure session

        val s1 = Screenshot.captureScreen()
        Screenshot.saveForArtifact(s1, "5a-s1.png")

        fixture.tapOverlay() // locked tap → SecureViewer renders the session's GREEN photo

        // Poll up to 15 s for the letterboxed GREEN band to appear — SecureViewer's cold start
        // (process spawn + SubsamplingScaleImageView decode) races a fixed pause. The poll requires
        // the same letterbox geometry as the assertion below (full width, height a minority of the
        // screen), so it does not short-circuit on the full-screen mock-camera green that is on
        // screen before the tap, and it predicts the assertion rather than measuring a different
        // quantity.
        fixture.waitForGreenBand(minWidthFraction = 0.80f, maxHeightFraction = 0.70f, timeoutMs = 15_000L)

        val s2 = Screenshot.captureScreen()
        Screenshot.saveForArtifact(s2, "5a-s2.png")

        val greenMask = ColorMatch.mask(s2, Rgb.GREEN)
        Screenshot.saveForArtifact(maskToBitmap(greenMask), "5a-green-mask.png")

        // The displayed photo is a solid-green 16:9 image letterboxed to full width by
        // SecureViewer's center-inside SubsamplingScaleImageView (see kdoc above).
        val bandWidthFraction = greenMask.bbox.width().toFloat() / greenMask.width
        val bandHeightFraction = greenMask.bbox.height().toFloat() / greenMask.height
        val bboxArea = greenMask.bbox.width() * greenMask.bbox.height()
        val withinBandCoverage = if (bboxArea > 0) greenMask.pixelCount.toFloat() / bboxArea else 0f

        // The black letterbox bar above the band positively confirms SecureViewer's background
        // rather than leftover full-screen mock-camera green. Measure the green coverage of the top
        // strip (the screen above where the ~25%-tall centred band starts). With the band centred,
        // its top edge sits at ~37% of the screen height, so the top 25% strip is entirely within
        // the upper letterbox bar and must be essentially green-free.
        val topStrip = android.graphics.Rect(0, 0, greenMask.width, (greenMask.height * 0.25f).toInt())
        val topStripGreen = ColorMatch.coverageFraction(greenMask, topStrip)

        if (bandWidthFraction <= 0.80f ||
            withinBandCoverage <= 0.80f ||
            bandHeightFraction >= 0.70f ||
            topStripGreen >= 0.10f
        ) {
            fail(
                "test5a_secureCameraLockedPopulatedGalleryShowsGreen: the locked tap should open " +
                    "SecureViewerActivity showing the GREEN photo captured during the secure " +
                    "session as a letterboxed band over a black background, but the displayed green " +
                    "region is not a solid, full-width, letterboxed band. " +
                    "Measured: band spans ${bandWidthFraction * 100f}% of screen width " +
                    "(expected > 80%), green coverage within its bounding box is " +
                    "${withinBandCoverage * 100f}% (expected > 80%), band height is " +
                    "${bandHeightFraction * 100f}% of the screen (expected < 70%, i.e. a letterbox " +
                    "not a full-screen fill), and the top strip is ${topStripGreen * 100f}% green " +
                    "(expected < 10%, i.e. a black letterbox bar, not leftover mock-camera green); " +
                    "green pixels=${greenMask.pixelCount}, bbox=${greenMask.bbox}, " +
                    "screen=${greenMask.width}x${greenMask.height}. " +
                    "A full-height green band with no black bar above it means the locked tap was a " +
                    "no-op and the secure camera is still in front (overlay not composited / not " +
                    "tappable over the keyguard). " +
                    "Check the 5a-s1/5a-s2 artifacts and the GB4PC_Overlay / Logic: logcat: the " +
                    "session may be empty (capture did not land in-session) or the overlay was " +
                    "not tappable.",
            )
        }
    }

    // ── Private helpers ───────────────────────────────────────────────────────

    /**
     * Returns the Euclidean distance between two [PointF]s.
     */
    private fun distance(
        a: PointF,
        b: PointF,
    ): Float {
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
