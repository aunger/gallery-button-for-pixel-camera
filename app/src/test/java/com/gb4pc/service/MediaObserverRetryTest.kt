package com.gb4pc.service

import android.os.Handler
import com.gb4pc.Constants
import com.gb4pc.viewer.MediaItem
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.mockito.kotlin.any
import org.mockito.kotlin.argumentCaptor
import org.mockito.kotlin.atLeastOnce
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
                isSuccess = { it != 0 },
                handleResult = { result, isRetry -> seen.add(result to isRetry) },
                retryDelayMs = retryDelayMs,
            )

        retry.onChange(startMs = 999_000L)

        assertEquals(listOf(42 to false), seen)
    }

    // ── Success-gated retry path ────────────────────────────────────────────

    @Test
    fun `onChange schedules a retry when the initial result is unsuccessful`() {
        val retry =
            MediaObserverRetry<Int>(
                handler = handler,
                query = { 0 },
                isSuccess = { it != 0 },
                handleResult = { _, _ -> },
                retryDelayMs = retryDelayMs,
            )

        retry.onChange(startMs = 999_000L)

        verify(handler).postDelayed(any(), eq(retryDelayMs))
    }

    @Test
    fun `onChange does not schedule a retry when the initial result is successful`() {
        val retry =
            MediaObserverRetry<Int>(
                handler = handler,
                query = { 1 },
                isSuccess = { it != 0 },
                handleResult = { _, _ -> },
                retryDelayMs = retryDelayMs,
            )

        retry.onChange(startMs = 999_000L)

        verify(handler, never()).postDelayed(any(), any())
    }

    @Test
    fun `retry runnable invokes handleResult with isRetry=true`() {
        var callCount = 0
        val seen = mutableListOf<Pair<Int, Boolean>>()
        val retry =
            MediaObserverRetry<Int>(
                handler = handler,
                // 0 (unsuccessful) then 1 (successful), so exactly one retry fires.
                query = { callCount++ },
                isSuccess = { it != 0 },
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
    fun `unsuccessful retry re-schedules itself`() {
        val retry =
            MediaObserverRetry<Int>(
                handler = handler,
                query = { 0 }, // always unsuccessful
                isSuccess = { it != 0 },
                handleResult = { _, _ -> },
                retryDelayMs = retryDelayMs,
            )

        retry.onChange(startMs = 999_000L)
        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(retryDelayMs))
        runnableCaptor.firstValue.run()

        // The retry, still unsuccessful, enqueues another retry.
        verify(handler, times(2)).postDelayed(any(), eq(retryDelayMs))
    }

    @Test
    fun `successful retry stops the chain`() {
        var callCount = 0
        val retry =
            MediaObserverRetry<Int>(
                handler = handler,
                // 0 (unsuccessful), then 1 (successful).
                query = { callCount++ },
                isSuccess = { it != 0 },
                handleResult = { _, _ -> },
                retryDelayMs = retryDelayMs,
            )

        retry.onChange(startMs = 999_000L)
        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(retryDelayMs))
        runnableCaptor.firstValue.run()

        // Retry succeeded, so no further retry is scheduled.
        verify(handler, times(1)).postDelayed(any(), eq(retryDelayMs))
    }

    @Test
    fun `retry chain stops after MEDIA_OBSERVER_RETRY_MAX_ATTEMPTS`() {
        val retry =
            MediaObserverRetry<Int>(
                handler = handler,
                query = { 0 }, // always unsuccessful
                isSuccess = { it != 0 },
                handleResult = { _, _ -> },
                retryDelayMs = retryDelayMs,
            )

        retry.onChange(startMs = 999_000L)
        drainRetries()

        // Initial onChange + exactly MAX_ATTEMPTS retries, then the chain gives up.
        verify(handler, times(Constants.MEDIA_OBSERVER_RETRY_MAX_ATTEMPTS))
            .postDelayed(any(), eq(retryDelayMs))
    }

    @Test
    fun `regression issue 509 - thumbnail refreshes when commit lands after the first retry`() {
        // Reproduces #509: the query returns null on the initial call and on the first retry,
        // then a committed item on the second retry. With a one-shot retry the thumbnail would
        // never refresh; the self-rescheduling retry must still surface it.
        val sample = MediaItem(uri = "content://509", dateTaken = 1L, isVideo = false)
        var callCount = 0
        val shown = mutableListOf<String>()
        val retry =
            MediaObserverRetry<MediaItem?>(
                handler = handler,
                query = { if (callCount++ < 2) null else sample },
                isSuccess = { it != null },
                handleResult = { item, _ -> item?.let { shown.add(it.uri) } },
                retryDelayMs = retryDelayMs,
            )

        retry.onChange(startMs = 999_000L)
        drainRetries()

        assertEquals(listOf("content://509"), shown)
    }

    // ── Cancel-and-reschedule ───────────────────────────────────────────────

    @Test
    fun `rapid-fire onChange cancels previous retry and reschedules`() {
        val retry =
            MediaObserverRetry<Int>(
                handler = handler,
                query = { 0 },
                isSuccess = { it != 0 },
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

    @Test
    fun `fresh onChange resets the attempt budget`() {
        val retry =
            MediaObserverRetry<Int>(
                handler = handler,
                query = { 0 }, // always unsuccessful
                isSuccess = { it != 0 },
                handleResult = { _, _ -> },
                retryDelayMs = retryDelayMs,
            )

        // Exhaust the budget on the first event.
        retry.onChange(startMs = 999_000L)
        drainRetries()
        verify(handler, times(Constants.MEDIA_OBSERVER_RETRY_MAX_ATTEMPTS))
            .postDelayed(any(), eq(retryDelayMs))

        // A fresh onChange resets the counter and schedules a full new budget.
        retry.onChange(startMs = 999_000L)
        drainRetries()
        verify(handler, times(2 * Constants.MEDIA_OBSERVER_RETRY_MAX_ATTEMPTS))
            .postDelayed(any(), eq(retryDelayMs))
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
                isSuccess = { it != 0 },
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
     *
     * The session observer uses isSuccess = { false } so the retry always fires, even when the
     * session already contains earlier photos. A non-empty list from stale items must not mask
     * the still-pending new shot.
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
                // Call 0: nothing committed yet (IS_PENDING race); call 1 (retry): both committed.
                query = { if (callCount++ == 0) emptyList() else listOf(item1, item2) },
                isSuccess = { false },
                handleResult = { items, _ -> items.forEach { added.add(it.uri) } },
                retryDelayMs = retryDelayMs,
            )

        retry.onChange(startMs = 999_000L)
        val runnableCaptor = argumentCaptor<Runnable>()
        verify(handler).postDelayed(runnableCaptor.capture(), eq(retryDelayMs))
        runnableCaptor.firstValue.run()

        // Nothing added on the empty initial call; item1 + item2 added on the retry (de-dup is
        // the SessionTracker's job, not this helper's).
        assertEquals(listOf("content://1", "content://2"), added)
    }

    /**
     * Regression for the blocking review finding: when the session already holds a prior photo,
     * the query returns a non-empty list on the initial call even though the new shot is still
     * IS_PENDING. isSuccess = { false } must still schedule a retry so the new item is captured.
     */
    @Test
    fun `list-result - retry fires even when initial result is non-empty (prior session media)`() {
        val prior = MediaItem(uri = "content://prior", dateTaken = 1L, isVideo = false)
        val newItem = MediaItem(uri = "content://new", dateTaken = 2L, isVideo = false)
        var callCount = 0
        val added = mutableListOf<String>()

        val retry =
            MediaObserverRetry<List<MediaItem>>(
                handler = handler,
                // Call 0: only prior item committed (new shot still IS_PENDING).
                // Call 1 (retry): both are committed.
                query = { if (callCount++ == 0) listOf(prior) else listOf(prior, newItem) },
                isSuccess = { false },
                handleResult = { items, _ -> items.forEach { added.add(it.uri) } },
                retryDelayMs = retryDelayMs,
            )

        retry.onChange(startMs = 999_000L)
        val runnableCaptor = argumentCaptor<Runnable>()
        // Retry must be scheduled even though the initial result was non-empty.
        verify(handler).postDelayed(runnableCaptor.capture(), eq(retryDelayMs))
        runnableCaptor.firstValue.run()

        // Both the prior item (initial) and both items (retry) are delivered; de-dup is
        // the SessionTracker's job, not this helper's.
        assertEquals(listOf("content://prior", "content://prior", "content://new"), added)
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
                isSuccess = { it != null },
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

    // ── Helpers ─────────────────────────────────────────────────────────────

    /**
     * Drives the self-rescheduling retry chain to completion. Each fired retry that is still
     * unsuccessful schedules the next one via a new handler.postDelayed(). This captures every
     * posted runnable and runs the latest one until a step posts nothing new. Bounded so a
     * non-terminating chain fails the test rather than hanging.
     */
    private fun drainRetries() {
        var total = 0
        while (true) {
            val captor = argumentCaptor<Runnable>()
            verify(handler, atLeastOnce()).postDelayed(captor.capture(), eq(retryDelayMs))
            if (captor.allValues.size <= total) break // no new retry posted -> chain terminated
            captor.allValues.last().run()
            total = captor.allValues.size
            assertTrue(
                "Retry chain did not terminate within a bounded number of attempts",
                total <= Constants.MEDIA_OBSERVER_RETRY_MAX_ATTEMPTS * 4,
            )
        }
    }
}
