package com.gb4pc.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.gb4pc.service.OverlayService
import com.gb4pc.util.DebugLog

/**
 * Starts the overlay service after device reboot (FS-05) or app install/update (FS-68),
 * if the service was previously enabled.
 * L1: Delegates all decision logic to BootReceiverLogic to avoid duplicating PrefsManager checks.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val isBoot = BootReceiverLogic.isBootIntent(intent.action)
        val isInstallOrUpdate = BootReceiverLogic.isInstallOrUpdateIntent(intent.action)
        if (!isBoot && !isInstallOrUpdate) return

        val trigger = if (isBoot) "Boot" else "Install/update"
        val wasEnabled = BootReceiverLogic.shouldStartService(context)
        DebugLog.log("$trigger completed: serviceWasEnabled=$wasEnabled")

        if (wasEnabled) {
            DebugLog.log("$trigger: starting overlay service")
            OverlayService.start(context)
        } else {
            DebugLog.log("$trigger: service not enabled, skipping start")
        }
    }
}
