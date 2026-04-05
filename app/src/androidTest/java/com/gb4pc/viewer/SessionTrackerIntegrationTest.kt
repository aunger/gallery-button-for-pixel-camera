package com.gb4pc.viewer

import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Integration tests for SessionTracker running on the device JVM.
 * Exercises the full production singleton to catch concurrency or logic bugs
 * that Robolectric unit tests might miss.
 */
@RunWith(AndroidJUnit4::class)
class SessionTrackerIntegrationTest {

    private val tracker = SessionTracker()   // fresh instance per test

    @Before
    fun startSession() {
        tracker.startSession()
    }

    @After
    fun endSession() {
        tracker.endSession()
    }

    @Test
    fun addMedia_itemAppearsInSession() {
        val item = MediaItem("content://media/1", dateTaken = 1000L, isVideo = false)
        tracker.addMedia(item)
        assertTrue(tracker.getSessionMedia().any { it.uri == "content://media/1" })
    }

    @Test
    fun addMedia_duplicateUri_isDeduped() {
        val item = MediaItem("content://media/2", dateTaken = 2000L, isVideo = false)
        tracker.addMedia(item)
        tracker.addMedia(item)  // second add must be a no-op
        assertEquals(1, tracker.getSessionMedia().count { it.uri == "content://media/2" })
    }

    @Test
    fun removeMedia_removesItemFromSession() {
        val item = MediaItem("content://media/3", dateTaken = 3000L, isVideo = false)
        tracker.addMedia(item)
        tracker.removeMedia("content://media/3")
        assertTrue(tracker.getSessionMedia().none { it.uri == "content://media/3" })
    }

    @Test
    fun getSessionMedia_returnsMostRecentFirst() {
        val older = MediaItem("content://media/4", dateTaken = 1000L, isVideo = false)
        val newer = MediaItem("content://media/5", dateTaken = 9000L, isVideo = false)
        tracker.addMedia(older)
        tracker.addMedia(newer)
        val result = tracker.getSessionMedia()
        assertEquals("content://media/5", result[0].uri)
        assertEquals("content://media/4", result[1].uri)
    }

    @Test
    fun endSession_clearsAllMedia() {
        tracker.addMedia(MediaItem("content://media/6", 1000L, false))
        tracker.endSession()
        assertTrue(tracker.getSessionMedia().isEmpty())
    }

    @Test
    fun addMedia_whenSessionNotActive_isIgnored() {
        tracker.endSession()  // deactivate
        tracker.addMedia(MediaItem("content://media/7", 1000L, false))
        assertTrue(tracker.getSessionMedia().isEmpty())
    }

    @Test
    fun concurrentAddMedia_noDuplicates() {
        // Fire 20 threads each trying to add the same URI
        val threads = (1..20).map {
            Thread {
                tracker.addMedia(MediaItem("content://media/concurrent", 1000L, false))
            }
        }
        threads.forEach { it.start() }
        threads.forEach { it.join() }
        assertEquals(1, tracker.getSessionMedia().count { it.uri == "content://media/concurrent" })
    }
}
