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
 * On the first fire (IS_PENDING = 1) the default query excludes the pending row, so
 * any item returned by [queryAllMedia] is a previously committed photo.  On the second
 * fire (IS_PENDING = 0) the newly committed row is found and added to the session.
 *
 * However, on some devices/firmware the second ContentObserver callback for the
 * IS_PENDING→0 transition is not delivered reliably (particularly on locked devices).
 * [onMediaChanged] therefore always schedules a single retry after [retryDelayMs]
 * (cancel-and-reschedule on rapid-fire shots) that queries for all newly committed
 * items — picking up photos whose IS_PENDING=0 notification was missed.
 *
 * ## Multiple photos
 *
 * When multiple photos are taken in quick succession, only the most recent IS_PENDING=1
 * callback would have found a null result (no committed item) with the old single-retry
 * approach.  Each subsequent IS_PENDING=1 callback found the *previous* committed photo
 * (non-null), so no retry was scheduled for the new photo.  By querying *all* items
 * since [sessionStartMs] on every callback and on the retry, every committed photo is
 * captured regardless of whether its IS_PENDING=0 notification fires.  Deduplication
 * inside [SessionTracker.addMedia] prevents double-adds.
 */
class MediaChangeDispatcher(
    private val sessionTracker: SessionTracker,
    private val handler: Handler,
    /** Returns all [MediaItem]s with DATE_ADDED after [sessionStartMs], newest-first. */
    private val queryAllMedia: (sessionStartMs: Long) -> List<MediaItem>,
    private val retryDelayMs: Long = Constants.MEDIA_OBSERVER_RETRY_MS,
) {
    // At most one pending retry runnable at a time; cancelled and rescheduled on each onChange.
    private var pendingRetry: Runnable? = null

    /**
     * Called whenever the media ContentObserver detects a change.
     *
     * Immediately adds all committed media items found since [sessionStartMs] to the session.
     * Then cancels any in-flight retry and schedules a fresh one after [retryDelayMs] ms so
     * that items still in IS_PENDING state at the time of this call are captured once they
     * are committed.  Rapid-fire photos produce at most one pending retry at any moment.
     */
    fun onMediaChanged(sessionStartMs: Long) {
        val items = queryAllMedia(sessionStartMs)
        items.forEach { item ->
            sessionTracker.addMedia(item)
            DebugLog.log("Media added to session: ${item.uri}")
        }
        if (items.isEmpty()) {
            DebugLog.log("Media query returned empty — scheduling retry in ${retryDelayMs}ms")
        }

        // Always schedule a retry: the onChange may have fired while the new item was
        // still IS_PENDING and therefore excluded from the query.  Cancel any previous
        // pending retry to avoid stacking runnables during burst-mode shooting.
        pendingRetry?.let { handler.removeCallbacks(it) }
        val runnable = Runnable {
            pendingRetry = null
            val retryItems = queryAllMedia(sessionStartMs)
            retryItems.forEach { item ->
                sessionTracker.addMedia(item)
                DebugLog.log("Media added to session (retry): ${item.uri}")
            }
        }
        pendingRetry = runnable
        handler.postDelayed(runnable, retryDelayMs)
    }
}
