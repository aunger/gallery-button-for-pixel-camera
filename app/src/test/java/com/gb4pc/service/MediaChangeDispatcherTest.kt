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
 * Covers the IS_PENDING race: when a photo is first inserted into MediaStore with
 * IS_PENDING=1 the default query returns null; a retry is scheduled so the item is
 * captured once IS_PENDING is cleared. Also verifies that when the item is already
 * committed on the first query no retry is scheduled.
 */
class MediaChangeDispatcherTest {

    private lateinit var sessionTracker: SessionTracker
    private lateinit var handler: Handler
    private val retryDelayMs = 500L

    private val sampleItem = MediaItem(
        uri = "content://media/external/images/media/42",
        dateTaken = 1_000_000L,
        isVideo = false
    )

    @Before
    fun setUp() {
        sessionTracker = mock()
        handler = mock()
    }

    // ── Immediate-hit path ──────────────────────────────────────────────────

    /**
     * When the query immediately finds a committed item, it is added to the session
     * and no retry is scheduled.
     */
    @Test
    fun `onMediaChanged adds item immediately when query succeeds`() {
        val dispatcher = MediaChangeDispatcher(
            sessionTracker = sessionTracker,
            handler = handler,
            queryLatestMedia = { sampleItem },
            retryDelayMs = retryDelayMs,
        )

        dispatcher.onMediaChanged(sessionStartMs = 999_000L)

        verify(sessionTracker).addMedia(sampleItem)
        verify(handler, never()).postDelayed(any(), any())
    }

    /**
     * The item passed to addMedia is the one returned by the query, not a copy.
     */
    @Test
    fun `onMediaChanged passes exact query result to sessionTracker`() {
        val captor = argumentCaptor<MediaItem>()
        val dispatcher = MediaChangeDispatcher(
            sessionTracker = sessionTracker,
            handler = handler,
            queryLatestMedia = { sampleItem },
            retryDelayMs = retryDelayMs,
        )

        dispatcher.onMediaChanged(sessionStartMs = 0L)

        verify(sessionTracker).addMedia(captor.capture())
        assertEquals(sampleItem.uri, captor.firstValue.uri)
    }

    // ── IS_PENDING retry path ───────────────────────────────────────────────

    /**
     * When the first query returns null (item is IS_PENDING), a retry runnable is
     * scheduled via handler.postDelayed with the configured retryDelayMs.
     */
    @Test
    fun `onMediaChanged schedules retry when first query returns null`() {
        val dispatcher = MediaChangeDispatcher(
            sessionTracker = sessionTracker,
            handler = handler,
            queryLatestMedia = { null },
            retryDelayMs = retryDelayMs,
        )

        dispatcher.onMediaChanged(sessionStartMs = 999_000L)

        verify(sessionTracker, never()).addMedia(any())
        verify(handler).postDelayed(any(), eq(retryDelayMs))
    }

    /**
     * When the retry fires and the item has been committed (query returns non-null),
     * it is added to the session.
     */
    @Test
    fun `retry runnable adds item when query succeeds on second attempt`() {
        var callCount = 0
        val dispatcher = MediaChangeDispatcher(
            sessionTracker = sessionTracker,
            handler = handler,
            queryLatestMedia = { if (callCount++ == 0) null else sampleItem },
            retryDelayMs = retryDelayMs,
        )

        dispatcher.onMediaChanged(sessionStartMs = 999_000L)

        // Nothing added yet; a retry was posted
        verify(sessionTracker, never()).addMedia(any())
        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(retryDelayMs))

        // Execute the retry runnable
        runnableCaptor.firstValue.run()

        verify(sessionTracker).addMedia(sampleItem)
    }

    /**
     * When the retry fires but the query STILL returns null (photo was deleted or
     * never committed), nothing is added to the session and no further retry is
     * scheduled — the retry is strictly one-shot.
     */
    @Test
    fun `retry is one-shot - no further retry when second query also returns null`() {
        val dispatcher = MediaChangeDispatcher(
            sessionTracker = sessionTracker,
            handler = handler,
            queryLatestMedia = { null },
            retryDelayMs = retryDelayMs,
        )

        dispatcher.onMediaChanged(sessionStartMs = 999_000L)

        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(retryDelayMs))

        // Execute the retry runnable
        runnableCaptor.firstValue.run()

        // No item added; no further postDelayed beyond the original one
        verify(sessionTracker, never()).addMedia(any())
        verify(handler, times(1)).postDelayed(any(), any())
    }

    /**
     * Two rapid media changes: first fires while item is pending (null result →
     * retry scheduled), second fires after item is committed (non-null → item added
     * immediately). The retry still fires but deduplication in SessionTracker
     * prevents double-adding (by design in addMedia, not tested here since we
     * use a mock tracker).
     */
    @Test
    fun `two onChange calls for same photo - first schedules retry, second adds immediately`() {
        var callCount = 0
        val dispatcher = MediaChangeDispatcher(
            sessionTracker = sessionTracker,
            handler = handler,
            queryLatestMedia = { if (callCount++ == 0) null else sampleItem },
            retryDelayMs = retryDelayMs,
        )

        // First change: item is pending
        dispatcher.onMediaChanged(sessionStartMs = 999_000L)
        verify(handler, times(1)).postDelayed(any(), eq(retryDelayMs))
        verify(sessionTracker, never()).addMedia(any())

        // Second change: item is now committed
        dispatcher.onMediaChanged(sessionStartMs = 999_000L)
        verify(sessionTracker, times(1)).addMedia(sampleItem)
        // No additional retry posted
        verify(handler, times(1)).postDelayed(any(), any())
    }

    // ── Session start parameter is forwarded correctly ───────────────────────

    /**
     * The sessionStartMs value passed to onMediaChanged is forwarded to the query
     * lambda on both the initial call and the retry.
     */
    @Test
    fun `sessionStartMs is forwarded to queryLatestMedia on initial call and retry`() {
        val capturedStartMs = mutableListOf<Long>()
        val dispatcher = MediaChangeDispatcher(
            sessionTracker = sessionTracker,
            handler = handler,
            queryLatestMedia = { startMs ->
                capturedStartMs.add(startMs)
                null
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
