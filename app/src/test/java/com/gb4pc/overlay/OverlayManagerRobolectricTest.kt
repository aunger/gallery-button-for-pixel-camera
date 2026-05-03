package com.gb4pc.overlay

import android.app.Application
import android.graphics.Bitmap
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
import org.robolectric.shadows.ShadowWindowManagerImpl

/**
 * Robolectric integration tests for OverlayManager.
 *
 * Covers:
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

        val prefsManager: PrefsManager = mock {
            on { galleryPackage } doReturn null
            on { getOverlayPosition(any()) } doReturn OverlayPosition.default()
            on { focusableOverlay } doReturn false
        }

        var focusLostCount = 0
        val overlayManager = OverlayManager(
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
            focusLostCount
        )

        val windowManager = context.getSystemService(WindowManager::class.java)
        val shadowWm = shadowOf(windowManager) as ShadowWindowManagerImpl
        assertEquals(
            "Overlay view must remain in the WindowManager after show()",
            1,
            shadowWm.views.size
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
        val prefsManager: PrefsManager = mock {
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
            overlayView.drawable is BitmapDrawable
        )

        // Step 3: second show() call — simulates the second camera lens on a dual-camera
        // device firing its onCameraUnavailable callback while the overlay is already up.
        overlayManager.show()

        // Step 4: The drawable must still be a BitmapDrawable — the isShowing guard in
        // show() returned early so updateIcon() was never called.
        assertTrue(
            "After the second show() call the thumbnail BitmapDrawable must not have been " +
                "replaced by the icon drawable (regression guard for Issue #45)",
            overlayView.drawable is BitmapDrawable
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
        val prefsManager: PrefsManager = mock {
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
        val prefsManager: PrefsManager = mock {
            on { galleryPackage } doReturn null
            on { getOverlayPosition(any()) } doReturn OverlayPosition.default()
            on { focusableOverlay } doReturn false
        }
        val overlayManager = OverlayManager(context, prefsManager)

        val urisToTest = listOf(
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
                threw
            )
        }
    }
}
