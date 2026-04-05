package com.gb4pc.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.gb4pc.Constants
import com.gb4pc.service.OverlayService
import com.gb4pc.util.DebugLog

/**
 * Restarts the overlay service after device reboot if it was previously enabled (FS-05).
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return

        val prefs = context.getSharedPreferences(Constants.PREFS_NAME, Context.MODE_PRIVATE)
        val wasEnabled = prefs.getBoolean(Constants.PREF_SERVICE_ENABLED, false)

        DebugLog.log("Boot completed. Service was enabled: $wasEnabled")

        if (wasEnabled) {
            OverlayService.start(context)
        }
    }
}
