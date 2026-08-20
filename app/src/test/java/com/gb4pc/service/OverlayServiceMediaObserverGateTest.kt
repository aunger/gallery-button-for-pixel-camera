package com.gb4pc.service

import android.Manifest
import android.app.Application
import android.provider.MediaStore
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf

/**
 * Issue #565: `OverlayService.registerMediaObserver()` must be gated on the media read
 * permission, exactly as `registerThumbnailObserver()` already is (issue #564).
 *
 * Without full media read access every `MediaStore` query returns only rows owned by this app
 * (scoped storage, API 29+), never Pixel Camera's, so the observer backing the secure filmstrip
 * can only ever deliver an empty result set no matter how many times it fires. Registering it
 * anyway buys nothing and costs a query per shutter press during a locked-device session.
 *
 * These tests assert the registration itself rather than a log line, so they stay meaningful if
 * the wording changes. Robolectric is needed for two reasons the plain-JVM tests in this package
 * cannot supply: a real `Build.VERSION.SDK_INT` (`PermissionHelper.hasMediaPermission` picks
 * `READ_MEDIA_IMAGES` vs. `READ_EXTERNAL_STORAGE` from it; see `PermissionHelperTest`'s own note
 * that SDK_INT is 0 there), and a `ContentResolver` that records its registered observers.
 *
 * This is the JVM-level coverage issue #565 asked for. The one instrumented test touching this
 * surface, `GalleryButtonVisualE2ETest#test5a_secureCameraLockedPopulatedGalleryShowsGreen`, is
 * quarantined in `.github/allowed-test-failures.txt` under issue #243, so an E2E test here would
 * land next to a red neighbour.
 */
@RunWith(RobolectricTestRunner::class)
class OverlayServiceMediaObserverGateTest {
    private val app: Application = ApplicationProvider.getApplicationContext()

    /** Creates the service through its real `onCreate`, as the framework would. */
    private fun createService(): OverlayService = Robolectric.buildService(OverlayService::class.java).create().get()

    /**
     * Invokes the private `registerMediaObserver()`. Reflection rather than a widened visibility:
     * the production seam here is the observer registration the method performs, which the tests
     * assert directly, so there is no reason to expose the method itself. A rename fails loudly.
     */
    private fun OverlayService.registerMediaObserver() {
        OverlayService::class.java
            .getDeclaredMethod("registerMediaObserver")
            .apply {
                isAccessible = true
            }.invoke(this)
    }

    /** Observers registered on the image and video collections `registerMediaObserver` watches. */
    private fun registeredObserverCount(): Int {
        val resolver = shadowOf(app.contentResolver)
        return resolver.getContentObservers(MediaStore.Images.Media.EXTERNAL_CONTENT_URI).size +
            resolver.getContentObservers(MediaStore.Video.Media.EXTERNAL_CONTENT_URI).size
    }

    private fun grantMediaPermission() {
        shadowOf(app).grantPermissions(Manifest.permission.READ_MEDIA_IMAGES)
    }

    @Test
    fun `registerMediaObserver registers on both collections when the media permission is granted`() {
        grantMediaPermission()

        val service = createService()
        assertEquals("No observer should be registered before session start", 0, registeredObserverCount())

        service.registerMediaObserver()

        assertEquals(
            "With the media permission granted the session observer must watch both the image " +
                "and video collections (SF-03)",
            2,
            registeredObserverCount(),
        )
    }

    @Test
    fun `registerMediaObserver registers nothing when the media permission is absent`() {
        // Deliberately no grant: this is the state a user reaches by skipping the setup flow's
        // media step, or by Android revoking the permission later (issue #563).
        val service = createService()

        service.registerMediaObserver()

        assertEquals(
            "Without the media permission the session query can only ever return this app's own " +
                "rows, so no observer should be registered (issue #565)",
            0,
            registeredObserverCount(),
        )
    }

    /**
     * The gate must return early without recording an observer, so a later session (after the
     * user acts on the tap-to-fix notification and grants the permission) still registers one.
     * A gate that set the `mediaObserver` field before checking would wedge the service for its
     * whole lifetime.
     */
    @Test
    fun `registerMediaObserver still registers on a later session once the permission is granted`() {
        val service = createService()
        service.registerMediaObserver()
        assertEquals(0, registeredObserverCount())

        grantMediaPermission()
        service.registerMediaObserver()

        assertTrue(
            "A denied registration must not latch; granting the permission and starting another " +
                "session must register the observer",
            registeredObserverCount() > 0,
        )
    }
}
