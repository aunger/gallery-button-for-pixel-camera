package com.gb4pc.service

import android.os.Handler
import com.gb4pc.Constants
import com.gb4pc.util.DebugLog
import com.gb4pc.viewer.MediaItem
import com.gb4pc.viewer.SessionTracker

/**
 * Handles media-change events from the session ContentObserver.
 *
 * Extracted from [OverlayService] for unit-testability. All Android-framework
 * side-effects are accessed through constructor lambdas so this class can be
 * exercised in plain JVM tests.
 *
 * ## IS_PENDING race
 *
 * Pixel Camera (and other modern camera apps on API 29+) inserts new photos into
 * MediaStore with IS_PENDING = 1 while the file is being written, then clears the
 * flag once the write completes. The ContentObserver fires on both state transitions.
 * On the first fire (IS_PENDING = 1) the default query excludes the pending row and
 * [queryLatestMedia] returns null. On the second fire (IS_PENDING cleared) the
 * committed row is found and added to the session.
 *
 * However, on some devices/firmware the second ContentObserver callback for the
 * IS_PENDING→0 transition is not delivered reliably. [onMediaChanged] therefore
 * schedules a single retry after [Constants.MEDIA_OBSERVER_RETRY_MS] when the
 * initial query returns null, picking up committed rows that the first callback
 * missed.
 */
class MediaChangeDispatcher(
    private val sessionTracker: SessionTracker,
    private val handler: Handler,
    /** Returns the most recent [MediaItem] added after [sessionStartMs], or null. */
    private val queryLatestMedia: (sessionStartMs: Long) -> MediaItem?,
    private val retryDelayMs: Long = Constants.MEDIA_OBSERVER_RETRY_MS,
) {

    /**
     * Called whenever the media ContentObserver detects a change.
     *
     * Immediately queries for the latest committed media added since [sessionStartMs].
     * If the query returns null (item is still IS_PENDING), schedules a single retry
     * after [retryDelayMs] ms.
     */
    fun onMediaChanged(sessionStartMs: Long) {
        val item = queryLatestMedia(sessionStartMs)
        if (item != null) {
            sessionTracker.addMedia(item)
            DebugLog.log("Media added to session: ${item.uri}")
        } else {
            // First onChange may fire while the photo is IS_PENDING; schedule a retry so we
            // catch it once it has been committed to MediaStore.
            DebugLog.log("Media query returned null — scheduling retry in ${retryDelayMs}ms")
            handler.postDelayed({
                val retryItem = queryLatestMedia(sessionStartMs)
                if (retryItem != null) {
                    sessionTracker.addMedia(retryItem)
                    DebugLog.log("Media added to session (retry): ${retryItem.uri}")
                }
            }, retryDelayMs)
        }
    }
}
