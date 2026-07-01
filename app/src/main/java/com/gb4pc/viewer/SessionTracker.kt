package com.gb4pc.viewer

import com.gb4pc.Constants
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

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

    private val _sessionMedia = MutableStateFlow<List<MediaItem>>(emptyList())

    /**
     * SF-07: Observable, most-recent-first view of the current session media.
     * Emits a fresh list on every mutation so collectors (e.g. SecureViewerActivity)
     * re-render when the session is populated, reset, or edited (#537).
     */
    val sessionMedia: StateFlow<List<MediaItem>> = _sessionMedia.asStateFlow()

    /**
     * SF-01: Begin a new session, recording the start timestamp (SF-02).
     */
    fun startSession() {
        synchronized(lock) {
            isSessionActive = true
            sessionStartTimestamp = System.currentTimeMillis()
            mediaItems.clear()
            emitSnapshot()
        }
    }

    /**
     * SF-01: End the session, clearing all media (SF-05).
     */
    fun endSession() {
        synchronized(lock) {
            isSessionActive = false
            mediaItems.clear()
            emitSnapshot()
        }
    }

    fun addMedia(item: MediaItem) {
        synchronized(lock) {
            if (!isSessionActive) return
            if (mediaItems.none { it.uri == item.uri }) {
                mediaItems.add(item)
                emitSnapshot()
            }
        }
    }

    fun removeMedia(uri: String) {
        synchronized(lock) {
            if (mediaItems.removeAll { it.uri == uri }) {
                emitSnapshot()
            }
        }
    }

    /**
     * SF-07: Returns session media sorted most recent first.
     */
    fun getSessionMedia(): List<MediaItem> =
        synchronized(lock) {
            sortedSnapshot()
        }

    /**
     * SF-07: Builds a fresh, most-recent-first snapshot of the current media.
     * Callers must already hold [lock].
     */
    private fun sortedSnapshot(): List<MediaItem> = mediaItems.sortedByDescending { it.dateTaken }

    /**
     * Publishes a fresh snapshot to [sessionMedia]. Callers must already hold [lock]
     * so the emitted list is consistent with the state under the lock.
     * A new list instance is emitted so StateFlow structural-equality de-duplication
     * compares contents rather than the same mutable reference.
     */
    private fun emitSnapshot() {
        _sessionMedia.value = sortedSnapshot()
    }

    /**
     * SF-04: Check if a media item belongs to the current session.
     */
    fun isMediaInSession(
        dateTaken: Long,
        relativePath: String,
    ): Boolean {
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
