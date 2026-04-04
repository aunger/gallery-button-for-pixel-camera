package com.gb4pc.viewer

import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.CyclicBarrier
import java.util.concurrent.atomic.AtomicInteger

/**
 * Verifies SessionTracker is thread-safe under concurrent access (C2 fix).
 */
class SessionTrackerThreadSafetyTest {

    @Test
    fun `concurrent add and read does not throw ConcurrentModificationException`() {
        val tracker = SessionTracker()
        tracker.startSession()

        val threadCount = 10
        val iterations = 200
        val barrier = CyclicBarrier(threadCount)
        val errors = AtomicInteger(0)
        val latch = CountDownLatch(threadCount)

        val threads = (0 until threadCount).map { threadId ->
            Thread {
                try {
                    barrier.await()
                    for (i in 0 until iterations) {
                        val item = MediaItem(
                            uri = "content://media/${threadId}_$i",
                            dateTaken = System.currentTimeMillis(),
                            isVideo = false
                        )
                        tracker.addMedia(item)
                        tracker.getSessionMedia()
                        tracker.isMediaInSession(System.currentTimeMillis(), "DCIM/Camera/test.jpg")
                    }
                } catch (e: Exception) {
                    errors.incrementAndGet()
                } finally {
                    latch.countDown()
                }
            }
        }

        threads.forEach { it.start() }
        latch.await()

        assertEquals("Expected no errors from concurrent access", 0, errors.get())
        // All items should have been added
        val media = tracker.getSessionMedia()
        assertEquals(threadCount * iterations, media.size)
    }

    @Test
    fun `concurrent add and remove does not throw`() {
        val tracker = SessionTracker()
        tracker.startSession()

        val threadCount = 4
        val iterations = 100
        val barrier = CyclicBarrier(threadCount)
        val errors = AtomicInteger(0)
        val latch = CountDownLatch(threadCount)

        // Half threads add, half threads remove
        val threads = (0 until threadCount).map { threadId ->
            Thread {
                try {
                    barrier.await()
                    for (i in 0 until iterations) {
                        if (threadId % 2 == 0) {
                            tracker.addMedia(MediaItem(
                                uri = "content://media/${threadId}_$i",
                                dateTaken = System.currentTimeMillis(),
                                isVideo = false
                            ))
                        } else {
                            tracker.removeMedia("content://media/${threadId - 1}_$i")
                            tracker.getSessionMedia() // concurrent read
                        }
                    }
                } catch (e: Exception) {
                    errors.incrementAndGet()
                } finally {
                    latch.countDown()
                }
            }
        }

        threads.forEach { it.start() }
        latch.await()

        assertEquals("Expected no errors from concurrent access", 0, errors.get())
    }
}
