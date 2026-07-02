package com.gb4pc.service

import android.os.Handler
import com.gb4pc.Constants
import com.gb4pc.util.DebugLog

/**
 * Generic "ContentObserver onChange + delayed retry" scaffold used by the session-media and
 * overlay-thumbnail observers.
 *
 * ## Why a retry exists
 *
 * Pixel Camera (and other modern camera apps on API 29+) inserts new photos into MediaStore
 * with `IS_PENDING = 1` while the file is being written, then clears the flag once the write
 * completes. The ContentObserver fires on both transitions, but on the first fire the default
 * query excludes the still-pending row, so the result reflects only previously committed
 * items. On some devices/firmware the second callback (IS_PENDING → 0) is not delivered
 * reliably, particularly on locked devices.
 *
 * [onChange] therefore re-runs the query after [retryDelayMs] ms whenever the result so far is
 * not yet successful. Because the commit can land later than a single [retryDelayMs] window (or
 * the IS_PENDING -> 0 callback may never arrive while locked), the retry re-schedules itself up
 * to [maxAttempts] times per onChange event instead of giving up after one attempt. Each fresh
 * onChange is a new commit event, so it cancels any in-flight retry and resets the attempt budget.
 * Burst-mode shots therefore cancel-and-reschedule, so at most one retry is in flight at any time.
 *
 * ## Generic in T
 *
 * [T] is whatever the query returns (e.g. `List<MediaItem>` for the session observer, or
 * `MediaItem?` for the overlay-thumbnail observer). [isSuccess] tells the helper when a result is
 * good enough to stop retrying: for the thumbnail observer, `{ it != null }` stops once the item
 * commits; for the session observer, `{ false }` retries unconditionally, because a non-empty
 * list may reflect only previously-committed items while the new shot is still pending. [handleResult]
 * receives the query result along with a flag indicating whether this call is the initial one or
 * the retry, so callers can log/dedup as appropriate.
 */
class MediaObserverRetry<T>(
    private val handler: Handler,
    private val query: (startMs: Long) -> T,
    private val isSuccess: (result: T) -> Boolean,
    private val handleResult: (result: T, isRetry: Boolean) -> Unit,
    private val retryDelayMs: Long = Constants.MEDIA_OBSERVER_RETRY_MS,
    private val maxAttempts: Int = Constants.MEDIA_OBSERVER_RETRY_MAX_ATTEMPTS,
) {
    // At most one pending retry runnable at a time; cancelled and rescheduled on each onChange.
    private var pendingRetry: Runnable? = null

    // Retries scheduled for the current onChange event, capped at maxAttempts.
    private var attempts = 0

    fun onChange(startMs: Long) {
        // Fresh observer event: cancel any in-flight retry and reset the budget.
        pendingRetry?.let { handler.removeCallbacks(it) }
        pendingRetry = null
        attempts = 0

        val result = query(startMs)
        handleResult(result, false)
        if (!isSuccess(result)) scheduleRetry(startMs)
    }

    private fun scheduleRetry(startMs: Long) {
        if (attempts >= maxAttempts) {
            DebugLog.log("MediaObserverRetry: retry budget exhausted after $attempts attempts")
            return
        }
        attempts++
        val runnable =
            Runnable {
                pendingRetry = null
                val result = query(startMs)
                handleResult(result, true)
                if (!isSuccess(result)) scheduleRetry(startMs)
            }
        pendingRetry = runnable
        handler.postDelayed(runnable, retryDelayMs)
    }
}
