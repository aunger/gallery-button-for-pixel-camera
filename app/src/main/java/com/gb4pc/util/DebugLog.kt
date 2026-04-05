package com.gb4pc.util

import com.gb4pc.Constants
import java.util.LinkedList

/**
 * In-memory circular buffer debug log (DA-03, UI-10).
 * Thread-safe. Entries are lost when the process is killed.
 */
object DebugLog {

    data class Entry(val timestamp: Long, val message: String)

    private val buffer = LinkedList<Entry>()
    private val lock = Any()

    /** Called on the thread that called [log] whenever a new entry is added. */
    var listener: (() -> Unit)? = null

    fun log(message: String) {
        val snapshot: (() -> Unit)?
        synchronized(lock) {
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
        synchronized(lock) {
            buffer.clear()
        }
    }
}
