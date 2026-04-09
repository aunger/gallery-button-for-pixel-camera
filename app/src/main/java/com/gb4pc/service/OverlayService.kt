package com.gb4pc.service

import android.app.KeyguardManager
import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.app.usage.UsageStatsManager
import android.content.BroadcastReceiver
import android.content.ContentUris
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.database.ContentObserver
import android.hardware.camera2.CameraManager
import android.net.Uri
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.provider.MediaStore
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.gb4pc.Constants
import com.gb4pc.R
import com.gb4pc.data.PrefsManager
import com.gb4pc.overlay.OverlayManager
import com.gb4pc.ui.settings.MainActivity
import com.gb4pc.util.DebugLog
import com.gb4pc.util.PermissionHelper
import com.gb4pc.viewer.MediaItem
import com.gb4pc.viewer.SessionTracker

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
    private lateinit var logic: OverlayServiceLogic

    // H7: Track registration state to avoid re-registering on each onStartCommand
    private var callbackRegistered = false

    // H3/M1: BroadcastReceiver for screen-off (start session) and user-present (end session)
    private var screenEventReceiver: BroadcastReceiver? = null

    // H4: ContentObserver registered at session start, unregistered at session end
    private var mediaObserver: ContentObserver? = null

    private val cameraCallback = object : CameraManager.AvailabilityCallback() {
        override fun onCameraUnavailable(cameraId: String) {
            DebugLog.log("Camera $cameraId unavailable")
            logic.onCameraUnavailable(cameraId)
        }

        override fun onCameraAvailable(cameraId: String) {
            DebugLog.log("Camera $cameraId available")
            logic.onCameraAvailable(cameraId)
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
        val km = getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager

        logic = OverlayServiceLogic(
            hasUsageStatsPermission = { PermissionHelper.hasUsageStatsPermission(this) },
            hasOverlayPermission = { PermissionHelper.hasOverlayPermission(this) },
            overlayManager = overlayManager,
            cameraState = cameraState,
            foregroundDetector = foregroundDetector,
            sessionTracker = SessionTracker.instance,
            handler = handler,
            onUsageAccessLost = {
                postPermissionNotification(
                    Constants.NOTIFICATION_PERMISSION_ID,
                    getString(R.string.notification_usage_access_lost)
                )
            },
            onOverlayPermissionLost = {
                postPermissionNotification(
                    Constants.NOTIFICATION_PERMISSION_ID,
                    getString(R.string.notification_overlay_lost)
                )
            },
            isKeyguardLocked = { km.isKeyguardLocked },
            onRegisterMediaObserver = ::registerMediaObserver,
            onUnregisterMediaObserver = ::unregisterMediaObserver,
            onOverlayStateChanged = { active -> isOverlayActive = active },
        )
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            DebugLog.log("Service stop requested")
            prefsManager.isServiceEnabled = false
            stopSelf()
            return START_NOT_STICKY
        }

        // H6: Refuse to start if Pixel Camera is not installed (OV-04)
        if (!PermissionHelper.isPixelCameraInstalled(this)) {
            DebugLog.log("Pixel Camera not installed — stopping service (OV-04)")
            stopSelf()
            return START_NOT_STICKY
        }

        startForeground(Constants.NOTIFICATION_ID, buildNotification())
        registerCameraCallbacks()
        registerScreenEventReceiver()
        DebugLog.log("Service started")
        return START_STICKY
    }

    override fun onDestroy() {
        DebugLog.log("Service destroyed")
        isOverlayActive = false
        logic.reset()
        if (callbackRegistered) {
            cameraManager.unregisterAvailabilityCallback(cameraCallback)
            callbackRegistered = false
        }
        unregisterScreenEventReceiver()
        unregisterMediaObserver()
        overlayManager.hide()
        cameraState.reset()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun registerCameraCallbacks() {
        // H7: Only register if not already registered
        if (callbackRegistered) return
        // DT-01: Register for all camera IDs
        try {
            cameraManager.registerAvailabilityCallback(cameraCallback, handler)
            callbackRegistered = true
            val ids = cameraManager.cameraIdList
            DebugLog.log("Registered camera callback for ${ids.size} cameras: ${ids.joinToString()}")
        } catch (e: Exception) {
            DebugLog.log("Failed to register camera callback: ${e.message}")
        }
    }

    // H3/M1: Register receiver for screen-off and user-present events
    private fun registerScreenEventReceiver() {
        if (screenEventReceiver != null) return
        screenEventReceiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context?, intent: Intent?) {
                when (intent?.action) {
                    Intent.ACTION_SCREEN_OFF -> onScreenOff()
                    Intent.ACTION_USER_PRESENT -> onUserPresent()
                }
            }
        }
        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_OFF)
            addAction(Intent.ACTION_USER_PRESENT)
        }
        ContextCompat.registerReceiver(this, screenEventReceiver, filter, ContextCompat.RECEIVER_NOT_EXPORTED)
        DebugLog.log("Screen event receiver registered")
    }

    private fun unregisterScreenEventReceiver() {
        screenEventReceiver?.let {
            try {
                unregisterReceiver(it)
            } catch (_: Exception) {}
            screenEventReceiver = null
        }
    }

    // H3: When screen turns off while overlay is active but session not started, start session
    private fun onScreenOff() {
        DebugLog.log("Screen off received")
        if (logic.isOverlayActive && !SessionTracker.instance.isSessionActive) {
            SessionTracker.instance.startSession()
            registerMediaObserver()
            DebugLog.log("Secure camera session started on screen off")
        }
    }

    // M1: When device unlocks while session is active, end the session
    private fun onUserPresent() {
        DebugLog.log("User present (device unlocked)")
        if (SessionTracker.instance.isSessionActive) {
            SessionTracker.instance.endSession()
            unregisterMediaObserver()
            DebugLog.log("Secure camera session ended on device unlock")
        }
    }

    // H4: Register ContentObserver on session start (SF-03)
    private fun registerMediaObserver() {
        if (mediaObserver != null) return
        val sessionStartMs = SessionTracker.instance.sessionStartTimestamp
        mediaObserver = object : ContentObserver(handler) {
            override fun onChange(selfChange: Boolean, uri: Uri?) {
                val item = queryLatestMedia(sessionStartMs)
                if (item != null) {
                    SessionTracker.instance.addMedia(item)
                    DebugLog.log("Media added to session: ${item.uri}")
                }
            }
        }
        contentResolver.registerContentObserver(
            MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
            true,
            mediaObserver!!
        )
        contentResolver.registerContentObserver(
            MediaStore.Video.Media.EXTERNAL_CONTENT_URI,
            true,
            mediaObserver!!
        )
        DebugLog.log("ContentObserver registered at session start")
    }

    private fun unregisterMediaObserver() {
        mediaObserver?.let {
            contentResolver.unregisterContentObserver(it)
            mediaObserver = null
            DebugLog.log("ContentObserver unregistered")
        }
    }

    // H4: Query for latest media item added after session start
    private fun queryLatestMedia(sessionStartMs: Long): MediaItem? {
        val projection = arrayOf(
            MediaStore.MediaColumns._ID,
            MediaStore.MediaColumns.MIME_TYPE,
            MediaStore.MediaColumns.DATE_ADDED,
            MediaStore.MediaColumns.DATE_TAKEN
        )
        val selectionArgs = arrayOf((sessionStartMs / 1000).toString())

        // Query images
        val imageResult = queryMediaStore(
            MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
            projection,
            selectionArgs,
            isVideo = false
        )
        if (imageResult != null) return imageResult

        // Query videos
        return queryMediaStore(
            MediaStore.Video.Media.EXTERNAL_CONTENT_URI,
            projection,
            selectionArgs,
            isVideo = true
        )
    }

    private fun queryMediaStore(
        contentUri: Uri,
        projection: Array<String>,
        selectionArgs: Array<String>,
        isVideo: Boolean
    ): MediaItem? {
        val selection = "${MediaStore.MediaColumns.DATE_ADDED} >= ?"
        val sortOrder = "${MediaStore.MediaColumns.DATE_ADDED} DESC LIMIT 1"
        contentResolver.query(contentUri, projection, selection, selectionArgs, sortOrder)?.use { cursor ->
            if (cursor.moveToFirst()) {
                val id = cursor.getLong(cursor.getColumnIndexOrThrow(MediaStore.MediaColumns._ID))
                val mime = cursor.getString(cursor.getColumnIndexOrThrow(MediaStore.MediaColumns.MIME_TYPE))
                val dateTakenCol = cursor.getColumnIndex(MediaStore.MediaColumns.DATE_TAKEN)
                val dateTaken = if (dateTakenCol >= 0) cursor.getLong(dateTakenCol) else System.currentTimeMillis()
                val uri = ContentUris.withAppendedId(contentUri, id)
                return MediaItem(uri.toString(), dateTaken, isVideo)
            }
        }
        return null
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

        /**
         * True while the overlay is currently visible. Updated by the running service instance;
         * resets to false when the service is destroyed. Observable by E2E tests without binding.
         */
        @Volatile
        @JvmField
        var isOverlayActive: Boolean = false

        fun start(context: Context) {
            val intent = Intent(context, OverlayService::class.java)
            context.startForegroundService(intent)
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, OverlayService::class.java))
        }
    }
}
