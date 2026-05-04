package com.gb4pc.service

import android.os.Handler
import com.gb4pc.viewer.MediaItem
import com.gb4pc.viewer.SessionTracker
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.mockito.kotlin.*

/**
 * Unit tests for [MediaChangeDispatcher].
 *
 * Covers:
 * - Immediate-hit path: all committed items are added to the session right away.
 * - Always-retry: a retry is always scheduled after every onChange, not just when the
 *   query returns empty. This ensures IS_PENDING items that were excluded from the
 *   initial query are captured once committed.
 * - Multiple photos: each committed photo since session start is added (not just the
 *   single most-recent), fixing the case where a new photo's IS_PENDING=1 callback
 *   finds an older committed photo and skips the retry.
 * - Cancel-and-reschedule: rapid-fire photos replace any pending retry with a fresh one,
 *   keeping at most one outstanding runnable at any time.
 * - sessionStartMs forwarding.
 */
class MediaChangeDispatcherTest {

    private lateinit var sessionTracker: SessionTracker
    private lateinit var handler: Handler
    private val retryDelayMs = 500L

    private val item1 = MediaItem(
        uri = "content://media/external/images/media/1",
        dateTaken = 1_000_000L,
        isVideo = false
    )
    private val item2 = MediaItem(
        uri = "content://media/external/images/media/2",
        dateTaken = 2_000_000L,
        isVideo = false
    )

    @Before
    fun setUp() {
        sessionTracker = mock()
        handler = mock()
    }

    // ── Immediate-hit path ──────────────────────────────────────────────────

    /**
     * When committed items are found immediately, all of them are added to the session.
     */
    @Test
    fun `onMediaChanged adds all items immediately when query returns results`() {
        val dispatcher = MediaChangeDispatcher(
            sessionTracker = sessionTracker,
            handler = handler,
            queryAllMedia = { listOf(item1, item2) },
            retryDelayMs = retryDelayMs,
        )

        dispatcher.onMediaChanged(sessionStartMs = 999_000L)

        verify(sessionTracker).addMedia(item1)
        verify(sessionTracker).addMedia(item2)
    }

    /**
     * Items passed to addMedia are the exact objects returned by the query.
     */
    @Test
    fun `onMediaChanged passes exact query results to sessionTracker`() {
        val captor = argumentCaptor<MediaItem>()
        val dispatcher = MediaChangeDispatcher(
            sessionTracker = sessionTracker,
            handler = handler,
            queryAllMedia = { listOf(item1) },
            retryDelayMs = retryDelayMs,
        )

        dispatcher.onMediaChanged(sessionStartMs = 0L)

        verify(sessionTracker).addMedia(captor.capture())
        assertEquals(item1.uri, captor.firstValue.uri)
    }

    // ── Always-retry path ───────────────────────────────────────────────────

    /**
     * A retry is always scheduled, even when the query returned items immediately.
     * This ensures IS_PENDING items (invisible on the first query) are captured once
     * committed.
     */
    @Test
    fun `onMediaChanged always schedules a retry`() {
        val dispatcher = MediaChangeDispatcher(
            sessionTracker = sessionTracker,
            handler = handler,
            queryAllMedia = { listOf(item1) },
            retryDelayMs = retryDelayMs,
        )

        dispatcher.onMediaChanged(sessionStartMs = 999_000L)

        verify(handler).postDelayed(any(), eq(retryDelayMs))
    }

    /**
     * A retry is also scheduled when the query returns empty (IS_PENDING race).
     */
    @Test
    fun `onMediaChanged schedules retry when query returns empty`() {
        val dispatcher = MediaChangeDispatcher(
            sessionTracker = sessionTracker,
            handler = handler,
            queryAllMedia = { emptyList() },
            retryDelayMs = retryDelayMs,
        )

        dispatcher.onMediaChanged(sessionStartMs = 999_000L)

        verify(sessionTracker, never()).addMedia(any())
        verify(handler).postDelayed(any(), eq(retryDelayMs))
    }

    /**
     * When the retry fires and new items are committed, they are added to the session.
     */
    @Test
    fun `retry runnable adds items when query succeeds on second attempt`() {
        var callCount = 0
        val dispatcher = MediaChangeDispatcher(
            sessionTracker = sessionTracker,
            handler = handler,
            queryAllMedia = { if (callCount++ == 0) emptyList() else listOf(item1) },
            retryDelayMs = retryDelayMs,
        )

        dispatcher.onMediaChanged(sessionStartMs = 999_000L)

        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(retryDelayMs))

