package com.gb4pc.service

import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import com.gb4pc.Constants
import com.gb4pc.util.DebugLog

/**
 * Detects the current foreground app using UsageStatsManager (DT-02, DT-06).
 */
class ForegroundDetector(private val usageStatsManager: UsageStatsManager) {

    /**
     * Queries UsageStatsManager for the most recent MOVE_TO_FOREGROUND event
     * in the last [Constants.USAGE_STATS_WINDOW_MS] milliseconds.
     * Returns the package name, or null if no event found (EC-09).
     */
    fun getForegroundPackage(): String? {
        val endTime = System.currentTimeMillis()
        val beginTime = endTime - Constants.USAGE_STATS_WINDOW_MS
        DebugLog.log("ForegroundDetector: querying window=${Constants.USAGE_STATS_WINDOW_MS}ms ending at $endTime")

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

        while (events.hasNextEvent()) {
            events.getNextEvent(event)
            totalEvents++
            if (event.eventType == UsageEvents.Event.MOVE_TO_FOREGROUND) {
                foregroundEvents++
                DebugLog.log("ForegroundDetector: MOVE_TO_FOREGROUND pkg=${event.packageName} ts=${event.timeStamp}")
                if (event.timeStamp >= latestTimestamp) {
                    latestTimestamp = event.timeStamp
                    latestForegroundPackage = event.packageName
                }
            }
        }

        if (latestForegroundPackage != null) {
            DebugLog.log("ForegroundDetector: foreground=$latestForegroundPackage ($foregroundEvents foreground event(s) of $totalEvents total)")
        } else {
            DebugLog.log("ForegroundDetector: no foreground app detected ($foregroundEvents foreground event(s) of $totalEvents total)")
        }
        return latestForegroundPackage
    }

    companion object {
        fun isPixelCameraPackage(packageName: String?): Boolean {
            val result = packageName == Constants.PIXEL_CAMERA_PACKAGE
            DebugLog.log("ForegroundDetector: isPixelCamera($packageName) = $result")
            return result
        }
    }
}
