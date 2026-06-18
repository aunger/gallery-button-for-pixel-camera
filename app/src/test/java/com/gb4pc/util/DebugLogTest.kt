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

    @Test
    fun `listener is invoked on each log call`() {
        var callCount = 0
        DebugLog.listener = { callCount++ }
        try {
            DebugLog.log("a")
            DebugLog.log("b")
            assertEquals(2, callCount)
        } finally {
            DebugLog.listener = null
        }
    }

    @Test
    fun `clear invokes the listener with empty entries`() {
        var callCount = 0
        var entriesOnClear: List<DebugLog.Entry>? = null
        DebugLog.log("before")
        DebugLog.listener = {
            callCount++
            entriesOnClear = DebugLog.getEntries()
        }
        try {
            DebugLog.clear()
            assertEquals(1, callCount)
            assertNotNull(entriesOnClear)
            assertTrue(entriesOnClear!!.isEmpty())
        } finally {
            DebugLog.listener = null
        }
    }

    @Test
    fun `getEntries returns entries in oldest-first order after clear and re-log`() {
        DebugLog.log("old")
        DebugLog.clear()
        DebugLog.log("new1")
        DebugLog.log("new2")
        val entries = DebugLog.getEntries()
        assertEquals(2, entries.size)
        assertEquals("new1", entries[0].message)
        assertEquals("new2", entries[1].message)
    }
}
