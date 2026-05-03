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
 * On some devices the IS_PENDING→0 second ContentObserver callback (which signals
 * the file is fully written) is not delivered reliably. When [queryLatestMedia]
 * returns null on the first callback, [onThumbnailChanged] schedules a single retry
 * after [retryDelayMs] ms so the thumbnail update is not silently dropped.
 */
class ThumbnailChangeDispatcher(
    private val handler: Handler,
    /** Returns the most recent [MediaItem] added after [startMs], or null. */
    private val queryLatestMedia: (startMs: Long) -> MediaItem?,
    /** Called with the URI string of the resolved media item so the overlay can display it. */
    private val showThumbnail: (uri: String) -> Unit,
    private val retryDelayMs: Long = Constants.MEDIA_OBSERVER_RETRY_MS,
) {

    /**
     * Called whenever the thumbnail ContentObserver detects a change.
     *
     * Immediately queries for the latest committed media added since [startMs].
     * If the query returns null (item is still IS_PENDING), schedules a single retry
     * after [retryDelayMs] ms.
     */
    fun onThumbnailChanged(startMs: Long) {
        val item = queryLatestMedia(startMs)
        if (item != null) {
            showThumbnail(item.uri)
            DebugLog.log("Thumbnail updated: ${item.uri}")
        } else {
            // Item may still be IS_PENDING; schedule a single retry so we don't
            // silently drop the thumbnail update on devices where the second
            // ContentObserver callback (IS_PENDING→0) is unreliable.
            DebugLog.log("Thumbnail query returned null — scheduling retry in ${retryDelayMs}ms")
            handler.postDelayed({
                val retryItem = queryLatestMedia(startMs)
                if (retryItem != null) {
                    showThumbnail(retryItem.uri)
                    DebugLog.log("Thumbnail updated (retry): ${retryItem.uri}")
                }
            }, retryDelayMs)
        }
    }
}
