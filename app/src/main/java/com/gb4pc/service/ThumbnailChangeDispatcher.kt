package com.gb4pc.service

import android.os.Handler
import com.gb4pc.Constants
import com.gb4pc.util.DebugLog
import com.gb4pc.viewer.MediaItem

/**
 * Handles media-change events from the thumbnail ContentObserver.
 *
 * Extracted from [OverlayService] for unit-testability. All Android-framework
 * side-effects are accessed through constructor lambdas so this class can be
 * exercised in plain JVM tests.
 *
 * ## IS_PENDING retry
 *
 * When a new photo is taken, the ContentObserver fires while the photo is still
 * IS_PENDING=1.  [queryLatestMedia] returns the most recently *committed* item at
 * that moment — which is the previous photo, not the new one.  The overlay would
 * therefore remain showing the old thumbnail until the IS_PENDING=0 callback fired.
 *
 * On some devices (particularly on the lock screen) the IS_PENDING=0 callback is
 * not delivered reliably.  [onThumbnailChanged] therefore always schedules a retry
 * after [retryDelayMs] ms.  Any in-flight retry is cancelled and rescheduled on
 * each new callback so that burst-mode photos produce at most one pending retry.
 * The retry queries for the most recently committed item and updates the overlay
 * thumbnail, ensuring the overlay shows the correct photo within [retryDelayMs] ms
 * even when the IS_PENDING=0 notification is suppressed.
 */
class ThumbnailChangeDispatcher(
    private val handler: Handler,
    /** Returns the most recent [MediaItem] added after [startMs], or null. */
    private val queryLatestMedia: (startMs: Long) -> MediaItem?,
    /** Called with the URI string of the resolved media item so the overlay can display it. */
    private val showThumbnail: (uri: String) -> Unit,
    private val retryDelayMs: Long = Constants.MEDIA_OBSERVER_RETRY_MS,
) {
    // At most one pending retry runnable at a time; cancelled and rescheduled on each onChange.
    private var pendingRetry: Runnable? = null

    /**
     * Called whenever the thumbnail ContentObserver detects a change.
     *
     * Immediately updates the thumbnail if a committed item is found.  Then cancels any
     * in-flight retry and schedules a fresh one after [retryDelayMs] ms.  This ensures
     * the overlay shows the newest photo even when the IS_PENDING=0 callback is suppressed
     * or arrives after a long delay on locked devices.
     */
    fun onThumbnailChanged(startMs: Long) {
        val item = queryLatestMedia(startMs)
        if (item != null) {
            showThumbnail(item.uri)
            DebugLog.log("Thumbnail updated: ${item.uri}")
        } else {
            DebugLog.log("Thumbnail query returned null — scheduling retry in ${retryDelayMs}ms")
        }

        // Always schedule a retry: when the new photo is IS_PENDING, the query above returns
        // the previous photo (if any).  The retry fires after the new photo is committed and
        // shows the correct, up-to-date thumbnail.  Cancel any previous pending retry to avoid
        // stacking runnables during burst-mode shooting.
        pendingRetry?.let { handler.removeCallbacks(it) }
        val runnable = Runnable {
            pendingRetry = null
            val retryItem = queryLatestMedia(startMs)
            if (retryItem != null) {
                showThumbnail(retryItem.uri)
                DebugLog.log("Thumbnail updated (retry): ${retryItem.uri}")
            }
        }
        pendingRetry = runnable
        handler.postDelayed(runnable, retryDelayMs)
    }
}
