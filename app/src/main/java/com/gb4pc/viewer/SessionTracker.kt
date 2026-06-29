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
     * Observers notified whenever the session's media set changes (start, end, add, remove).
     *
     * Issue #537: [SecureViewerActivity] reads the session once in `onCreate()`. When the
     * activity opens in the brief window before the OverlayService's ContentObserver has
     * populated the session, that one-shot read sees an empty session and renders a black
     * "no photos" state. A reactive observer lets the viewer auto-refresh as soon as media
     * arrives, rather than relying solely on the startup read.
     *
     * Backed by a [CopyOnWriteArraySet] so registration/deregistration and notification are
     * thread-safe without holding [lock] while invoking listeners (which would risk a
     * re-entrant deadlock if a listener called back into the tracker).
     */
    private val listeners = java.util.concurrent.CopyOnWriteArraySet<SessionListener>()

    /** Listener for session media changes. See [addListener]. */
    fun interface SessionListener {
        fun onSessionMediaChanged()
    }

    /**
     * Register a listener for session media changes. Re-registering the same instance is a no-op.
     * Listeners are invoked on the thread that mutates the session; UI consumers must marshal
     * any view updates onto the main thread themselves.
     */
    fun addListener(listener: SessionListener) {
        listeners.add(listener)
    }

    /**
     * Deregister a previously registered listener.
     * Thread-safe; no-op if the listener is not currently registered.
     */
    fun removeListener(listener: SessionListener) {
        listeners.remove(listener)
    }

    private fun notifyListeners() {
        for (listener in listeners) {
            listener.onSessionMediaChanged()
        }
    }

    /**
     * SF-01: Begin a new session, recording the start timestamp (SF-02).
     */
    fun startSession() {
        synchronized(lock) {
            isSessionActive = true
            sessionStartTimestamp = System.currentTimeMillis()
            mediaItems.clear()
        }
        notifyListeners()
    }

    /**
     * SF-01: End the session, clearing all media (SF-05).
     * No-op (and no notification) if the session is already inactive.
     */
    fun endSession() {
        val changed =
            synchronized(lock) {
                if (!isSessionActive) return
                isSessionActive = false
                mediaItems.clear()
                true
            }
        if (changed) notifyListeners()
    }

    fun addMedia(item: MediaItem) {
        val changed =
            synchronized(lock) {
                if (!isSessionActive) return
                if (mediaItems.none { it.uri == item.uri }) {
                    mediaItems.add(item)
                    true
                } else {
                    false
                }
            }
        if (changed) notifyListeners()
    }

    fun removeMedia(uri: String) {
        val changed =
            synchronized(lock) {
                mediaItems.removeAll { it.uri == uri }
            }
        if (changed) notifyListeners()
    }

    /**
     * SF-07: Returns session media sorted most recent first.
     */
    fun getSessionMedia(): List<MediaItem> =
        synchronized(lock) {
            mediaItems.sortedByDescending { it.dateTaken }
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
