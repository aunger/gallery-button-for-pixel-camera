package com.gb4pc.overlay

import org.junit.Assert.*
import org.junit.Test

class TapActionResolverTest {

    // --- AC-01: Unlocked, gallery configured and installed ---

    @Test
    fun `AC-01 unlocked with installed gallery launches gallery app`() {
        val action = TapActionResolver.resolve(
            isLocked = false,
            galleryPackage = "com.example.gallery",
            isGalleryInstalled = true
        )
        assertTrue(action is TapAction.LaunchGallery)
        assertEquals("com.example.gallery", (action as TapAction.LaunchGallery).packageName)
    }

    // --- AC-02: Locked, gallery configured and installed ---

    @Test
    fun `AC-02 locked with installed gallery launches secure viewer`() {
        val action = TapActionResolver.resolve(
            isLocked = true,
            galleryPackage = "com.example.gallery",
            isGalleryInstalled = true
        )
        assertEquals(TapAction.LaunchSecureViewer, action)
    }

    // --- AC-03: No gallery configured ---

    @Test
    fun `AC-03 unlocked with no gallery configured launches picker`() {
        val action = TapActionResolver.resolve(
            isLocked = false,
            galleryPackage = null,
            isGalleryInstalled = false
        )
        assertEquals(TapAction.LaunchPicker, action)
    }

    @Test
    fun `AC-03 locked with no gallery configured shows unlock toast`() {
        val action = TapActionResolver.resolve(
            isLocked = true,
            galleryPackage = null,
            isGalleryInstalled = false
        )
        assertEquals(TapAction.ShowUnlockToSetupToast, action)
    }

    // --- AC-04: Gallery uninstalled ---

    @Test
    fun `AC-04 unlocked with uninstalled gallery launches picker`() {
        val action = TapActionResolver.resolve(
            isLocked = false,
            galleryPackage = "com.example.gallery",
            isGalleryInstalled = false
        )
        assertEquals(TapAction.LaunchPickerGalleryMissing, action)
    }

    @Test
    fun `AC-04 locked with uninstalled gallery shows not found toast`() {
        val action = TapActionResolver.resolve(
            isLocked = true,
            galleryPackage = "com.example.gallery",
            isGalleryInstalled = false
        )
        assertEquals(TapAction.ShowGalleryNotFoundToast, action)
    }
}
