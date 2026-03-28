package com.gb4pc.service

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.app.usage.UsageStatsManager
import android.content.Context
import android.content.Intent
import android.hardware.camera2.CameraManager
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.core.app.NotificationCompat
import com.gb4pc.Constants
import com.gb4pc.R
import com.gb4pc.data.PrefsManager
import com.gb4pc.overlay.OverlayManager
import com.gb4pc.ui.settings.MainActivity
import com.gb4pc.util.DebugLog
import com.gb4pc.util.PermissionHelper

/**
 * Foreground service that monitors camera hardware and manages the overlay (§3, §7).
 */
class OverlayService : Service() {

    private lateinit var cameraManager: CameraManager
    private lateinit var foregroundDetector: ForegroundDetector
    private lateinit var prefsManager: PrefsManager
    private lateinit var overlayManager: OverlayManager
    private lateinit var cameraState: CameraState
    private lateinit var handler: Handler

    private var deactivateRunnable: Runnable? = null
    private var isOverlayActive = false

    private val cameraCallback = object : CameraManager.AvailabilityCallback() {
        override fun onCameraUnavailable(cameraId: String) {
            DebugLog.log("Camera $cameraId unavailable")
            cameraState.setCameraUnavailable(cameraId)
            cancelPendingDeactivation()
            evaluateForeground()
        }

        override fun onCameraAvailable(cameraId: String) {
            DebugLog.log("Camera $cameraId available")
            cameraState.setCameraAvailable(cameraId)

            // DT-04/DT-05: Only deactivate if ALL cameras available, with debounce
            if (cameraState.areAllCamerasAvailable()) {
                scheduleDeactivation()
            }
        }
    }

    override fun onCreate() {
        super.onCreate()
        handler = Handler(Looper.getMainLooper())
        cameraManager = getSystemService(Context.CAMERA_SERVICE) as CameraManager
        val usm = getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
        foregroundDetector = ForegroundDetector(usm)
        prefsManager = PrefsManager(this)
        overlayManager = OverlayManager(this, prefsManager)
        cameraState = CameraState()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            DebugLog.log("Service stop requested")
            prefsManager.isServiceEnabled = false
            stopSelf()
            return START_NOT_STICKY
        }

        startForeground(Constants.NOTIFICATION_ID, buildNotification())
        registerCameraCallbacks()
        DebugLog.log("Service started")
        return START_STICKY
    }

    override fun onDestroy() {
        DebugLog.log("Service destroyed")
        cancelPendingDeactivation()
        cameraManager.unregisterAvailabilityCallback(cameraCallback)
        overlayManager.hide()
        cameraState.reset()
        isOverlayActive = false
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun registerCameraCallbacks() {
        // DT-01: Register for all camera IDs
        try {
            cameraManager.registerAvailabilityCallback(cameraCallback, handler)
            val ids = cameraManager.cameraIdList
            DebugLog.log("Registered camera callback for ${ids.size} cameras: ${ids.joinToString()}")
        } catch (e: Exception) {
            DebugLog.log("Failed to register camera callback: ${e.message}")
        }
    }

    /**
     * DT-02/DT-03: Check if Pixel Camera is the foreground app.
     */
    private fun evaluateForeground() {
        if (!PermissionHelper.hasUsageStatsPermission(this)) {
            DebugLog.log("Usage stats permission missing — hiding overlay (PM-03)")
            if (isOverlayActive) {
                overlayManager.hide()
                isOverlayActive = false
                postPermissionNotification(
                    Constants.NOTIFICATION_PERMISSION_ID,
                    getString(R.string.notification_usage_access_lost)
                )
            }
            return
        }

        val foregroundPackage = foregroundDetector.getForegroundPackage()
        DebugLog.log("Foreground app: $foregroundPackage")

        if (ForegroundDetector.isPixelCameraPackage(foregroundPackage)) {
            if (!isOverlayActive) {
                showOverlay()
            }
        } else {
            // Not Pixel Camera — do nothing (DT-03, EC-07)
            // The overlay will be hidden via the deactivation path when camera is released
        }
    }

    private fun showOverlay() {
        if (!PermissionHelper.hasOverlayPermission(this)) {
            DebugLog.log("Overlay permission missing (PM-04)")
            postPermissionNotification(
                Constants.NOTIFICATION_PERMISSION_ID,
                getString(R.string.notification_overlay_lost)
            )
            return
        }
        DebugLog.log("Showing overlay")
        overlayManager.show()
        isOverlayActive = true
    }

    /**
     * DT-04: Schedule overlay deactivation with debounce delay.
     */
    private fun scheduleDeactivation() {
        cancelPendingDeactivation()
        deactivateRunnable = Runnable {
            if (cameraState.areAllCamerasAvailable()) {
                DebugLog.log("Deactivating overlay (all cameras available)")
                overlayManager.hide()
                isOverlayActive = false
            }
        }
        handler.postDelayed(deactivateRunnable!!, Constants.CAMERA_DEBOUNCE_MS)
    }

    private fun cancelPendingDeactivation() {
        deactivateRunnable?.let {
            handler.removeCallbacks(it)
            deactivateRunnable = null
        }
    }

    private fun buildNotification(): Notification {
        val openIntent = Intent(this, MainActivity::class.java).let { notifIntent ->
            PendingIntent.getActivity(
                this, 0, notifIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
        }
        val stopIntent = Intent(this, OverlayService::class.java).apply {
            action = ACTION_STOP
        }.let { stopInt ->
            PendingIntent.getService(
                this, 0, stopInt,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
        }

        return NotificationCompat.Builder(this, Constants.NOTIFICATION_CHANNEL_ID)
            .setContentTitle(getString(R.string.notification_running))
            .setSmallIcon(R.drawable.ic_notification)
            .setContentIntent(openIntent)
            .addAction(0, getString(R.string.notification_stop), stopIntent)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    private fun postPermissionNotification(id: Int, message: String) {
        val intent = Intent(this, MainActivity::class.java).let { notifIntent ->
            PendingIntent.getActivity(
                this, id, notifIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
        }
        val notification = NotificationCompat.Builder(this, Constants.NOTIFICATION_CHANNEL_ID)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(message)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentIntent(intent)
            .setAutoCancel(true)
            .build()

        val nm = getSystemService(android.app.NotificationManager::class.java)
        nm.notify(id, notification)
    }

    companion object {
        const val ACTION_STOP = "com.gb4pc.STOP_SERVICE"

        fun start(context: Context) {
            val intent = Intent(context, OverlayService::class.java)
            context.startForegroundService(intent)
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, OverlayService::class.java))
        }
    }
}
