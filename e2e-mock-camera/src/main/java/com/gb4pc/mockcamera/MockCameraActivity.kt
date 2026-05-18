package com.gb4pc.mockcamera

import android.app.Activity
import android.content.BroadcastReceiver
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.graphics.Bitmap
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
import android.provider.MediaStore
import android.util.Log

/**
 * Mock camera activity for E2E testing.
 *
 * onResume  → opens the first available camera → fires CameraManager.onCameraUnavailable
 *             in OverlayService → overlay should appear.
 * onPause   → releases the camera             → fires CameraManager.onCameraAvailable
 *             after the debounce delay         → overlay should disappear.
 *
 * Renders a solid #00C853 (GREEN) full-bleed View — no real camera preview is shown.
 * This is intentional (Alternative 1 from the E2E plan): the emulator's virtual-scene
 * camera renderer is incompatible with -gpu swiftshader_indirect, so we render green
 * directly in the activity rather than relying on the camera feed.
 *
 * Supports capture via the ACTION_SHUTTER broadcast: generates a synthetic GREEN JPEG
 * (Bitmap.createBitmap filled with #00C853), saves it to MediaStore, and broadcasts
 * ACTION_SHUTTER_DONE when the row is queryable.
 *
 * Declared with showWhenLocked + turnScreenOn so it can appear over the lock screen
 * when launched via STILL_IMAGE_CAMERA_SECURE for Tests 4a/5a.
 */
class MockCameraActivity : Activity() {

    companion object {
        private const val TAG = "MockCameraActivity"

        /** Trigger a capture; no extras required. */
        const val ACTION_SHUTTER = "com.gb4pc.mockcamera.ACTION_SHUTTER"

        /** Broadcast sent after the captured row is queryable in MediaStore. */
        const val ACTION_SHUTTER_DONE = "com.gb4pc.mockcamera.ACTION_SHUTTER_DONE"

        /** Intent extra: MediaStore URI of the captured image (Uri, may be absent on error). */
        const val EXTRA_IMAGE_URI = "com.gb4pc.mockcamera.EXTRA_IMAGE_URI"

        /** GREEN color (#00C853) used for the background view and synthetic captures. */
        private const val GREEN_COLOR = 0xFF00C853.toInt()

        private const val CAPTURE_WIDTH = 1920
        private const val CAPTURE_HEIGHT = 1080
    }

    // -------------------------------------------------------------------------
    // Camera state (open/close for CameraManager.AvailabilityCallback)
    // -------------------------------------------------------------------------

    private val mainHandler = Handler(Looper.getMainLooper())

    /** Background thread for camera callbacks so we don't block the main thread. */
    private var cameraThread: HandlerThread? = null
    private var cameraHandler: Handler? = null

    private var cameraDevice: CameraDevice? = null

    // -------------------------------------------------------------------------
    // Shutter broadcast receiver
    // -------------------------------------------------------------------------

