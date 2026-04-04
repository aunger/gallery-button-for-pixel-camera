package com.gb4pc.service

import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.CyclicBarrier
import java.util.concurrent.atomic.AtomicInteger

/**
 * Verifies CameraState is thread-safe under concurrent access (C1 fix).
 */
class CameraStateThreadSafetyTest {

    @Test
    fun `concurrent add and remove does not throw ConcurrentModificationException`() {
        val state = CameraState()
        val threadCount = 10
        val iterations = 1000
        val barrier = CyclicBarrier(threadCount)
        val errors = AtomicInteger(0)
        val latch = CountDownLatch(threadCount)

        val threads = (0 until threadCount).map { threadId ->
            Thread {
                try {
                    barrier.await()
                    for (i in 0 until iterations) {
                        val cameraId = "${threadId}_$i"
                        state.setCameraUnavailable(cameraId)
                        state.anyCameraUnavailable()
                        state.areAllCamerasAvailable()
                        state.getUnavailableCameraIds()
                        state.setCameraAvailable(cameraId)
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
        assertTrue("All cameras should be available after test", state.areAllCamerasAvailable())
    }
}
