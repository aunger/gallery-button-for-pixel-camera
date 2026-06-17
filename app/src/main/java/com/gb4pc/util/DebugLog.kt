package com.gb4pc.util

import android.util.Log
import com.gb4pc.Constants
import java.util.LinkedList

/**
 * In-memory circular buffer debug log (DA-03, UI-10).
 * Thread-safe. Entries are lost when the process is killed.
 *
 * Each call to [log] also forwards the message to [android.util.Log] under the tag [LOGCAT_TAG]
 * so that all application log messages are visible in logcat and CI log captures (Issue #399).
 */
object DebugLog {

    /** logcat tag used for all forwarded messages. */
    const val LOGCAT_TAG = "GB4PC"

    data class Entry(val timestamp: Long, val message: String)

    private val buffer = LinkedList<Entry>()
    private val lock = Any()

    /** Called on the thread that called [log] whenever a new entry is added. */
    var listener: (() -> Unit)? = null

    fun log(message: String) {
        val snapshot: (() -> Unit)?
        synchronized(lock) {
            Log.d(LOGCAT_TAG, message)
            buffer.addLast(Entry(System.currentTimeMillis(), message))
            while (buffer.size > Constants.DEBUG_LOG_BUFFER_SIZE) buffer.removeFirst()
            snapshot = listener
        }
        snapshot?.invoke()
    }

    fun getEntries(): List<Entry> {
        synchronized(lock) {
            return buffer.toList()
        }
    }

    fun clear() {
        val snapshot: (() -> Unit)?
        synchronized(lock) {
            buffer.clear()
            snapshot = listener
        }
        snapshot?.invoke()
    }
}
