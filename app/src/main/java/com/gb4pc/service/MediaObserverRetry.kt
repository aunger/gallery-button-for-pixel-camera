package com.gb4pc.service

import android.os.Handler
import com.gb4pc.Constants

/**
 * Generic "ContentObserver onChange + delayed retry" scaffold used by the session-media and
 * overlay-thumbnail observers.
 *
 * ## Why a retry exists
 *
 * Pixel Camera (and other modern camera apps on API 29+) inserts new photos into MediaStore
 * with `IS_PENDING = 1` while the file is being written, then clears the flag once the write
 * completes. The ContentObserver fires on both transitions, but on the first fire the default
 * query excludes the still-pending row — so the result reflects only previously committed
 * items. On some devices/firmware the second callback (IS_PENDING → 0) is not delivered
 * reliably, particularly on locked devices.
 *
 * [onChange] therefore always schedules a single delayed retry that re-runs the query after
 * [retryDelayMs] ms. Burst-mode shots cancel-and-reschedule, so at most one retry is in flight
 * at any time.
 *
 * ## Generic in T
 *
 * [T] is whatever the query returns (e.g. `List<MediaItem>` for the session observer, or
 * `MediaItem?` for the overlay-thumbnail observer). [handleResult] receives the query result
 * along with a flag indicating whether this call is the initial one or the retry, so callers
 * can log/dedup as appropriate.
 */
class MediaObserverRetry<T>(
    private val handler: Handler,
    private val query: (startMs: Long) -> T,
    private val handleResult: (result: T, isRetry: Boolean) -> Unit,
    private val retryDelayMs: Long = Constants.MEDIA_OBSERVER_RETRY_MS,
) {
    // At most one pending retry runnable at a time; cancelled and rescheduled on each onChange.
    private var pendingRetry: Runnable? = null

    fun onChange(startMs: Long) {
        handleResult(query(startMs), false)

        pendingRetry?.let { handler.removeCallbacks(it) }
        val runnable =
            Runnable {
                pendingRetry = null
                handleResult(query(startMs), true)
            }
        pendingRetry = runnable
        handler.postDelayed(runnable, retryDelayMs)
    }
}
