package com.gb4pc.service

import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import com.gb4pc.Constants

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

        val events = usageStatsManager.queryEvents(beginTime, endTime) ?: return null
        val event = UsageEvents.Event()
        var latestForegroundPackage: String? = null
        var latestTimestamp = 0L

        while (events.hasNextEvent()) {
            events.getNextEvent(event)
            if (event.eventType == UsageEvents.Event.MOVE_TO_FOREGROUND) {
                if (event.timeStamp >= latestTimestamp) {
                    latestTimestamp = event.timeStamp
                    latestForegroundPackage = event.packageName
                }
            }
        }
        return latestForegroundPackage
    }

    companion object {
        fun isPixelCameraPackage(packageName: String?): Boolean {
            return packageName == Constants.PIXEL_CAMERA_PACKAGE
        }
    }
}
