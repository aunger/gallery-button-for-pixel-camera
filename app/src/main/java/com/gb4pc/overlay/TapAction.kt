package com.gb4pc.overlay

/**
 * Represents the action to take when the overlay is tapped (AC-01 through AC-04).
 */
sealed class TapAction {
    /** AC-01: Device unlocked, gallery configured and installed — launch gallery */
    data class LaunchGallery(val packageName: String) : TapAction()
    /** AC-02: Device locked, gallery configured and installed — open secure viewer */
    object LaunchSecureViewer : TapAction()
    /** AC-03: No gallery configured, device unlocked — open picker */
    object LaunchPicker : TapAction()
    /** AC-03: No gallery configured, device locked — show toast */
    object ShowUnlockToSetupToast : TapAction()
    /** AC-04: Gallery uninstalled, device unlocked — open picker */
    object LaunchPickerGalleryMissing : TapAction()
    /** AC-04: Gallery uninstalled, device locked — show toast */
    object ShowGalleryNotFoundToast : TapAction()
}

/**
 * Pure function that determines what action to take on overlay tap.
 * Extracted for testability.
 */
object TapActionResolver {

    fun resolve(
        isLocked: Boolean,
        galleryPackage: String?,
        isGalleryInstalled: Boolean
    ): TapAction {
        return when {
            // AC-03: No gallery app configured
            galleryPackage == null -> {
                if (isLocked) TapAction.ShowUnlockToSetupToast
                else TapAction.LaunchPicker
            }
            // AC-04: Gallery app uninstalled
            !isGalleryInstalled -> {
                if (isLocked) TapAction.ShowGalleryNotFoundToast
                else TapAction.LaunchPickerGalleryMissing
            }
            // AC-02: Device locked — open secure viewer
            isLocked -> TapAction.LaunchSecureViewer
            // AC-01: Device unlocked — launch gallery app
            else -> TapAction.LaunchGallery(galleryPackage)
        }
    }
}
