package com.gb4pc.overlay

import android.app.Application
import android.graphics.Bitmap
import android.graphics.drawable.AdaptiveIconDrawable
import android.graphics.drawable.BitmapDrawable
import android.view.WindowManager
import android.widget.ImageView
import androidx.test.core.app.ApplicationProvider
import com.gb4pc.data.OverlayPosition
import com.gb4pc.data.PrefsManager
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.mockito.kotlin.any
import org.mockito.kotlin.doReturn
import org.mockito.kotlin.mock
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.shadows.ShadowLooper
import org.robolectric.shadows.ShadowWindowManagerImpl

/**
 * Robolectric integration tests for OverlayManager.
 *
 * Covers:
 * - Issue #39 / #188: overlay view must use squircle clipping for both the gallery icon state
 *   and the photo-thumbnail state. Issue #188 replaces the clipToOutline / outline-provider
 *   approach with a [SquircleDrawable] wrapper so the squircle shape is baked into the drawable,
 *   independent of the device launcher's adaptive-icon mask.
 * - Issue #45: second show() must not overwrite a thumbnail with the icon.
 * - Issue #66: overlay must remain visible after show() when focusableOverlay is false
 *   (FLAG_NOT_FOCUSABLE windows never receive focus, so onWindowFocusChanged(false) fires
 *   immediately — the focus callbacks must be suppressed in that mode).
 * - Issue #81: loadThumbnailBitmap must return null (not throw) when the URI is invalid
 *   and must fall back gracefully when loadThumbnail fails.
 */
@RunWith(RobolectricTestRunner::class)
class OverlayManagerRobolectricTest {
    /**
     * Regression guard for Issue #66: when focusableOverlay is false (the default),
     * onFocusLost must not be called when the overlay is shown.
     *
     * With FLAG_NOT_FOCUSABLE the window never receives input focus, so Android fires
     * onWindowFocusChanged(false) immediately after the view is attached. Before the fix,
     * this fired the onFocusLost callback unconditionally, which hid the overlay the instant
     * it was shown — causing the overlay to never appear in v0.0.6.
     */
    @Test
    fun `show with non-focusable overlay does not trigger onFocusLost`() {
        val context: Application = ApplicationProvider.getApplicationContext()

        val prefsManager: PrefsManager =
            mock {
                on { galleryPackage } doReturn null
                on { getOverlayPosition(any()) } doReturn OverlayPosition.default()
                on { focusableOverlay } doReturn false
            }

        var focusLostCount = 0
        val overlayManager =
            OverlayManager(
                context = context,
                prefsManager = prefsManager,
                onFocusLost = { focusLostCount++ },
                onFocusGained = {},
            )

        overlayManager.show()

        // onFocusLost must not have been called — the overlay should stay visible.
        assertEquals(
            "onFocusLost must not fire when focusableOverlay is false (Issue #66 regression)",
            0,
            focusLostCount,
        )

        val windowManager = context.getSystemService(WindowManager::class.java)
        val shadowWm = shadowOf(windowManager) as ShadowWindowManagerImpl
        assertEquals(
            "Overlay view must remain in the WindowManager after show()",
            1,
            shadowWm.views.size,
        )
    }

