package com.gb4pc.service

import java.util.concurrent.ConcurrentHashMap

/**
 * Tracks which camera IDs are currently unavailable (held by an app).
 * Used to implement DT-05: only deactivate overlay when ALL cameras are available.
 * Thread-safe: camera callbacks may interleave with foreground evaluation.
 */
class CameraState {

    private val unavailableCameras: MutableSet<String> = ConcurrentHashMap.newKeySet()

    fun setCameraUnavailable(cameraId: String) {
        unavailableCameras.add(cameraId)
    }

    fun setCameraAvailable(cameraId: String) {
        unavailableCameras.remove(cameraId)
    }

    fun areAllCamerasAvailable(): Boolean = unavailableCameras.isEmpty()

    fun anyCameraUnavailable(): Boolean = unavailableCameras.isNotEmpty()

    fun getUnavailableCameraIds(): Set<String> = unavailableCameras.toSet()

    fun reset() {
        unavailableCameras.clear()
    }
}
