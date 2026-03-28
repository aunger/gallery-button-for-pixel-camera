package com.gb4pc.viewer

import com.gb4pc.Constants

/**
 * Tracks the current secure camera session and its media items (§5.1).
 * All data is in-memory only (SF-05).
 */
class SessionTracker {

    var isSessionActive: Boolean = false
        private set

    var sessionStartTimestamp: Long = 0L
        private set

    private val mediaItems = mutableListOf<MediaItem>()

    /**
     * SF-01: Begin a new session, recording the start timestamp (SF-02).
     */
    fun startSession() {
        isSessionActive = true
        sessionStartTimestamp = System.currentTimeMillis()
        mediaItems.clear()
    }

    /**
     * SF-01: End the session, clearing all media (SF-05).
     */
    fun endSession() {
        isSessionActive = false
        mediaItems.clear()
    }

    fun addMedia(item: MediaItem) {
        if (!isSessionActive) return
        mediaItems.add(item)
    }

    fun removeMedia(uri: String) {
        mediaItems.removeAll { it.uri == uri }
    }

    /**
     * SF-07: Returns session media sorted most recent first.
     */
    fun getSessionMedia(): List<MediaItem> {
        return mediaItems.sortedByDescending { it.dateTaken }
    }

    /**
     * SF-04: Check if a media item belongs to the current session.
     */
    fun isMediaInSession(dateTaken: Long, relativePath: String): Boolean {
        if (!isSessionActive) return false

        val threshold = sessionStartTimestamp - Constants.SESSION_TIMESTAMP_TOLERANCE_MS
        if (dateTaken < threshold) return false

        if (!relativePath.startsWith(Constants.MEDIA_RELATIVE_PATH_PREFIX)) return false

        return true
    }

    companion object {
        // Singleton for service-wide access
        val instance = SessionTracker()
    }
}
