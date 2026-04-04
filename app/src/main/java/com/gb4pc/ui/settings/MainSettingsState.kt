package com.gb4pc.ui.settings

import com.gb4pc.data.PrefsManager

/**
 * Represents the UI state for the main settings screen.
 */
data class MainSettingsState(
    val isServiceEnabled: Boolean,
    val galleryPackage: String?,
    val isSetupCompleted: Boolean
) {
    companion object {
        fun from(prefs: PrefsManager): MainSettingsState {
            return MainSettingsState(
                isServiceEnabled = prefs.isServiceEnabled,
                galleryPackage = prefs.galleryPackage,
                isSetupCompleted = prefs.isSetupCompleted
            )
        }
    }
}
