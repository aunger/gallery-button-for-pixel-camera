package com.gb4pc

object Constants {
    const val PIXEL_CAMERA_PACKAGE = "com.google.android.GoogleCamera"

    // Default overlay position (PS-02)
    const val DEFAULT_X_PERCENT = 20.0f
    const val DEFAULT_Y_PERCENT = 75.0f
    const val DEFAULT_SIZE_PERCENT = 15.0f

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

    // Maximum number of activation retries per camera-open event (DT-06a). UsageStats can lag the
    // camera callback by more than a single ACTIVATION_RETRY_MS interval, especially on slow
    // emulators, so the retry re-schedules itself up to this many times (a total retry budget of
    // ACTIVATION_RETRY_MS x ACTIVATION_RETRY_MAX_ATTEMPTS) instead of giving up after one attempt.
    const val ACTIVATION_RETRY_MAX_ATTEMPTS = 5

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
    const val PREF_FOCUSABLE_OVERLAY = "focusable_overlay"

    // Secure viewer
    const val SESSION_TIMESTAMP_TOLERANCE_MS = 2000L
    const val MEDIA_RELATIVE_PATH_PREFIX = "DCIM/Camera/"

    // Retry delay when ContentObserver fires before MediaStore commits the new item (IS_PENDING race)
    const val MEDIA_OBSERVER_RETRY_MS = 500L

    // Maximum number of MediaStore-commit retries per ContentObserver onChange event. The
    // IS_PENDING -> 0 transition can take longer than a single MEDIA_OBSERVER_RETRY_MS interval
    // (or never fire while locked), so the retry re-schedules itself up to this many times instead
    // of giving up after one attempt. Total retry budget = MEDIA_OBSERVER_RETRY_MS x this value.
    const val MEDIA_OBSERVER_RETRY_MAX_ATTEMPTS = 5

    // Snackbar undo timeout
    const val UNDO_TIMEOUT_MS = 5000L

    // Overlay button shape (Issue #39)
    // Corner radius as a fraction of the button's width, producing a squircle shape
    // (30% matches Pixel Camera's rounded-square style).
    const val SQUIRCLE_CORNER_RADIUS_FRACTION = 0.30f
}