        // Execute the retry runnable
        runnableCaptor.firstValue.run()

        verify(sessionTracker).addMedia(item1)
    }

    /**
     * When the retry fires and the query still returns empty (item never committed),
     * nothing is added to the session. No further retry is scheduled.
     */
    @Test
    fun `retry is one-shot - no further retry when second query also returns empty`() {
        val dispatcher = MediaChangeDispatcher(
            sessionTracker = sessionTracker,
            handler = handler,
            queryAllMedia = { emptyList() },
            retryDelayMs = retryDelayMs,
        )

        dispatcher.onMediaChanged(sessionStartMs = 999_000L)

        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(retryDelayMs))

        // Execute the retry runnable
        runnableCaptor.firstValue.run()

        verify(sessionTracker, never()).addMedia(any())
        // Only one postDelayed call (from onMediaChanged, not from the retry itself)
        verify(handler, times(1)).postDelayed(any(), any())
    }

    // ── Multiple-photo scenario ─────────────────────────────────────────────

    /**
     * When Photo2's IS_PENDING=1 onChange fires, Photo1 is already committed.
     * onMediaChanged returns [item1] immediately and schedules a retry.
     * The retry fires and finds both item1 and item2 committed — both are added
     * (dedup in SessionTracker prevents the item1 double-add in practice).
     */
    @Test
    fun `multiple photos - retry captures newly committed photo not found on initial call`() {
        // First call: only item1 is committed; item2 is still IS_PENDING
        // Retry: both item1 and item2 are committed
        var callCount = 0
        val dispatcher = MediaChangeDispatcher(
            sessionTracker = sessionTracker,
            handler = handler,
            queryAllMedia = { if (callCount++ == 0) listOf(item1) else listOf(item1, item2) },
            retryDelayMs = retryDelayMs,
        )

        dispatcher.onMediaChanged(sessionStartMs = 999_000L)

        // item1 added immediately
        verify(sessionTracker).addMedia(item1)

        // Retry is scheduled
        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(retryDelayMs))

        // Execute the retry — item1 and item2 both found (item1 is dedup'd by SessionTracker)
        runnableCaptor.firstValue.run()

        // item2 is added by retry
        verify(sessionTracker).addMedia(item2)
    }

    // ── Cancel-and-reschedule ───────────────────────────────────────────────

    /**
     * When a second onMediaChanged fires before the previous retry runs, the pending retry
     * is cancelled and a fresh one is scheduled. At most one retry is outstanding at a time.
     */
    @Test
    fun `rapid-fire photos cancel previous retry and reschedule fresh one`() {
        val dispatcher = MediaChangeDispatcher(
            sessionTracker = sessionTracker,
            handler = handler,
            queryAllMedia = { emptyList() },
            retryDelayMs = retryDelayMs,
        )

        dispatcher.onMediaChanged(sessionStartMs = 999_000L)

        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(retryDelayMs))
        val firstRetry = runnableCaptor.firstValue

        // Second onChange fires — should cancel first retry and post a new one
        dispatcher.onMediaChanged(sessionStartMs = 999_000L)

        verify(handler).removeCallbacks(firstRetry)
        // Two postDelayed calls total (one per onChange)
        verify(handler, times(2)).postDelayed(any(), eq(retryDelayMs))
    }

    // ── sessionStartMs forwarding ───────────────────────────────────────────

    /**
     * The sessionStartMs passed to onMediaChanged is forwarded to the query lambda on both
     * the initial call and the retry.
     */
    @Test
    fun `sessionStartMs is forwarded to queryAllMedia on initial call and retry`() {
        val capturedStartMs = mutableListOf<Long>()
        val dispatcher = MediaChangeDispatcher(
            sessionTracker = sessionTracker,
            handler = handler,
            queryAllMedia = { startMs ->
                capturedStartMs.add(startMs)
                emptyList()
            },
            retryDelayMs = retryDelayMs,
        )

        val expectedStartMs = 1_234_567L
        dispatcher.onMediaChanged(sessionStartMs = expectedStartMs)

        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), any())
        runnableCaptor.firstValue.run()

        assertEquals("Both calls should use the same sessionStartMs", 2, capturedStartMs.size)
        assertTrue(capturedStartMs.all { it == expectedStartMs })
    }
}