    private val shutterReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action == ACTION_SHUTTER) {
                triggerCapture()
            }
        }
    }

    /**
     * True only after a successful [registerReceiver]. Tracked so [onPause] does not call
     * [unregisterReceiver] when registration threw, which would itself throw
     * `IllegalArgumentException: Receiver not registered` and mask the original error.
     */
    private var shutterReceiverRegistered = false

    // -------------------------------------------------------------------------
    // CameraDevice state callback
    // -------------------------------------------------------------------------

    private val deviceStateCallback = object : CameraDevice.StateCallback() {
        override fun onOpened(camera: CameraDevice) {
            Log.d(TAG, "CameraDevice.onOpened: ${camera.id}")
            cameraDevice = camera
        }

        override fun onDisconnected(camera: CameraDevice) {
            Log.w(TAG, "CameraDevice.onDisconnected: ${camera.id}")
            camera.close()
            cameraDevice = null
        }

        override fun onError(camera: CameraDevice, error: Int) {
            Log.e(TAG, "CameraDevice.onError: ${camera.id} error=$error")
            camera.close()
            cameraDevice = null
        }
    }

    // -------------------------------------------------------------------------
    // Activity lifecycle
    // -------------------------------------------------------------------------

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // On API 27+ the manifest android:showWhenLocked / android:turnScreenOn attributes
        // are advisory hints that some OEM ROMs and the API-35 emulator may ignore.
        // Calling the programmatic equivalents here guarantees the window is shown over the
        // lock screen and wakes the display even if the screen turned off between the
        // adb unlock and am start.
        setShowWhenLocked(true)
        setTurnScreenOn(true)
        setContentView(R.layout.activity_mock_camera)
    }

    override fun onResume() {
        super.onResume()
        startCameraThread()
        openCamera()
        // RECEIVER_EXPORTED is required so the E2E test process (com.gb4pc UID) can
        // deliver ACTION_SHUTTER across UIDs into this activity (com.google.android.GoogleCamera).
        // On API 33+ RECEIVER_NOT_EXPORTED silently drops cross-UID broadcasts. This APK is
        // installed only on test emulators and ACTION_SHUTTER is a private action string, so
        // exporting the receiver carries no production risk.
        //
        // The registration is wrapped in try/catch because an uncaught exception here would
        // bubble out of onResume() and crash the activity *after* the window has been
        // created but *before* the green View has been composed to the surface, leaving
        // the CI pre-flight smoke check (and the rest of the E2E suite) staring at the
        // system default window background instead of #00C853. The shutter handshake is
        // only needed by the capture tests; the smoke check and the
        // ForegroundDetector/overlay path do not depend on it, so a failed registration
        // must not prevent the activity from rendering.
        try {
            registerReceiver(
                shutterReceiver,
                IntentFilter(ACTION_SHUTTER),
                Context.RECEIVER_EXPORTED,
            )
            shutterReceiverRegistered = true
        } catch (e: RuntimeException) {
            Log.e(TAG, "onResume: shutter receiver registration failed: ${e.message}", e)
        }
    }

    override fun onPause() {
        super.onPause()
        if (shutterReceiverRegistered) {
            unregisterReceiver(shutterReceiver)
            shutterReceiverRegistered = false
        }
        closeCamera()
        stopCameraThread()
    }

    // -------------------------------------------------------------------------
    // Camera open / close (only for CameraManager.AvailabilityCallback)
    // -------------------------------------------------------------------------

    private fun startCameraThread() {
        val t = HandlerThread("MockCameraThread").also { it.start() }
        cameraThread = t
        cameraHandler = Handler(t.looper)
    }

    private fun stopCameraThread() {
        cameraThread?.quitSafely()
        cameraThread?.join()
        cameraThread = null
        cameraHandler = null
    }

    private fun openCamera() {
        val cm = getSystemService(CAMERA_SERVICE) as CameraManager
        val cameraId = cm.cameraIdList.firstOrNull() ?: run {
            Log.w(TAG, "openCamera: no cameras found")
            return
        }
        try {
            cm.openCamera(cameraId, deviceStateCallback, cameraHandler)
        } catch (e: Exception) {
            Log.w(TAG, "openCamera failed for $cameraId: ${e.message}")
        }
    }

    private fun closeCamera() {
        cameraDevice?.close()
        cameraDevice = null
    }

    // -------------------------------------------------------------------------
    // Capture / shutter path (synthetic GREEN JPEG)
    // -------------------------------------------------------------------------

    /**
     * Generates a synthetic GREEN ([GREEN_COLOR]) JPEG, saves it to MediaStore,
     * and broadcasts [ACTION_SHUTTER_DONE] once the row is queryable.
     *
     * The bitmap is created with [Bitmap.createBitmap] filled with [GREEN_COLOR], then
     * compressed to JPEG. This is the Alternative 1 approach from the E2E plan: a
     * purely synthetic capture, independent of the emulator's camera hardware.
     */
    private fun triggerCapture() {
        cameraHandler?.post {
            val bytes = buildGreenJpeg()
            mainHandler.post { saveJpegToMediaStore(bytes) }
        }
    }

    private fun buildGreenJpeg(): ByteArray {
        val bmp = Bitmap.createBitmap(CAPTURE_WIDTH, CAPTURE_HEIGHT, Bitmap.Config.ARGB_8888)
        bmp.eraseColor(GREEN_COLOR)
        val out = java.io.ByteArrayOutputStream()
        bmp.compress(Bitmap.CompressFormat.JPEG, 100, out)
        bmp.recycle()
        return out.toByteArray()
    }

    private fun saveJpegToMediaStore(bytes: ByteArray) {
        val now = System.currentTimeMillis()
        val values = ContentValues().apply {
            put(MediaStore.Images.Media.DISPLAY_NAME, "mock_capture_$now.jpg")
            put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg")
            put(MediaStore.Images.Media.DATE_TAKEN, now)
            put(MediaStore.Images.Media.DATE_ADDED, now / 1000)
            put(MediaStore.Images.Media.DATE_MODIFIED, now / 1000)
        }

        val uri: Uri? = contentResolver.insert(
            MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values
        )
        if (uri == null) {
            Log.e(TAG, "saveJpegToMediaStore: insert returned null URI")
            broadcastShutterDone(null)
            return
        }

        try {
            contentResolver.openOutputStream(uri)?.use { it.write(bytes) }
        } catch (e: Exception) {
            Log.e(TAG, "saveJpegToMediaStore: write failed: ${e.message}")
            broadcastShutterDone(null)
            return
        }

        Log.d(TAG, "saveJpegToMediaStore: saved $uri")
        pollUntilQueryable(uri)
    }

    /**
     * Polls MediaStore until [uri] is queryable, then broadcasts [ACTION_SHUTTER_DONE].
     * Gives up after 10 s (100 × 100 ms).
     */
    private fun pollUntilQueryable(uri: Uri, attemptsLeft: Int = 100) {
        val cursor = contentResolver.query(uri, arrayOf(MediaStore.Images.Media._ID), null, null, null)
        val found = (cursor?.count ?: 0) > 0
        cursor?.close()

        if (found) {
            broadcastShutterDone(uri)
        } else if (attemptsLeft > 0) {
            mainHandler.postDelayed({ pollUntilQueryable(uri, attemptsLeft - 1) }, 100)
        } else {
            Log.w(TAG, "pollUntilQueryable: timed out waiting for $uri")
            broadcastShutterDone(uri) // still signal so callers don't hang forever
        }
    }

    private fun broadcastShutterDone(uri: Uri?) {
        // Intentionally do NOT call setPackage(packageName): the listener is the E2E test
        // process (com.gb4pc), not this APK. setPackage(com.google.android.GoogleCamera)
        // would restrict delivery to this package and starve the test's latch receiver.
        val intent = Intent(ACTION_SHUTTER_DONE).apply {
            if (uri != null) putExtra(EXTRA_IMAGE_URI, uri)
        }
        sendBroadcast(intent)
    }
}
