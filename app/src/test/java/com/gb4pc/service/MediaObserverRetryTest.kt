package com.gb4pc.service

import android.os.Handler
import com.gb4pc.viewer.MediaItem
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.mockito.kotlin.any
import org.mockito.kotlin.argumentCaptor
import org.mockito.kotlin.eq
import org.mockito.kotlin.mock
import org.mockito.kotlin.never
import org.mockito.kotlin.times
import org.mockito.kotlin.verify

/**
 * Unit tests for [MediaObserverRetry] — the generic ContentObserver onChange + retry
 * scaffold previously duplicated across MediaChangeDispatcher and ThumbnailChangeDispatcher.
 *
 * Tests use `Int` results to keep type plumbing minimal, plus a couple of integration
 * checks at the end that wire up the two original concrete shapes (`List<MediaItem>` for
 * the session observer and `MediaItem?` for the thumbnail observer) to confirm the helper
 * still satisfies both call sites.
 */
class MediaObserverRetryTest {
    private lateinit var handler: Handler
    private val retryDelayMs = 500L

    @Before
    fun setUp() {
        handler = mock()
    }

    // ── Immediate-hit path ──────────────────────────────────────────────────

    @Test
    fun `onChange invokes handleResult immediately with isRetry=false`() {
        val seen = mutableListOf<Pair<Int, Boolean>>()
        val retry =
            MediaObserverRetry<Int>(
                handler = handler,
                query = { 42 },
                handleResult = { result, isRetry -> seen.add(result to isRetry) },
                retryDelayMs = retryDelayMs,
            )

        retry.onChange(startMs = 999_000L)

        assertEquals(listOf(42 to false), seen)
    }

    // ── Always-retry path ───────────────────────────────────────────────────

    @Test
    fun `onChange always schedules a retry`() {
        val retry =
            MediaObserverRetry<Int>(
                handler = handler,
                query = { 1 },
                handleResult = { _, _ -> },
                retryDelayMs = retryDelayMs,
            )

        retry.onChange(startMs = 999_000L)

        verify(handler).postDelayed(any(), eq(retryDelayMs))
    }

    @Test
    fun `retry runnable invokes handleResult with isRetry=true`() {
        var callCount = 0
        val seen = mutableListOf<Pair<Int, Boolean>>()
        val retry =
            MediaObserverRetry<Int>(
                handler = handler,
                query = { callCount++ },
                handleResult = { result, isRetry -> seen.add(result to isRetry) },
                retryDelayMs = retryDelayMs,
            )

        retry.onChange(startMs = 999_000L)
        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(retryDelayMs))
        runnableCaptor.firstValue.run()

        assertEquals(listOf(0 to false, 1 to true), seen)
    }

    @Test
    fun `retry is one-shot - retry runnable does not schedule another retry`() {
        val retry =
            MediaObserverRetry<Int>(
                handler = handler,
                query = { 0 },
                handleResult = { _, _ -> },
                retryDelayMs = retryDelayMs,
            )

        retry.onChange(startMs = 999_000L)
        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(retryDelayMs))
        runnableCaptor.firstValue.run()

        // Only the original postDelayed call; the retry runnable does not enqueue another.
        verify(handler, times(1)).postDelayed(any(), any())
    }

    // ── Cancel-and-reschedule ───────────────────────────────────────────────

    @Test
    fun `rapid-fire onChange cancels previous retry and reschedules`() {
        val retry =
            MediaObserverRetry<Int>(
                handler = handler,
                query = { 0 },
                handleResult = { _, _ -> },
                retryDelayMs = retryDelayMs,
            )

        retry.onChange(startMs = 999_000L)
        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(retryDelayMs))
        val firstRetry = runnableCaptor.firstValue

        retry.onChange(startMs = 999_000L)

        verify(handler).removeCallbacks(firstRetry)
        verify(handler, times(2)).postDelayed(any(), eq(retryDelayMs))
    }

    // ── startMs forwarding ──────────────────────────────────────────────────

    @Test
    fun `startMs is forwarded to query on initial call and retry`() {
        val capturedStartMs = mutableListOf<Long>()
        val retry =
            MediaObserverRetry<Int>(
                handler = handler,
                query = { startMs ->
                    capturedStartMs.add(startMs)
                    0
                },
                handleResult = { _, _ -> },
                retryDelayMs = retryDelayMs,
            )

        val expectedStartMs = 1_234_567L
        retry.onChange(startMs = expectedStartMs)

        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), any())
        runnableCaptor.firstValue.run()

        assertEquals("Both calls should use the same startMs", 2, capturedStartMs.size)
        assertTrue(capturedStartMs.all { it == expectedStartMs })
    }

    // ── Wiring checks for the two original call sites ───────────────────────

    /**
     * Mirrors how OverlayService wires the session-media observer (List<MediaItem> result).
     * Previously covered by MediaChangeDispatcherTest.
     */
    @Test
    fun `list-result wiring matches former MediaChangeDispatcher contract`() {
        val item1 = MediaItem(uri = "content://1", dateTaken = 1L, isVideo = false)
        val item2 = MediaItem(uri = "content://2", dateTaken = 2L, isVideo = false)
        var callCount = 0
        val added = mutableListOf<String>()

        val retry =
            MediaObserverRetry<List<MediaItem>>(
                handler = handler,
                // Call 0: only item1 committed; call 1 (retry): both committed.
                query = { if (callCount++ == 0) listOf(item1) else listOf(item1, item2) },
                handleResult = { items, _ -> items.forEach { added.add(it.uri) } },
                retryDelayMs = retryDelayMs,
            )

        retry.onChange(startMs = 999_000L)
        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(retryDelayMs))
        runnableCaptor.firstValue.run()

        // item1 added on initial call; item1 + item2 added on retry (de-dup is the
        // SessionTracker's job, not this helper's).
        assertEquals(listOf("content://1", "content://1", "content://2"), added)
    }

    /**
     * Mirrors how OverlayService wires the thumbnail observer (MediaItem? result).
     * Previously covered by ThumbnailChangeDispatcherTest.
     */
    @Test
    fun `nullable-result wiring matches former ThumbnailChangeDispatcher contract`() {
        val sample = MediaItem(uri = "content://42", dateTaken = 1L, isVideo = false)
        var callCount = 0
        val shown = mutableListOf<String>()
        val showThumbnail: (String) -> Unit = mock()

        val retry =
            MediaObserverRetry<MediaItem?>(
                handler = handler,
                // Call 0: still IS_PENDING (null); call 1 (retry): committed.
                query = { if (callCount++ == 0) null else sample },
                handleResult = { item, _ ->
                    if (item != null) {
                        showThumbnail(item.uri)
                        shown.add(item.uri)
                    }
                },
                retryDelayMs = retryDelayMs,
            )

        retry.onChange(startMs = 999_000L)
        // showThumbnail not yet called — initial query returned null.
        verify(showThumbnail, never()).invoke(any())

        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(retryDelayMs))
        runnableCaptor.firstValue.run()

        assertEquals(listOf("content://42"), shown)
        verify(showThumbnail).invoke(sample.uri)
    }
}
