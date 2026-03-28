package com.gb4pc.util

import com.gb4pc.Constants
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

class DebugLogTest {

    @Before
    fun setUp() {
        DebugLog.clear()
    }

    @Test
    fun `log adds entry with timestamp`() {
        DebugLog.log("test message")
        val entries = DebugLog.getEntries()
        assertEquals(1, entries.size)
        assertTrue(entries[0].message == "test message")
        assertTrue(entries[0].timestamp > 0)
    }

    @Test
    fun `log respects buffer size limit`() {
        repeat(Constants.DEBUG_LOG_BUFFER_SIZE + 50) { i ->
            DebugLog.log("message $i")
        }
        val entries = DebugLog.getEntries()
        assertEquals(Constants.DEBUG_LOG_BUFFER_SIZE, entries.size)
        // Oldest entries should be dropped
        assertEquals("message 50", entries.first().message)
    }

    @Test
    fun `clear removes all entries`() {
        DebugLog.log("test")
        DebugLog.clear()
        assertTrue(DebugLog.getEntries().isEmpty())
    }

    @Test
    fun `getEntries returns a copy`() {
        DebugLog.log("test")
        val entries1 = DebugLog.getEntries()
        DebugLog.log("another")
        val entries2 = DebugLog.getEntries()
        // entries1 should not be affected by subsequent log calls
        assertEquals(1, entries1.size)
        assertEquals(2, entries2.size)
    }

    @Test
    fun `entries are ordered oldest first`() {
        DebugLog.log("first")
        DebugLog.log("second")
        DebugLog.log("third")
        val entries = DebugLog.getEntries()
        assertEquals("first", entries[0].message)
        assertEquals("second", entries[1].message)
        assertEquals("third", entries[2].message)
    }
}
