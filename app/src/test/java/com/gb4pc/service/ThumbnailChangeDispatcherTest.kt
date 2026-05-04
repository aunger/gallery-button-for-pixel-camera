package com.gb4pc.service

import android.os.Handler
import com.gb4pc.viewer.MediaItem
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.mockito.kotlin.*

/**
 * Unit tests for [ThumbnailChangeDispatcher].
 *
 * Covers:
 * - Immediate-hit path: showThumbnail is called right away when a committed item is found.
 * - Always-retry: a retry is always scheduled after every onChange, even when an item was
 *   found immediately. This ensures that when a new photo is IS_PENDING, the query returns
 *   the previous photo but the retry fires 500ms later and shows the newly committed photo.
 * - Cancel-and-reschedule: rapid-fire photos replace any pending retry with a fresh one,
 *   keeping at most one outstanding runnable at any time.
 * - startMs forwarding.
 */
class ThumbnailChangeDispatcherTest {

    private lateinit var handler: Handler
    private lateinit var showThumbnail: (String) -> Unit
    private val retryDelayMs = 500L

    private val sampleItem = MediaItem(
        uri = "content://media/external/images/media/42",
        dateTaken = 1_000_000L,
        isVideo = false
    )

    @Before
    fun setUp() {
        handler = mock()
        showThumbnail = mock()
    }

    // ── Immediate-hit path ──────────────────────────────────────────────────

    /**
     * When the query immediately finds a committed item, showThumbnail is called.
     */
    @Test
    fun `onThumbnailChanged shows thumbnail immediately when query succeeds`() {
        val dispatcher = ThumbnailChangeDispatcher(
            handler = handler,
            queryLatestMedia = { sampleItem },
            showThumbnail = showThumbnail,
            retryDelayMs = retryDelayMs,
        )

        dispatcher.onThumbnailChanged(startMs = 999_000L)

        verify(showThumbnail).invoke(sampleItem.uri)
    }

    /**
     * The URI passed to showThumbnail is the one from the query result.
     */
    @Test
    fun `onThumbnailChanged passes exact query result URI to showThumbnail`() {
        val uriCaptor = argumentCaptor<String>()
        val dispatcher = ThumbnailChangeDispatcher(
            handler = handler,
            queryLatestMedia = { sampleItem },
            showThumbnail = showThumbnail,
            retryDelayMs = retryDelayMs,
        )

        dispatcher.onThumbnailChanged(startMs = 0L)

        verify(showThumbnail).invoke(uriCaptor.capture())
        assertEquals(sampleItem.uri, uriCaptor.firstValue)
    }

    // ── Always-retry path ───────────────────────────────────────────────────

    /**
     * A retry is always scheduled, even when the query found an item immediately.
     * This ensures the overlay shows the newest photo even when the initial onChange
     * fired while the new photo was IS_PENDING (the query returns the previous photo).
     */
    @Test
    fun `onThumbnailChanged always schedules a retry`() {
        val dispatcher = ThumbnailChangeDispatcher(
            handler = handler,
            queryLatestMedia = { sampleItem },
            showThumbnail = showThumbnail,
            retryDelayMs = retryDelayMs,
        )

        dispatcher.onThumbnailChanged(startMs = 999_000L)

        verify(handler).postDelayed(any(), eq(retryDelayMs))
    }

    /**
     * A retry is also scheduled when the query returns null.
     */
    @Test
    fun `onThumbnailChanged schedules retry when query returns null`() {
        val dispatcher = ThumbnailChangeDispatcher(
            handler = handler,
            queryLatestMedia = { null },
            showThumbnail = showThumbnail,
            retryDelayMs = retryDelayMs,
        )

        dispatcher.onThumbnailChanged(startMs = 999_000L)

        verify(showThumbnail, never()).invoke(any())
        verify(handler).postDelayed(any(), eq(retryDelayMs))
    }

    /**
     * When the retry fires and the item has been committed, showThumbnail is called.
     */
    @Test
    fun `retry runnable shows thumbnail when query succeeds on second attempt`() {
        var callCount = 0
        val dispatcher = ThumbnailChangeDispatcher(
            handler = handler,
            queryLatestMedia = { if (callCount++ == 0) null else sampleItem },
            showThumbnail = showThumbnail,
            retryDelayMs = retryDelayMs,
        )

        dispatcher.onThumbnailChanged(startMs = 999_000L)

        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(retryDelayMs))

        // Execute the retry runnable
        runnableCaptor.firstValue.run()

        verify(showThumbnail).invoke(sampleItem.uri)
    }

    /**
     * When the retry fires but the query still returns null, showThumbnail is not called.
     * No further retry is scheduled — the retry is one-shot.
     */
    @Test
    fun `retry is one-shot - no further retry when second query also returns null`() {
        val dispatcher = ThumbnailChangeDispatcher(
            handler = handler,
            queryLatestMedia = { null },
            showThumbnail = showThumbnail,
            retryDelayMs = retryDelayMs,
        )

        dispatcher.onThumbnailChanged(startMs = 999_000L)

        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(retryDelayMs))

        // Execute the retry runnable
        runnableCaptor.firstValue.run()

        verify(showThumbnail, never()).invoke(any())
        // Only one postDelayed call (from onThumbnailChanged, not the retry itself)
        verify(handler, times(1)).postDelayed(any(), any())
    }

    // ── Cancel-and-reschedule ───────────────────────────────────────────────

    /**
     * When a second onThumbnailChanged fires before the previous retry runs, the pending
     * retry is cancelled and a fresh one is scheduled. At most one retry is outstanding.
     */
    @Test
    fun `rapid-fire photos cancel previous retry and reschedule fresh one`() {
        val dispatcher = ThumbnailChangeDispatcher(
            handler = handler,
            queryLatestMedia = { null },
            showThumbnail = showThumbnail,
            retryDelayMs = retryDelayMs,
        )

        dispatcher.onThumbnailChanged(startMs = 999_000L)

        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(retryDelayMs))
        val firstRetry = runnableCaptor.firstValue

        // Second onChange fires — should cancel first retry and post a new one
        dispatcher.onThumbnailChanged(startMs = 999_000L)

        verify(handler).removeCallbacks(firstRetry)
        // Two postDelayed calls total (one per onThumbnailChanged)
        verify(handler, times(2)).postDelayed(any(), eq(retryDelayMs))
    }

    // ── startMs forwarding ──────────────────────────────────────────────────

    /**
     * The startMs value passed to onThumbnailChanged is forwarded to the query lambda on
     * both the initial call and the retry.
     */
    @Test
    fun `startMs is forwarded to queryLatestMedia on initial call and retry`() {
        val capturedStartMs = mutableListOf<Long>()
        val dispatcher = ThumbnailChangeDispatcher(
            handler = handler,
            queryLatestMedia = { startMs ->
                capturedStartMs.add(startMs)
                null
            },
            showThumbnail = showThumbnail,
            retryDelayMs = retryDelayMs,
        )

        val expectedStartMs = 1_234_567L
        dispatcher.onThumbnailChanged(startMs = expectedStartMs)

        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), any())
        runnableCaptor.firstValue.run()

        assertEquals("Both calls should use the same startMs", 2, capturedStartMs.size)
        assertTrue(capturedStartMs.all { it == expectedStartMs })
    }
}