    /**
     * Three-step regression guard for the dual-camera thumbnail-overwrite bug:
     *
     *   1. show()                          → overlay added; ImageView holds icon drawable
     *   2. overlayView.setImageBitmap(bmp) → simulates showLatestPhotoThumbnail() success
     *   3. show() again                    → must be a no-op (isShowing guard)
     *   4. Assert drawable is still BitmapDrawable → updateIcon() was NOT called
     */
    @Test
    fun `second show call does not overwrite thumbnail bitmap with icon`() {
        val context: Application = ApplicationProvider.getApplicationContext()

        // Mock PrefsManager: galleryPackage = null → getGalleryIcon returns the plain
        // placeholder drawable (a VectorDrawable / LayerDrawable, NOT a BitmapDrawable),
        // so any BitmapDrawable on the view must have come from setImageBitmap().
        val prefsManager: PrefsManager =
            mock {
                on { galleryPackage } doReturn null
                on { getOverlayPosition(any()) } doReturn OverlayPosition.default()
            }

        val overlayManager = OverlayManager(context, prefsManager)

        // Step 1: first show() — overlay view is added to the WindowManager.
        overlayManager.show()

        // Retrieve the view from ShadowWindowManagerImpl (Robolectric shadow).
        val windowManager = context.getSystemService(WindowManager::class.java)
        val shadowWm = shadowOf(windowManager) as ShadowWindowManagerImpl
        val views = shadowWm.views
        assertEquals("Expected exactly one view added to WindowManager after show()", 1, views.size)
        val overlayView = views[0] as ImageView

        // Step 2: simulate showLatestPhotoThumbnail() successfully loading a bitmap.
        val testBitmap = Bitmap.createBitmap(10, 10, Bitmap.Config.ARGB_8888)
        overlayView.setImageBitmap(testBitmap)
        assertTrue(
            "Sanity: after setImageBitmap the drawable must be a BitmapDrawable",
            overlayView.drawable is BitmapDrawable,
        )

        // Step 3: second show() call — simulates the second camera lens on a dual-camera
        // device firing its onCameraUnavailable callback while the overlay is already up.
        overlayManager.show()

        // Step 4: The drawable must still be a BitmapDrawable — the isShowing guard in
        // show() returned early so updateIcon() was never called.
        assertTrue(
            "After the second show() call the thumbnail BitmapDrawable must not have been " +
                "replaced by the icon drawable (regression guard for Issue #45)",
            overlayView.drawable is BitmapDrawable,
        )
    }

    // ── Issues #39 and #188: squircle button shape ────────────────────────────

    /**
     * The overlay ImageView drawable must be a [SquircleDrawable] when showing the gallery icon.
     *
     * Issue #188: the squircle shape is baked into the drawable so it is applied at draw time,
     * independent of the device launcher's adaptive-icon mask. On the [google_apis] API-35
     * emulator the launcher clips adaptive icons to a circle — wrapping in [SquircleDrawable]
     * draws the icon's layers directly, bypassing that mask.
     */
    @Test
    fun `overlay view shows gallery icon wrapped in SquircleDrawable`() {
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
        val overlayView = shadowWm.views[0] as ImageView

        assertTrue(
            "Issue #188: overlay drawable must be a SquircleDrawable so the squircle shape " +
                "is independent of the device launcher mask.",
            overlayView.drawable is SquircleDrawable,
        )
    }

    /**
     * After switching to a photo thumbnail via showLatestPhotoThumbnail(), the overlay must
     * still use a [SquircleDrawable] — the thumbnail is shaped just like the icon.
     *
     * Issue #188: squircle shape is baked into the drawable, so it persists across drawable
     * changes without relying on clipToOutline or outlineProvider.
     */
    @Test
    fun `overlay view shows thumbnail wrapped in SquircleDrawable`() {
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
        val overlayView = shadowWm.views[0] as ImageView

        // Exercise the public API to trigger a thumbnail load attempt.
        overlayManager.showLatestPhotoThumbnail("content://com.gb4pc.test/images/1")
        // Drain the main-looper queue so any Handler.post() from the background thread runs.
        ShadowLooper.idleMainLooper()

        // After showLatestPhotoThumbnail the drawable must still be a SquircleDrawable
        // (wrapping a BitmapDrawable), not a raw BitmapDrawable.
        assertTrue(
            "Issue #188: after showLatestPhotoThumbnail, drawable must still be a SquircleDrawable",
            overlayView.drawable is SquircleDrawable,
        )
    }

