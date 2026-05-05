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
 *   as the foreground app — which would displace the camera from the detected foreground app
 *   and hide the overlay (Issue #80).
 */
class ForegroundDetector(
    private val usageStatsManager: UsageStatsManager,
    private val selfPackage: String,
) {

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
            DebugLog.log("ForegroundDetector: queryEvents returned null — usage-stats permission missing?")
            return null
        }

        val event = UsageEvents.Event()
        var latestForegroundPackage: String? = null
        var latestTimestamp = 0L
        var totalEvents = 0
        var foregroundEvents = 0
        var skippedSelfEvents = 0

        while (events.hasNextEvent()) {
            events.getNextEvent(event)
            totalEvents++
            // Accept both ACTIVITY_RESUMED (API 29+) and the legacy MOVE_TO_FOREGROUND so that
            // detection works on all supported Android versions. On API 29+ the system emits
            // ACTIVITY_RESUMED instead of MOVE_TO_FOREGROUND (Issue #86).
            val isForegroundEvent = event.eventType == UsageEvents.Event.ACTIVITY_RESUMED ||
                event.eventType == UsageEvents.Event.MOVE_TO_FOREGROUND
            if (isForegroundEvent) {
                if (event.packageName == selfPackage) {
                    skippedSelfEvents++
                    continue
                }
                foregroundEvents++
                if (event.timeStamp >= latestTimestamp) {
                    latestTimestamp = event.timeStamp
                    latestForegroundPackage = event.packageName
                }
            }
        }

        val selfNote = if (skippedSelfEvents > 0) ", skipped $skippedSelfEvents self-event(s)" else ""
        if (latestForegroundPackage != null) {
            DebugLog.log("ForegroundDetector: foreground=$latestForegroundPackage ($foregroundEvents foreground event(s) of $totalEvents total$selfNote)")
        } else {
            DebugLog.log("ForegroundDetector: no foreground app detected ($foregroundEvents foreground event(s) of $totalEvents total$selfNote)")
        }
        return latestForegroundPackage
    }

    companion object {
        fun isPixelCameraPackage(packageName: String?): Boolean {
            return packageName == Constants.PIXEL_CAMERA_PACKAGE
        }
    }
}
