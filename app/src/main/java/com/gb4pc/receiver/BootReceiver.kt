package com.gb4pc.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.gb4pc.service.OverlayService
import com.gb4pc.util.DebugLog

/**
 * Restarts the overlay service after device reboot if it was previously enabled (FS-05).
 * L1: Delegates all decision logic to BootReceiverLogic to avoid duplicating PrefsManager checks.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (!BootReceiverLogic.isBootIntent(intent.action)) return

        val wasEnabled = BootReceiverLogic.shouldStartService(context)
        DebugLog.log("Boot completed. Service was enabled: $wasEnabled")

        if (wasEnabled) {
            OverlayService.start(context)
        }
    }
}
