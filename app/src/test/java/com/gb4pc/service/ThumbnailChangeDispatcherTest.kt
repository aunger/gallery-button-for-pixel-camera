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
 * Covers the IS_PENDING retry: when a photo is still IS_PENDING the thumbnail query
 * returns null; a retry is scheduled so the overlay thumbnail is updated once the
 * item is committed. Also verifies that when the item is already committed on the
 * first query no retry is scheduled.
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
     * When the query immediately finds a committed item, showThumbnail is called and
     * no retry is scheduled.
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
        verify(handler, never()).postDelayed(any(), any())
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

    // ── IS_PENDING retry path ───────────────────────────────────────────────

    /**
     * When the first query returns null (item is IS_PENDING), a retry runnable is
     * scheduled via handler.postDelayed with the configured retryDelayMs.
     */
    @Test
    fun `onThumbnailChanged schedules retry when first query returns null`() {
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
     * When the retry fires and the item has been committed (query returns non-null),
     * showThumbnail is called with the item URI.
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

        // Nothing shown yet; a retry was posted
        verify(showThumbnail, never()).invoke(any())
        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(retryDelayMs))

        // Execute the retry runnable
        runnableCaptor.firstValue.run()

        verify(showThumbnail).invoke(sampleItem.uri)
    }

    /**
     * When the retry fires but the query STILL returns null (photo was deleted or
     * never committed), showThumbnail is not called and no further retry is
     * scheduled — the retry is strictly one-shot.
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

        // No thumbnail shown; no further postDelayed beyond the original one
        verify(showThumbnail, never()).invoke(any())
        verify(handler, times(1)).postDelayed(any(), any())
    }

    /**
     * The startMs value passed to onThumbnailChanged is forwarded to the query
     * lambda on both the initial call and the retry.
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
