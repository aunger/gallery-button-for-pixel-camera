package com.gb4pc.service

import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import com.gb4pc.util.DebugLog
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.mockito.kotlin.*
import org.robolectric.RobolectricTestRunner

/**
 * Tests for Issue #324: ForegroundDetector must log the full list of foreground apps seen
 * in the query window, not just the top one.
 *
 * Robolectric is required so that UsageEvents.Event fields (mPackage, mEventType, mTimeStamp)
 * are accessible at runtime.
 */
@Suppress("DEPRECATION") // MOVE_TO_FOREGROUND is deprecated in API 29; accepted in mixed tests.
@RunWith(RobolectricTestRunner::class)
class ForegroundDetectorLogTest {
    private val selfPkg = "com.gb4pc"
    private val cameraPkg = "com.google.android.GoogleCamera"
    private val otherPkg = "com.example.other"
    private val thirdPkg = "com.example.third"

    private lateinit var usm: UsageStatsManager
    private lateinit var detector: ForegroundDetector

    @Before
    fun setUp() {
        usm = mock()
        detector = ForegroundDetector(usm, selfPkg)
        DebugLog.clear()
    }

    private fun eventsOf(vararg specs: Triple<String, Int, Long>) = stubUsageEvents(usm, *specs)

    private fun loggedMessages(): List<String> = DebugLog.getEntries().map { it.message }

    // ── Single foreground app ────────────────────────────────────────────────

    @Test
    fun `summary log includes single FG app in all-apps list (Issue #324)`() {
        eventsOf(Triple(cameraPkg, UsageEvents.Event.MOVE_TO_FOREGROUND, 1000L))

        detector.getForegroundPackage()

        val summary = loggedMessages().first { it.startsWith("ForegroundDetector: foreground=") }
        assertTrue(
            "Summary log must include 'all FG apps' with the camera package",
            summary.contains("all FG apps=[$cameraPkg]"),
        )
    }

    // ── Multiple distinct foreground apps ────────────────────────────────────

    @Test
    fun `summary log lists all FG apps when multiple packages appear (Issue #324)`() {
        eventsOf(
            Triple(otherPkg, UsageEvents.Event.MOVE_TO_FOREGROUND, 1000L),
            Triple(cameraPkg, UsageEvents.Event.MOVE_TO_FOREGROUND, 2000L),
        )

        detector.getForegroundPackage()

        val summary = loggedMessages().first { it.startsWith("ForegroundDetector: foreground=") }
        assertTrue(
            "Summary log must list both packages in the all-apps list",
            summary.contains(otherPkg) && summary.contains(cameraPkg) && summary.contains("all FG apps="),
        )
    }

    @Test
    fun `summary log lists three distinct FG apps (Issue #324)`() {
        eventsOf(
            Triple(otherPkg, UsageEvents.Event.MOVE_TO_FOREGROUND, 1000L),
            Triple(thirdPkg, UsageEvents.Event.MOVE_TO_FOREGROUND, 2000L),
            Triple(cameraPkg, UsageEvents.Event.MOVE_TO_FOREGROUND, 3000L),
        )

        detector.getForegroundPackage()

        val summary = loggedMessages().first { it.startsWith("ForegroundDetector: foreground=") }
        assertTrue(
            "Summary log must list all three packages",
            summary.contains(otherPkg) && summary.contains(thirdPkg) && summary.contains(cameraPkg),
        )
    }

    // ── Deduplication: same package appearing more than once ─────────────────

    @Test
    fun `same package appearing multiple times is listed only once (Issue #324)`() {
        eventsOf(
            Triple(cameraPkg, UsageEvents.Event.MOVE_TO_FOREGROUND, 1000L),
            Triple(otherPkg, UsageEvents.Event.MOVE_TO_FOREGROUND, 2000L),
            Triple(cameraPkg, UsageEvents.Event.MOVE_TO_FOREGROUND, 3000L),
        )

        detector.getForegroundPackage()

        val summary = loggedMessages().first { it.startsWith("ForegroundDetector: foreground=") }
        // Count occurrences of cameraPkg inside the all-FG-apps bracket
        val allAppsSection = summary.substringAfter("all FG apps=[").substringBefore("]")
        val count = allAppsSection.split(", ").count { it == cameraPkg }
        assertTrue(
            "Camera package must appear exactly once in the all-apps list even when it fires twice",
            count == 1,
        )
    }

    // ── Self events excluded from all-apps list ───────────────────────────────

    @Test
    fun `self package is not included in all FG apps list (Issue #324)`() {
        eventsOf(
            Triple(cameraPkg, UsageEvents.Event.MOVE_TO_FOREGROUND, 1000L),
            Triple(selfPkg, UsageEvents.Event.MOVE_TO_FOREGROUND, 2000L),
        )

        detector.getForegroundPackage()

        val summary = loggedMessages().first { it.startsWith("ForegroundDetector: foreground=") }
        val allAppsSection = summary.substringAfter("all FG apps=[").substringBefore("]")
        assertTrue(
            "Self package must not appear in the all-apps list",
            !allAppsSection.contains(selfPkg),
        )
    }

    // ── No foreground events: no all-apps log line ───────────────────────────

    @Test
    fun `no foreground events produces 'no foreground app detected' log, not all-apps (Issue #324)`() {
        val events: UsageEvents =
            mock {
                on { hasNextEvent() } doReturn false
            }
        whenever(usm.queryEvents(any(), any())).thenReturn(events)

        detector.getForegroundPackage()

        val messages = loggedMessages()
        assertTrue(
            "When no events found the log must say 'no foreground app detected'",
            messages.any { it.contains("no foreground app detected") },
        )
        assertTrue(
            "When no events found, the summary must not contain 'all FG apps'",
            messages.none { it.contains("all FG apps=") },
        )
    }
}
