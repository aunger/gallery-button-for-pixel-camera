package com.gb4pc.viewer

import com.gb4pc.Constants

/**
 * Tracks the current secure camera session and its media items (§5.1).
 * All data is in-memory only (SF-05).
 * Thread-safe: accessed from OverlayService, SecureViewerActivity, and ContentObserver callbacks.
 */
class SessionTracker {

    private val lock = Any()

    var isSessionActive: Boolean = false
        private set

    var sessionStartTimestamp: Long = 0L
        private set

    private val mediaItems = mutableListOf<MediaItem>()

    /**
     * SF-01: Begin a new session, recording the start timestamp (SF-02).
     */
    fun startSession() {
        synchronized(lock) {
            isSessionActive = true
            sessionStartTimestamp = System.currentTimeMillis()
            mediaItems.clear()
        }
    }

    /**
     * SF-01: End the session, clearing all media (SF-05).
     */
    fun endSession() {
        synchronized(lock) {
            isSessionActive = false
            mediaItems.clear()
        }
    }

    fun addMedia(item: MediaItem) {
        synchronized(lock) {
            if (!isSessionActive) return
            if (mediaItems.none { it.uri == item.uri }) {
                mediaItems.add(item)
            }
        }
    }

    fun removeMedia(uri: String) {
        synchronized(lock) {
            mediaItems.removeAll { it.uri == uri }
        }
    }

    /**
     * SF-07: Returns session media sorted most recent first.
     */
    fun getSessionMedia(): List<MediaItem> {
        return synchronized(lock) {
            mediaItems.sortedByDescending { it.dateTaken }
        }
    }

    /**
     * SF-04: Check if a media item belongs to the current session.
     */
    fun isMediaInSession(dateTaken: Long, relativePath: String): Boolean {
        return synchronized(lock) {
            if (!isSessionActive) return false

            val threshold = sessionStartTimestamp - Constants.SESSION_TIMESTAMP_TOLERANCE_MS
            if (dateTaken < threshold) return false

            if (!relativePath.startsWith(Constants.MEDIA_RELATIVE_PATH_PREFIX)) return false

            true
        }
    }

    companion object {
        // Singleton for service-wide access
        val instance = SessionTracker()
    }
}
