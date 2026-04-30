package com.gb4pc.service

import android.app.ActivityManager
import android.content.Context
import com.gb4pc.data.PrefsManager
import com.gb4pc.util.DebugLog

/**
 * Checks whether the overlay service should be running and starts it if it isn't (issue #67).
 *
 * Extracted as a standalone object so the logic can be unit-tested without a real Activity.
 */
object ServiceChecker {

    /**
     * Returns true if [OverlayService] currently has a running instance.
     *
     * [ActivityManager.getRunningServices] is deprecated for third-party use on API 26+ but
     * remains functional for an app querying its own services, which is exactly our use case.
     */
    @Suppress("DEPRECATION")
    fun isServiceRunning(context: Context): Boolean {
        val am = context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        val serviceName = OverlayService::class.java.name
        return am.getRunningServices(Int.MAX_VALUE)
            .any { it.service.className == serviceName }
    }

    /**
     * If the service is enabled in preferences but is not currently running, logs the
     * discrepancy and starts the service.
     *
     * Call this on app launch (e.g. [android.app.Activity.onCreate]) so a crashed or
     * killed service is automatically recovered.
     */
    fun ensureServiceRunningIfEnabled(context: Context, prefs: PrefsManager) {
        if (!prefs.isServiceEnabled) return

        if (!isServiceRunning(context)) {
            DebugLog.log("Service should be running but isn't — starting it now")
            OverlayService.start(context)
        }
    }
}
