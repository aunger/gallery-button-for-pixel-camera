package com.gb4pc.receiver

import android.content.Context
import android.content.Intent
import com.gb4pc.Constants

/**
 * Testable logic for boot receiver decisions (FS-05).
 */
object BootReceiverLogic {
    fun shouldStartService(context: Context): Boolean {
        val prefs = context.getSharedPreferences(Constants.PREFS_NAME, Context.MODE_PRIVATE)
        return prefs.getBoolean(Constants.PREF_SERVICE_ENABLED, false)
    }

    fun isBootIntent(action: String?): Boolean {
        return action == Intent.ACTION_BOOT_COMPLETED
    }
}
