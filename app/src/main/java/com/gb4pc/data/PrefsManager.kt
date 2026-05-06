package com.gb4pc.data

import android.content.Context
import android.content.SharedPreferences
import com.gb4pc.Constants
import org.json.JSONObject

/**
 * Manages all app settings via SharedPreferences (DA-01).
 *
 * Overlay positions are serialized as JSON keyed by quantized aspect ratio (DA-02).
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

    var cameraDebounceMs: Long
        get() = prefs.getLong(Constants.PREF_CAMERA_DEBOUNCE_MS, Constants.CAMERA_DEBOUNCE_MS)
        set(value) = prefs.edit().putLong(Constants.PREF_CAMERA_DEBOUNCE_MS, value).apply()

    var focusableOverlay: Boolean
        get() = prefs.getBoolean(Constants.PREF_FOCUSABLE_OVERLAY, false)
        set(value) = prefs.edit().putBoolean(Constants.PREF_FOCUSABLE_OVERLAY, value).apply()

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
            .putString(Constants.PREF_OVERLAY_POSITIONS, positionsToJson(positions))
            .apply()
    }

    fun resetOverlayPosition(aspectRatio: String) {
        val positions = loadPositions().toMutableMap()
        positions.remove(aspectRatio)
        prefs.edit()
            .putString(Constants.PREF_OVERLAY_POSITIONS, positionsToJson(positions))
            .apply()
    }

    private fun loadPositions(): Map<String, OverlayPosition> {
        val json = prefs.getString(Constants.PREF_OVERLAY_POSITIONS, "") ?: ""
        return positionsFromJson(json)
    }
}

private fun positionsToJson(positions: Map<String, OverlayPosition>): String {
    val root = JSONObject()
    for ((ratio, pos) in positions) {
        val obj = JSONObject().apply {
            put("x", pos.xPercent.toDouble())
            put("y", pos.yPercent.toDouble())
            put("size", pos.sizePercent.toDouble())
        }
        root.put(ratio, obj)
    }
    return root.toString()
}

private fun positionsFromJson(json: String): Map<String, OverlayPosition> {
    if (json.isBlank()) return emptyMap()
    return try {
        val root = JSONObject(json)
        val result = mutableMapOf<String, OverlayPosition>()
        for (key in root.keys()) {
            val obj = root.getJSONObject(key)
            result[key] = OverlayPosition(
                xPercent = obj.getDouble("x").toFloat(),
                yPercent = obj.getDouble("y").toFloat(),
                sizePercent = obj.getDouble("size").toFloat()
            )
        }
        result
    } catch (e: Exception) {
        emptyMap()
    }
}
