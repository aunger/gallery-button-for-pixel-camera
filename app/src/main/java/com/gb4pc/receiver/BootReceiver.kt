package com.gb4pc.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.gb4pc.Constants
import com.gb4pc.service.OverlayService
import com.gb4pc.util.DebugLog

/**
 * Starts the overlay service after device reboot (FS-05) or app install/update (FS-68),
 * if the service was previously enabled.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val isBoot = intent.action == Intent.ACTION_BOOT_COMPLETED
        val isInstallOrUpdate = intent.action == Intent.ACTION_MY_PACKAGE_REPLACED
        if (!isBoot && !isInstallOrUpdate) return

        val trigger = if (isBoot) "Boot" else "Install/update"
        val prefs = context.getSharedPreferences(Constants.PREFS_NAME, Context.MODE_PRIVATE)
        val wasEnabled = prefs.getBoolean(Constants.PREF_SERVICE_ENABLED, false)
        DebugLog.log("$trigger completed: serviceWasEnabled=$wasEnabled")

        if (wasEnabled) {
            DebugLog.log("$trigger: starting overlay service")
            OverlayService.start(context)
        } else {
            DebugLog.log("$trigger: service not enabled, skipping start")
        }
    }
}
