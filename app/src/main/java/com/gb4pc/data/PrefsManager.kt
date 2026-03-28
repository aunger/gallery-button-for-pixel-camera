package com.gb4pc.data

import android.content.Context
import android.content.SharedPreferences
import com.gb4pc.Constants

/**
 * Manages all app settings via SharedPreferences (DA-01).
 */
class PrefsManager(context: Context) {

    private val prefs: SharedPreferences =
        context.getSharedPreferences(Constants.PREFS_NAME, Context.MODE_PRIVATE)

    var isServiceEnabled: Boolean
        get() = prefs.getBoolean(Constants.PREF_SERVICE_ENABLED, false)
        set(value) = prefs.edit().putBoolean(Constants.PREF_SERVICE_ENABLED, value).apply()

    var galleryPackage: String?
        get() = prefs.getString(Constants.PREF_GALLERY_PACKAGE, null)
        set(value) = prefs.edit().putString(Constants.PREF_GALLERY_PACKAGE, value).apply()

    var isSetupCompleted: Boolean
        get() = prefs.getBoolean(Constants.PREF_SETUP_COMPLETED, false)
        set(value) = prefs.edit().putBoolean(Constants.PREF_SETUP_COMPLETED, value).apply()

    /**
     * Returns the overlay position for the given aspect ratio.
     * Falls back to the closest stored ratio (PS-04), then to defaults.
     */
    fun getOverlayPosition(aspectRatio: String): OverlayPosition {
        val positions = loadPositions()
        // Exact match
        positions[aspectRatio]?.let { return it }
        // Closest ratio fallback (PS-04)
        val closest = AspectRatioUtil.findClosestRatio(aspectRatio, positions.keys)
        if (closest != null) {
            positions[closest]?.let { return it }
        }
        return OverlayPosition.default()
    }

    fun saveOverlayPosition(aspectRatio: String, position: OverlayPosition) {
        val positions = loadPositions().toMutableMap()
        positions[aspectRatio] = position
        prefs.edit()
            .putString(Constants.PREF_OVERLAY_POSITIONS, OverlayPositionStore.toJson(positions))
            .apply()
    }

    fun resetOverlayPosition(aspectRatio: String) {
        val positions = loadPositions().toMutableMap()
        positions.remove(aspectRatio)
        prefs.edit()
            .putString(Constants.PREF_OVERLAY_POSITIONS, OverlayPositionStore.toJson(positions))
            .apply()
    }

    private fun loadPositions(): Map<String, OverlayPosition> {
        val json = prefs.getString(Constants.PREF_OVERLAY_POSITIONS, "") ?: ""
        return OverlayPositionStore.fromJson(json)
    }
}