    /**
     * Issue #188 regression guard: when the gallery package has an AdaptiveIconDrawable, the
     * overlay must use a [SquircleDrawable] wrapping the raw [AdaptiveIconDrawable], so the
     * squircle shape is applied at draw time regardless of the launcher's icon mask.
     *
     * On [google_apis] API-35 the launcher mask is a circle; the [SquircleDrawable] draws the
     * background and foreground layers directly, bypassing that mask.
     */
    @Test
    fun `gallery icon is wrapped in SquircleDrawable containing AdaptiveIconDrawable`() {
        val context: Application = ApplicationProvider.getApplicationContext()

        // Use the app's own package as the "gallery" package under test. Robolectric fully
        // initialises the host application's resources, so getResourcesForApplication() and
        // the mipmap/ic_launcher adaptive icon (API 26+, AdaptiveIconDrawable) are available.
        val selfPackage = context.packageName // "com.gb4pc"

        val prefsManager: PrefsManager =
            mock {
                on { galleryPackage } doReturn selfPackage
                on { getOverlayPosition(any()) } doReturn OverlayPosition.default()
                on { focusableOverlay } doReturn false
            }

        val overlayManager = OverlayManager(context, prefsManager)
        overlayManager.show()

        val windowManager = context.getSystemService(WindowManager::class.java)
        val shadowWm = shadowOf(windowManager) as ShadowWindowManagerImpl
        val overlayView = shadowWm.views[0] as ImageView

        // The drawable must be a SquircleDrawable (outer squircle clip is guaranteed regardless
        // of the launcher mask), wrapping a raw AdaptiveIconDrawable (not a pre-masked bitmap).
        assertTrue(
            "Issue #188: gallery icon must be wrapped in a SquircleDrawable.",
            overlayView.drawable is SquircleDrawable,
        )
        val squircle = overlayView.drawable as SquircleDrawable
        assertFalse(
            "Issue #188: the inner drawable must NOT be a pre-masked BitmapDrawable.",
            squircle.inner is BitmapDrawable,
        )
        assertTrue(
            "Issue #188: the inner drawable must be a raw AdaptiveIconDrawable (no launcher mask).",
            squircle.inner is AdaptiveIconDrawable,
        )
    }

    // ── Issue #229: overlay positioned relative to the true screen origin ───

    /**
     * Regression guard for Issue #229: the overlay window's [WindowManager.LayoutParams] must
     * include `FLAG_LAYOUT_IN_SCREEN` so that `Gravity.TOP or Gravity.START` with `x`/`y` set by
     * [calculateOverlayXPx] / [calculateOverlayYPx] positions the overlay relative to the
     * physical-screen origin (0, 0), not below the status bar.
     *
     * Without this flag, the E2E visual test observed the overlay rendered ~128 px lower
     * than its configured `yPercent` (BLUE centroid at y=1784 instead of the expected
     * y=1656 for yPercent=69% on a 2400 px-tall display).
     */
    @Test
    fun `overlay window uses FLAG_LAYOUT_IN_SCREEN so position is relative to screen origin`() {
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
        val overlayView = shadowWm.views[0]
        val params = overlayView.layoutParams as WindowManager.LayoutParams

        assertTrue(
            "Overlay window must set FLAG_LAYOUT_IN_SCREEN so x/y are relative to the " +
                "physical-screen origin, not below the status bar (Issue #229).",
            (params.flags and WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN) != 0,
        )
    }

    // ── Issue #556: FLAG_LAYOUT_NO_LIMITS is not the non-focusable window's problem ──

    /**
     * Regression guard for Issue #556 (`test1a_overlayShowsBlueAtConfiguredPosition`): the
     * small, non-focusable overlay window must NOT set `FLAG_LAYOUT_NO_LIMITS`.
     *
     * Issue #556 originally suspected that PR #398 (commit ce20d71) dropping this flag here
     * caused test1a's `pixelCount=0` regression, and an earlier round of the #556 fix restored
     * it on that theory (also inverting this test's assertion to match). Direct CI evidence
     * (see PR #557) disproved the theory: with the flag restored, test1a still failed with the
     * exact same failure signature as without it. The real bug was a screenshot-timing race in
     * the E2E test harness, unrelated to this flag (fixed via
     * `E2EFixture.captureScreenUntilColorVisible`). This test is restored to its original
     * assertion, since no evidence supports setting this flag on the non-focusable branch.
     */
    @Test
    fun `non-focusable overlay window does not set FLAG_LAYOUT_NO_LIMITS`() {
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
        val overlayView = shadowWm.views[0]
        val params = overlayView.layoutParams as WindowManager.LayoutParams

        assertTrue(
            "Non-focusable overlay window must NOT set FLAG_LAYOUT_NO_LIMITS (Issue #556: " +
                "restoring it did not fix test1a in CI, and no evidence supports setting it).",
            (params.flags and WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS) == 0,
        )
    }

