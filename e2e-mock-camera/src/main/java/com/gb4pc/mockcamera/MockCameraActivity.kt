package com.gb4pc.mockcamera

import android.app.Activity
import android.content.BroadcastReceiver
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.graphics.ImageFormat
import android.graphics.SurfaceTexture
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.media.Image
import android.media.ImageReader
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
import android.provider.MediaStore
import android.util.Log
import android.view.Surface
import android.view.TextureView

/**
 * Mock camera activity for E2E testing.
 *
 * onResume  → opens the first available camera → fires CameraManager.onCameraUnavailable
 *             in OverlayService → overlay should appear.
 * onPause   → releases the camera             → fires CameraManager.onCameraAvailable
 *             after the debounce delay         → overlay should disappear.
 *
 * Renders the camera preview (virtual-scene GREEN feed) on a full-bleed TextureView.
 * Supports capture via the ACTION_SHUTTER broadcast: saves a JPEG to MediaStore and
 * broadcasts ACTION_SHUTTER_DONE when the row is queryable.
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

        private const val CAPTURE_WIDTH = 1920
        private const val CAPTURE_HEIGHT = 1080
    }

    // -------------------------------------------------------------------------
    // Camera / rendering state
    // -------------------------------------------------------------------------

    private val mainHandler = Handler(Looper.getMainLooper())

    /** Background thread for camera callbacks so we don't block the main thread. */
    private var cameraThread: HandlerThread? = null
    private var cameraHandler: Handler? = null

    private var cameraDevice: CameraDevice? = null
    private var captureSession: CameraCaptureSession? = null
    private var imageReader: ImageReader? = null
    private var previewSurface: Surface? = null

    /** Captured on the main thread in [openCamera]; consumed by [startPreviewAndCapture]. */
    private var pendingSurfaceTexture: SurfaceTexture? = null

    private lateinit var textureView: TextureView

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

    // -------------------------------------------------------------------------
    // CameraDevice state callback
    // -------------------------------------------------------------------------

    private val deviceStateCallback = object : CameraDevice.StateCallback() {
        override fun onOpened(camera: CameraDevice) {
            Log.d(TAG, "CameraDevice.onOpened: ${camera.id}")
            cameraDevice = camera
            startPreviewAndCapture(camera, pendingSurfaceTexture ?: return)
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
        setContentView(R.layout.activity_mock_camera)
        textureView = findViewById(R.id.preview_texture)
    }

    override fun onResume() {
        super.onResume()
        startCameraThread()
        if (textureView.isAvailable) {
            openCamera(textureView.surfaceTexture!!)
        } else {
            textureView.surfaceTextureListener = object : TextureView.SurfaceTextureListener {
                override fun onSurfaceTextureAvailable(surface: SurfaceTexture, w: Int, h: Int) {
                    openCamera(surface)
                }
                override fun onSurfaceTextureSizeChanged(s: SurfaceTexture, w: Int, h: Int) {}
                override fun onSurfaceTextureDestroyed(s: SurfaceTexture): Boolean = true
                override fun onSurfaceTextureUpdated(s: SurfaceTexture) {}
            }
        }
        @Suppress("UnspecifiedRegisterReceiverFlag")
        registerReceiver(shutterReceiver, IntentFilter(ACTION_SHUTTER))
    }

    override fun onPause() {
        super.onPause()
        unregisterReceiver(shutterReceiver)
        closeCamera()
        stopCameraThread()
    }

    // -------------------------------------------------------------------------
    // Camera setup
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

    private fun openCamera(surfaceTexture: SurfaceTexture) {
        pendingSurfaceTexture = surfaceTexture
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

    /**
     * Creates an ImageReader for JPEG capture and starts a preview session that
     * feeds both the TextureView surface and the ImageReader surface.
     *
     * [surfaceTexture] must have been captured on the main thread (see [openCamera]).
     */
    private fun startPreviewAndCapture(camera: CameraDevice, surfaceTexture: SurfaceTexture) {
        val st = surfaceTexture
        st.setDefaultBufferSize(CAPTURE_WIDTH, CAPTURE_HEIGHT)
        val preview = Surface(st)
        previewSurface = preview

        val reader = ImageReader.newInstance(
            CAPTURE_WIDTH, CAPTURE_HEIGHT, ImageFormat.JPEG, /* maxImages= */ 2
        )
        imageReader = reader

        val surfaces = listOf(preview, reader.surface)

        @Suppress("DEPRECATION")
        camera.createCaptureSession(
            surfaces,
            object : CameraCaptureSession.StateCallback() {
                override fun onConfigured(session: CameraCaptureSession) {
                    Log.d(TAG, "CaptureSession configured")
                    captureSession = session
                    startRepeatingPreview(session, camera, preview)
                }

                override fun onConfigureFailed(session: CameraCaptureSession) {
                    Log.e(TAG, "CaptureSession configuration failed")
                }
            },
            cameraHandler
        )
    }

    private fun startRepeatingPreview(
        session: CameraCaptureSession,
        camera: CameraDevice,
        previewSurface: Surface
    ) {
        val request = camera
            .createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW)
            .apply { addTarget(previewSurface) }
            .build()
        session.setRepeatingRequest(request, null, cameraHandler)
    }

    private fun closeCamera() {
        captureSession?.close()
        captureSession = null
        cameraDevice?.close()
        cameraDevice = null
        imageReader?.close()
        imageReader = null
        previewSurface?.release()
        previewSurface = null
        pendingSurfaceTexture = null
    }

    // -------------------------------------------------------------------------
    // Capture / shutter path
    // -------------------------------------------------------------------------

    /**
     * Fires a single JPEG capture from the current session. The [ImageReader.OnImageAvailableListener]
     * writes the result to MediaStore and broadcasts [ACTION_SHUTTER_DONE].
     */
    private fun triggerCapture() {
        val session = captureSession
        val camera = cameraDevice
        val reader = imageReader
        if (session == null || camera == null || reader == null) {
            Log.w(TAG, "triggerCapture: session/camera/reader not ready")
            return
        }

        // Install listener before submitting capture request so we don't miss the callback.
        reader.setOnImageAvailableListener({ imageReader ->
            val image = imageReader.acquireLatestImage() ?: return@setOnImageAvailableListener
            try {
                saveImageToMediaStore(image)
            } finally {
                image.close()
                // Remove listener until next trigger.
                imageReader.setOnImageAvailableListener(null, null)
            }
        }, cameraHandler)

        val captureRequest = camera
            .createCaptureRequest(CameraDevice.TEMPLATE_STILL_CAPTURE)
            .apply { addTarget(reader.surface) }
            .build()
        session.capture(captureRequest, null, cameraHandler)
    }

    private fun saveImageToMediaStore(image: Image) {
        val bytes = jpegBytesFrom(image) ?: return

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
            Log.e(TAG, "saveImageToMediaStore: insert returned null URI")
            broadcastShutterDone(null)
            return
        }

        try {
            contentResolver.openOutputStream(uri)?.use { it.write(bytes) }
        } catch (e: Exception) {
            Log.e(TAG, "saveImageToMediaStore: write failed: ${e.message}")
            broadcastShutterDone(null)
            return
        }

        Log.d(TAG, "saveImageToMediaStore: saved $uri")
        // Poll until row is queryable, then signal.
        mainHandler.post { pollUntilQueryable(uri) }
    }

    private fun jpegBytesFrom(image: Image): ByteArray? {
        val plane = image.planes.firstOrNull() ?: return null
        val buffer = plane.buffer
        val bytes = ByteArray(buffer.remaining())
        buffer.get(bytes)
        return bytes
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
        val intent = Intent(ACTION_SHUTTER_DONE).apply {
            setPackage(packageName)
            if (uri != null) putExtra(EXTRA_IMAGE_URI, uri)
        }
        sendBroadcast(intent)
    }
}
