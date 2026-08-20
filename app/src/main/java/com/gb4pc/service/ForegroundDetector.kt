package com.gb4pc.service

import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import com.gb4pc.Constants
import com.gb4pc.util.DebugLog

/**
 * Detects the current foreground app using UsageStatsManager (DT-02, DT-06).
 *
 * @param selfPackage The package name of this app (com.gb4pc). Foreground events for
 *   this package are ignored because the overlay window causes Android to report GB4PC itself
 *   as the foreground app, which would displace the camera from the detected foreground app
 *   and hide the overlay (Issue #80).
 */
class ForegroundDetector(
    private val usageStatsManager: UsageStatsManager,
    private val selfPackage: String,
) {
    /**
     * The distinct non-self packages that produced a foreground event during the most recent
     * [getForegroundPackage] call, which is the same set the summary line reports as `all FG
     * apps`. Empty before the first call, and empty after a call that found no foreground event.
     *
     * Exposed for callers that hold state this class cannot see (Issue #907): joining this set
     * with the camera state is what makes the Issue #86 race (a camera held while some other app
     * carries the latest foreground event and Pixel Camera carries an earlier one) visible as a
     * single signal instead of a coincidence between two log lines. It never takes part in
     * detection: [getForegroundPackage]'s return value is unaffected by this property existing.
     *
     * Volatile because camera callbacks can drive queries from different threads; each query
     * publishes its own finished set, so a reader always sees one complete window's candidates.
     */
    @Volatile
    var lastForegroundCandidates: Set<String> = emptySet()
        private set

    /**
     * Queries UsageStatsManager for the most recent foreground event
     * in the last [Constants.USAGE_STATS_WINDOW_MS] milliseconds.
     *
     * On Android 10+ (API 29+) the system emits [UsageEvents.Event.ACTIVITY_RESUMED] instead
     * of the deprecated [UsageEvents.Event.MOVE_TO_FOREGROUND]. Both event types are accepted
     * so that detection works across all supported API levels (Issue #86).
     *
     * Events for [selfPackage] are skipped (Issue #80).
     * Returns the package name, or null if no event found (EC-09).
     */
    @Suppress("DEPRECATION") // MOVE_TO_FOREGROUND is deprecated in API 29; we accept both types
    fun getForegroundPackage(): String? {
        val endTime = System.currentTimeMillis()
        val beginTime = endTime - Constants.USAGE_STATS_WINDOW_MS

        val events = usageStatsManager.queryEvents(beginTime, endTime)
        if (events == null) {
            lastForegroundCandidates = emptySet()
            DebugLog.log("ForegroundDetector: queryEvents returned null; usage-stats permission missing?")
            return null
        }

        val event = UsageEvents.Event()
        var latestForegroundPackage: String? = null
        var latestTimestamp = 0L
        var totalEvents = 0
        var foregroundEvents = 0
        var skippedSelfEvents = 0
        // Tracks all distinct foreground packages seen in this window, in order of first appearance
        // (Issue #324). LinkedHashSet gives O(1) deduplication while preserving insertion order.
        val allForegroundPackages = LinkedHashSet<String>()

        while (events.hasNextEvent()) {
            events.getNextEvent(event)
            totalEvents++
            // Accept both ACTIVITY_RESUMED (API 29+) and the legacy MOVE_TO_FOREGROUND so that
            // detection works on all supported Android versions. On API 29+ the system emits
            // ACTIVITY_RESUMED instead of MOVE_TO_FOREGROUND (Issue #86).
            val isForegroundEvent =
                event.eventType == UsageEvents.Event.ACTIVITY_RESUMED ||
                    event.eventType == UsageEvents.Event.MOVE_TO_FOREGROUND
            if (isForegroundEvent) {
                if (event.packageName == selfPackage) {
                    skippedSelfEvents++
                    DebugLog.log(
                        "ForegroundDetector: skipping self foreground event pkg=${event.packageName} ts=${event.timeStamp} (Issue #80)",
                    )
                    continue
                }
                foregroundEvents++
                DebugLog.log("ForegroundDetector: foreground event type=${event.eventType} pkg=${event.packageName} ts=${event.timeStamp}")
                allForegroundPackages.add(event.packageName)
                if (event.timeStamp >= latestTimestamp) {
                    latestTimestamp = event.timeStamp
                    latestForegroundPackage = event.packageName
                }
            }
        }

        lastForegroundCandidates = allForegroundPackages
        val selfNote = if (skippedSelfEvents > 0) ", skipped $skippedSelfEvents self-event(s)" else ""
        if (latestForegroundPackage != null) {
            DebugLog.log(
                "ForegroundDetector: foreground=$latestForegroundPackage, all FG apps=$allForegroundPackages ($foregroundEvents foreground event(s) of $totalEvents total$selfNote)",
            )
        } else {
            DebugLog.log(
                "ForegroundDetector: no foreground app detected ($foregroundEvents foreground event(s) of $totalEvents total$selfNote)",
            )
        }
        return latestForegroundPackage
    }

    companion object {
        fun isPixelCameraPackage(packageName: String?): Boolean = packageName == Constants.PIXEL_CAMERA_PACKAGE
    }
}
