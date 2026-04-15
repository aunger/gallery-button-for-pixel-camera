package com.gb4pc.mockcamera

import android.app.Activity
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper

/**
 * Minimal stub activity for E2E testing.
 *
 * onResume  → opens the first available camera → fires CameraManager.onCameraUnavailable
 *             in OverlayService → overlay should appear.
 * onPause   → releases the camera             → fires CameraManager.onCameraAvailable
 *             after the debounce delay         → overlay should disappear.
 *
 * No UI is shown; this is a pure camera-hardware trigger.
 */
class MockCameraActivity : Activity() {

    private val handler = Handler(Looper.getMainLooper())
    private var cameraDevice: CameraDevice? = null

    private val stateCallback = object : CameraDevice.StateCallback() {
        override fun onOpened(camera: CameraDevice) {
            cameraDevice = camera
        }

        override fun onDisconnected(camera: CameraDevice) {
            camera.close()
            cameraDevice = null
        }

        override fun onError(camera: CameraDevice, error: Int) {
            camera.close()
            cameraDevice = null
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
    }

    override fun onResume() {
        super.onResume()
        openCamera()
    }

    override fun onPause() {
        super.onPause()
        closeCamera()
    }

    private fun openCamera() {
        val cm = getSystemService(CAMERA_SERVICE) as CameraManager
        val cameraId = cm.cameraIdList.firstOrNull() ?: return
        try {
            cm.openCamera(cameraId, stateCallback, handler)
        } catch (_: Exception) {
            // Camera unavailable (e.g. already in use) — ignore; the callback
            // fired by the other app will still trigger OverlayService.
        }
    }

    private fun closeCamera() {
        cameraDevice?.close()
        cameraDevice = null
    }
}
