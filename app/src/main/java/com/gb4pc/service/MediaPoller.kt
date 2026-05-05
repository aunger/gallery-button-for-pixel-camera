package com.gb4pc.service

import android.os.Handler
import com.gb4pc.Constants
import com.gb4pc.util.DebugLog

/**
 * Periodically fans new MediaStore changes out to [MediaChangeDispatcher] and
 * [ThumbnailChangeDispatcher] while a secure camera session is active.
 *
 * ## Why polling is needed (Issue #81)
 *
 * The session and thumbnail [android.database.ContentObserver]s are normally the fast
 * path that detects newly captured photos.  Two earlier fixes (PR #88 and PR #98)
 * tightened the IS_PENDING retry logic inside the dispatchers, but both relied on
 * the observers' `onChange` callback firing in the first place.
 *
 * Field reports for issue #81 show that on a *locked* device the system can suppress
 * or delay [android.database.ContentObserver] dispatch indefinitely — so the
 * dispatcher's `onChange` is never invoked, the retry runnable is never scheduled,
 * and photos written to MediaStore are silently missed.  The overlay therefore stays
 * on the gallery icon and the secure-viewer filmstrip stays empty.
 *
 * [MediaPoller] adds a safety-net poll: every [intervalMs] it invokes the same
 * dispatcher entry points the observers would have invoked, so newly committed
 * photos are picked up within the polling interval even when no observer callback
 * arrives.  Detection on the lock screen therefore degrades from "never" to "within
 * one polling interval" in the worst case.
 *
 * The poller only runs while [start] / [stop] bracket an active session — typically
 * mirroring the lifecycle of the media observer.  When the device is unlocked and
 * the observer fires reliably the poller still runs but is a cheap no-op duplicate
 * (deduplication in [com.gb4pc.viewer.SessionTracker.addMedia] guarantees idempotency).
 *
 * ## Side-effect-free
 *
 * The poller does not query MediaStore itself — it delegates to the existing
 * dispatchers, so the test surface is unchanged and there is one canonical query
 * path for both the observer and the poller.
 */
class MediaPoller(
    private val handler: Handler,
    private val onPoll: () -> Unit,
    private val intervalMs: Long = Constants.MEDIA_POLL_INTERVAL_MS,
) {
    private var pollRunnable: Runnable? = null
    private var running: Boolean = false

    /** True while the poller has an outstanding scheduled tick. */
    val isRunning: Boolean
        get() = running

    /**
     * Starts the periodic poll.  Idempotent — calling [start] while already running is
     * a no-op (no second runnable is scheduled).  The first poll fires after [intervalMs]
     * milliseconds, not immediately, since the caller has just performed any work the
     * triggering event implies.
     */
    fun start() {
        if (running) {
            DebugLog.log("MediaPoller.start: already running, ignoring")
            return
        }
        running = true
        scheduleNext()
        DebugLog.log("MediaPoller started (interval=${intervalMs}ms)")
    }

    /**
     * Stops the periodic poll and removes any in-flight scheduled tick.  Idempotent.
     */
    fun stop() {
        if (!running) return
        running = false
        pollRunnable?.let { handler.removeCallbacks(it) }
        pollRunnable = null
        DebugLog.log("MediaPoller stopped")
    }

    private fun scheduleNext() {
        val runnable = Runnable {
            try {
                onPoll()
            } catch (e: Exception) {
                DebugLog.log("MediaPoller.onPoll threw: ${e.stackTraceToString()}")
            }
            // Only re-schedule if we are still running.  If [onPoll] (or anything else) called
            // [stop] during this tick, the loop terminates here.
            if (running) scheduleNext()
        }
        pollRunnable = runnable
        handler.postDelayed(runnable, intervalMs)
    }
}
