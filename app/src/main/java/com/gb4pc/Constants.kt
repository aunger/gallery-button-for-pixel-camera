package com.gb4pc

object Constants {
    const val PIXEL_CAMERA_PACKAGE = "com.google.android.GoogleCamera"

    // Default overlay position (PS-02)
    const val DEFAULT_X_PERCENT = 13.0f
    const val DEFAULT_Y_PERCENT = 91.5f
    const val DEFAULT_SIZE_PERCENT = 11.5f

    // Overlay size bounds
    const val MIN_SIZE_PERCENT = 1.0f
    const val MAX_SIZE_PERCENT = 30.0f

    // Camera debounce delay (DT-04)
    const val CAMERA_DEBOUNCE_MS = 50L
    const val MIN_CAMERA_DEBOUNCE_MS = 10L
    const val MAX_CAMERA_DEBOUNCE_MS = 1000L

    // UsageStats query window (DT-02)
    const val USAGE_STATS_WINDOW_MS = 5000L

    // Retry delay when UsageStats hasn't caught up with the foreground app yet (DT-06a)
    const val ACTIVATION_RETRY_MS = 1000L

    // Debug log buffer size (UI-10)
    const val DEBUG_LOG_BUFFER_SIZE = 200

    // Notification
    const val NOTIFICATION_CHANNEL_ID = "gb4pc_service"
    const val NOTIFICATION_ID = 1
    const val NOTIFICATION_PERMISSION_ID = 2

    // SharedPreferences
    const val PREFS_NAME = "gb4pc_prefs"
    const val PREF_SERVICE_ENABLED = "service_enabled"
    const val PREF_GALLERY_PACKAGE = "gallery_package"
    const val PREF_OVERLAY_POSITIONS = "overlay_positions"
    const val PREF_SETUP_COMPLETED = "setup_completed"
    const val PREF_CAMERA_DEBOUNCE_MS = "camera_debounce_ms"

    // Secure viewer
    const val SESSION_TIMESTAMP_TOLERANCE_MS = 2000L
    const val MEDIA_RELATIVE_PATH_PREFIX = "DCIM/Camera/"

    // Snackbar undo timeout
    const val UNDO_TIMEOUT_MS = 5000L
}
