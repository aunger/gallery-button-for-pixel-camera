package com.gb4pc.service

import android.os.Handler
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.mockito.kotlin.*

/**
 * Unit tests for [MediaPoller].
 *
 * Covers the safety-net polling behaviour added for issue #81:
 * - [start] schedules a tick every [intervalMs] until [stop] is called
 * - [start] is idempotent (no double-scheduling)
 * - [stop] is idempotent and cancels the in-flight tick
 * - The poll runnable invokes [onPoll] and re-schedules itself
 * - [onPoll] exceptions do not abort the polling loop
 */
class MediaPollerTest {

    private lateinit var handler: Handler
    private val intervalMs = 1500L

    private var pollCount = 0

    @Before
    fun setUp() {
        handler = mock()
        pollCount = 0
    }

    // ── start / stop lifecycle ──────────────────────────────────────────────

    @Test
    fun `start schedules first tick after intervalMs`() {
        val poller = MediaPoller(handler = handler, onPoll = { pollCount++ }, intervalMs = intervalMs)

        poller.start()

        verify(handler).postDelayed(any(), eq(intervalMs))
        assertTrue(poller.isRunning)
        assertEquals("onPoll must not fire on start (only after the first delay)", 0, pollCount)
    }

    @Test
    fun `start is idempotent - second call is a no-op`() {
        val poller = MediaPoller(handler = handler, onPoll = { pollCount++ }, intervalMs = intervalMs)

        poller.start()
        poller.start()
        poller.start()

        // Only the first call schedules a tick
        verify(handler, times(1)).postDelayed(any(), eq(intervalMs))
    }

    @Test
    fun `stop removes the in-flight tick and clears running flag`() {
        val poller = MediaPoller(handler = handler, onPoll = { pollCount++ }, intervalMs = intervalMs)

        poller.start()

        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(intervalMs))

        poller.stop()

        assertFalse(poller.isRunning)
        verify(handler).removeCallbacks(runnableCaptor.firstValue)
    }

    @Test
    fun `stop is idempotent when not running`() {
        val poller = MediaPoller(handler = handler, onPoll = { pollCount++ }, intervalMs = intervalMs)

        poller.stop()
        poller.stop()

        verify(handler, never()).removeCallbacks(any())
        assertFalse(poller.isRunning)
    }

    @Test
    fun `start after stop schedules a fresh tick`() {
        val poller = MediaPoller(handler = handler, onPoll = { pollCount++ }, intervalMs = intervalMs)

        poller.start()
        poller.stop()
        poller.start()

        verify(handler, times(2)).postDelayed(any(), eq(intervalMs))
        assertTrue(poller.isRunning)
    }

    // ── poll runnable behaviour ─────────────────────────────────────────────

    @Test
    fun `tick invokes onPoll exactly once`() {
        val poller = MediaPoller(handler = handler, onPoll = { pollCount++ }, intervalMs = intervalMs)
        poller.start()

        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(intervalMs))

        runnableCaptor.firstValue.run()

        assertEquals(1, pollCount)
    }

    @Test
    fun `tick re-schedules itself while running`() {
        val poller = MediaPoller(handler = handler, onPoll = { pollCount++ }, intervalMs = intervalMs)
        poller.start()

        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(intervalMs))

        // Fire the tick — it must schedule the next tick.
        runnableCaptor.firstValue.run()

        verify(handler, times(2)).postDelayed(any(), eq(intervalMs))
    }

    @Test
    fun `tick does not re-schedule after stop is called from inside onPoll`() {
        var capturedPoller: MediaPoller? = null
        val poller = MediaPoller(
            handler = handler,
            onPoll = {
                pollCount++
                capturedPoller?.stop()
            },
            intervalMs = intervalMs,
        )
        capturedPoller = poller
        poller.start()

        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(intervalMs))

        runnableCaptor.firstValue.run()

        // Only the original postDelayed (from start) — onPoll's stop() prevented re-schedule.
        verify(handler, times(1)).postDelayed(any(), eq(intervalMs))
        assertEquals(1, pollCount)
        assertFalse(poller.isRunning)
    }

    @Test
    fun `onPoll exception does not abort the polling loop`() {
        val poller = MediaPoller(
            handler = handler,
            onPoll = {
                pollCount++
                throw RuntimeException("boom")
            },
            intervalMs = intervalMs,
        )
        poller.start()

        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(intervalMs))

        // Fire the tick — onPoll throws but the loop must continue.
        runnableCaptor.firstValue.run()

        assertEquals(1, pollCount)
        assertTrue(poller.isRunning)
        // The next tick was scheduled despite the exception.
        verify(handler, times(2)).postDelayed(any(), eq(intervalMs))
    }

    @Test
    fun `multiple consecutive ticks each invoke onPoll`() {
        val poller = MediaPoller(handler = handler, onPoll = { pollCount++ }, intervalMs = intervalMs)
        poller.start()

        val runnableCaptor = argumentCaptor<Runnable>()
        // Fire three ticks, each one captured from the most recent postDelayed call.
        for (i in 1..3) {
            verify(handler, times(i)).postDelayed(runnableCaptor.capture(), eq(intervalMs))
            runnableCaptor.lastValue.run()
        }

        assertEquals(3, pollCount)
    }
}
