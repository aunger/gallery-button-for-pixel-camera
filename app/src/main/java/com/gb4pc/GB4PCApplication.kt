package com.gb4pc

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import com.gb4pc.data.PrefsManager
import com.gb4pc.util.PermissionHelper

class GB4PCApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        // Must run before any screen can route a permission request, and before this build can
        // record a first ask of its own, so it sees the *previous* build's state (issue #572).
        PermissionHelper.seedPermissionRequestHistoryForUpgrade(this, PrefsManager(this))
    }

    private fun createNotificationChannel() {
        val channel =
            NotificationChannel(
                Constants.NOTIFICATION_CHANNEL_ID,
                getString(R.string.notification_channel_name),
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = getString(R.string.notification_channel_description)
            }
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(channel)
    }
}