    // ── Issue #230 / #397: tap on the overlay reaches its clickable ImageView ─

    /**
     * Regression guard for Issue #230 / #397
     * (`test2a_emptyGalleryNoGreenAfterTap`): the small, non-focusable overlay window must set
     * `FLAG_NOT_TOUCH_MODAL` so an in-bounds tap reaches its clickable [android.widget.ImageView]
     * and fires `handleTap()`, while touches outside the icon's bounds still pass through to the
     * camera app behind it.
     *
     * Without this flag the E2E suite observed the tap as a no-op: the green camera feed stayed
     * full-screen and the gallery never opened (GREEN coverage ~87% after tap instead of < 10%).
     */
    @Test
    fun `overlay window sets FLAG_NOT_TOUCH_MODAL so in-bounds taps reach the icon`() {
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
        val overlayView = shadowWm.views[0]
        val params = overlayView.layoutParams as WindowManager.LayoutParams

        assertTrue(
            "Overlay window must set FLAG_NOT_TOUCH_MODAL so an in-bounds tap reaches the " +
                "clickable ImageView (firing handleTap()) while out-of-bounds touches pass " +
                "through to the camera app (Issue #230 / #397).",
            (params.flags and WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL) != 0,
        )
    }

    // ── Issue #81: loadThumbnailBitmap fallback ──────────────────────────────

    /**
     * When the URI is inaccessible (URI not accessible — as can happen on a locked device
     * or for a pending media item), loadThumbnailBitmap must complete without throwing.
     * The real assertion here is that no exception propagates out of the method.
     */
    @Test
    fun `loadThumbnailBitmap completes without throwing for an inaccessible URI`() {
        val context: Application = ApplicationProvider.getApplicationContext()
        val prefsManager: PrefsManager =
            mock {
                on { galleryPackage } doReturn null
                on { getOverlayPosition(any()) } doReturn OverlayPosition.default()
                on { focusableOverlay } doReturn false
            }
        val overlayManager = OverlayManager(context, prefsManager)

        // A URI with an unregistered authority — this exercises the fallback exception-
        // handling path so we know an inaccessible URI never crashes the overlay service.
        val unregisteredUri = android.net.Uri.parse("content://com.gb4pc.unregistered.authority/images/999")

        // If loadThumbnailBitmap throws, the test fails automatically. No explicit assert needed.
        overlayManager.loadThumbnailBitmap(unregisteredUri)
    }

    /**
     * Verifies that loadThumbnailBitmap's fallback path (openInputStream) is reachable
     * and returns null gracefully when the stream is empty/invalid.
     *
     * This guards against a regression where an exception from the pre-Q path
     * propagates out of loadThumbnailBitmap and crashes the service.
     */
    @Test
    fun `loadThumbnailBitmap does not throw for any URI`() {
        val context: Application = ApplicationProvider.getApplicationContext()
        val prefsManager: PrefsManager =
            mock {
                on { galleryPackage } doReturn null
                on { getOverlayPosition(any()) } doReturn OverlayPosition.default()
                on { focusableOverlay } doReturn false
            }
        val overlayManager = OverlayManager(context, prefsManager)

        val urisToTest =
            listOf(
                "content://media/external/images/media/99999",
                "content://com.gb4pc.unregistered/images/1",
                "content://invalid",
            )

        for (uriString in urisToTest) {
            val uri = android.net.Uri.parse(uriString)
            var threw = false
            try {
                overlayManager.loadThumbnailBitmap(uri)
            } catch (e: Exception) {
                threw = true
            }
            assertFalse(
                "loadThumbnailBitmap must not throw for URI: $uriString",
                threw,
            )
        }
    }
}
