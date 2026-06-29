package com.gb4pc.viewer

import com.gb4pc.Constants
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

class SessionTrackerTest {
    private lateinit var tracker: SessionTracker

    @Before
    fun setUp() {
        tracker = SessionTracker()
    }

    @Test
    fun `initially no active session`() {
        assertFalse(tracker.isSessionActive)
    }

    @Test
    fun `startSession sets active and records timestamp`() {
        val before = System.currentTimeMillis()
        tracker.startSession()
        val after = System.currentTimeMillis()

        assertTrue(tracker.isSessionActive)
        assertTrue(tracker.sessionStartTimestamp in before..after)
    }

    @Test
    fun `endSession clears active state and media list`() {
        tracker.startSession()
        tracker.addMedia(MediaItem(uri = "content://media/1", dateTaken = System.currentTimeMillis(), isVideo = false))
        tracker.endSession()

        assertFalse(tracker.isSessionActive)
        assertTrue(tracker.getSessionMedia().isEmpty())
    }

    @Test
    fun `addMedia adds to session list`() {
        tracker.startSession()
        val item = MediaItem(uri = "content://media/1", dateTaken = System.currentTimeMillis(), isVideo = false)
        tracker.addMedia(item)

        assertEquals(1, tracker.getSessionMedia().size)
        assertEquals("content://media/1", tracker.getSessionMedia()[0].uri)
    }

    @Test
    fun `addMedia ignores items when no active session`() {
        val item = MediaItem(uri = "content://media/1", dateTaken = System.currentTimeMillis(), isVideo = false)
        tracker.addMedia(item)
        assertTrue(tracker.getSessionMedia().isEmpty())
    }

    @Test
    fun `isMediaInSession checks timestamp with tolerance per SF-04`() {
        tracker.startSession()
        val sessionStart = tracker.sessionStartTimestamp

        // Within tolerance
        assertTrue(
            tracker.isMediaInSession(
                dateTaken = sessionStart - Constants.SESSION_TIMESTAMP_TOLERANCE_MS + 500,
                relativePath = "DCIM/Camera/IMG_001.jpg",
            ),
        )

        // After session start
        assertTrue(
            tracker.isMediaInSession(
                dateTaken = sessionStart + 1000,
                relativePath = "DCIM/Camera/IMG_002.jpg",
            ),
        )

        // Too early
        assertFalse(
            tracker.isMediaInSession(
                dateTaken = sessionStart - Constants.SESSION_TIMESTAMP_TOLERANCE_MS - 1000,
                relativePath = "DCIM/Camera/IMG_003.jpg",
            ),
        )
    }

    @Test
    fun `isMediaInSession checks relative path prefix per SF-04`() {
        tracker.startSession()
        val sessionStart = tracker.sessionStartTimestamp

        assertTrue(
            tracker.isMediaInSession(
                dateTaken = sessionStart,
                relativePath = "DCIM/Camera/IMG_001.jpg",
            ),
        )

        assertFalse(
            tracker.isMediaInSession(
                dateTaken = sessionStart,
                relativePath = "DCIM/Screenshots/IMG_001.jpg",
            ),
        )

        assertFalse(
            tracker.isMediaInSession(
                dateTaken = sessionStart,
                relativePath = "Download/photo.jpg",
            ),
        )
    }

    @Test
    fun `removeMedia removes item by URI`() {
        tracker.startSession()
        tracker.addMedia(MediaItem(uri = "content://media/1", dateTaken = System.currentTimeMillis(), isVideo = false))
        tracker.addMedia(MediaItem(uri = "content://media/2", dateTaken = System.currentTimeMillis(), isVideo = false))

        tracker.removeMedia("content://media/1")
        assertEquals(1, tracker.getSessionMedia().size)
        assertEquals("content://media/2", tracker.getSessionMedia()[0].uri)
    }

    // -------------------------------------------------------------------------
    // Issue #537: session-change observer
    // -------------------------------------------------------------------------

    @Test
    fun `addMedia notifies listener so a late photo can refresh the viewer`() {
        tracker.startSession()
        var notifications = 0
        tracker.addListener { notifications++ }

        tracker.addMedia(MediaItem(uri = "content://media/1", dateTaken = 1000L, isVideo = false))

        assertEquals(1, notifications)
    }

    @Test
    fun `startSession and endSession notify listener`() {
        var notifications = 0
        tracker.addListener { notifications++ }

        tracker.startSession()
        tracker.endSession()

        assertEquals(2, notifications)
    }

    @Test
    fun `endSession does not notify when session is already inactive`() {
        // endSession on a tracker that was never started must not fire listeners.
        var notifications = 0
        tracker.addListener { notifications++ }

        tracker.endSession()

        assertEquals(0, notifications)
    }

    @Test
    fun `removeMedia notifies only when an item is actually removed`() {
        tracker.startSession()
        tracker.addMedia(MediaItem(uri = "content://media/1", dateTaken = 1000L, isVideo = false))
        var notifications = 0
        tracker.addListener { notifications++ }

        tracker.removeMedia("content://media/1")
        assertEquals(1, notifications)

        // Removing a URI that is not present must not notify.
        tracker.removeMedia("content://media/absent")
        assertEquals(1, notifications)
    }

    @Test
    fun `addMedia does not notify when no active session`() {
        var notifications = 0
        tracker.addListener { notifications++ }

        tracker.addMedia(MediaItem(uri = "content://media/1", dateTaken = 1000L, isVideo = false))

        assertEquals(0, notifications)
    }

    @Test
    fun `addMedia does not notify for a duplicate URI`() {
        tracker.startSession()
        val item = MediaItem(uri = "content://media/1", dateTaken = 1000L, isVideo = false)
        tracker.addMedia(item)
        var notifications = 0
        tracker.addListener { notifications++ }

        tracker.addMedia(item)

        assertEquals(0, notifications)
    }

    @Test
    fun `removed listener is no longer notified`() {
        tracker.startSession()
        var notifications = 0
        val listener = SessionTracker.SessionListener { notifications++ }
        tracker.addListener(listener)
        tracker.removeListener(listener)

        tracker.addMedia(MediaItem(uri = "content://media/1", dateTaken = 1000L, isVideo = false))

        assertEquals(0, notifications)
    }

    @Test
    fun `getSessionMedia returns most recent first per SF-07`() {
        tracker.startSession()
        tracker.addMedia(MediaItem(uri = "content://media/1", dateTaken = 1000L, isVideo = false))
        tracker.addMedia(MediaItem(uri = "content://media/2", dateTaken = 3000L, isVideo = false))
        tracker.addMedia(MediaItem(uri = "content://media/3", dateTaken = 2000L, isVideo = false))

        val media = tracker.getSessionMedia()
        assertEquals("content://media/2", media[0].uri) // newest
        assertEquals("content://media/3", media[1].uri)
        assertEquals("content://media/1", media[2].uri) // oldest
    }
}
