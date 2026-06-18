package com.gb4pc.util

import android.util.Log
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.shadows.ShadowLog

/**
 * Verifies that DebugLog.log() forwards each message to android.util.Log (Issue #399).
 *
 * Robolectric is required so ShadowLog captures Log.d() calls made by DebugLog.
 */
@RunWith(RobolectricTestRunner::class)
class DebugLogLogcatTest {
    @Before
    fun setUp() {
        ShadowLog.reset()
        DebugLog.clear()
    }

    @Test
    fun `log forwards message to logcat under the GB4PC tag`() {
        DebugLog.log("hello logcat")

        val logged = ShadowLog.getLogsForTag(DebugLog.LOGCAT_TAG)
        assertEquals("Expected exactly one logcat entry for tag GB4PC", 1, logged.size)
        assertEquals("hello logcat", logged[0].msg)
        assertEquals(Log.DEBUG, logged[0].type)
    }

    @Test
    fun `log forwards every message to logcat in order`() {
        DebugLog.log("first")
        DebugLog.log("second")
        DebugLog.log("third")

        val logged = ShadowLog.getLogsForTag(DebugLog.LOGCAT_TAG)
        assertEquals(3, logged.size)
        assertEquals("first", logged[0].msg)
        assertEquals("second", logged[1].msg)
        assertEquals("third", logged[2].msg)
    }

    @Test
    fun `logcat tag constant equals GB4PC`() {
        assertEquals("GB4PC", DebugLog.LOGCAT_TAG)
    }

    @Test
    fun `log forwards message to logcat even when buffer is at capacity`() {
        // Fill the buffer to its limit so the next call will evict the oldest entry.
        repeat(com.gb4pc.Constants.DEBUG_LOG_BUFFER_SIZE) { i ->
            DebugLog.log("fill $i")
        }
        ShadowLog.reset()

        DebugLog.log("overflow message")

        val logged = ShadowLog.getLogsForTag(DebugLog.LOGCAT_TAG)
        assertTrue(
            "logcat must receive the overflow message even when the in-memory buffer evicts it",
            logged.any { it.msg == "overflow message" },
        )
    }
}
