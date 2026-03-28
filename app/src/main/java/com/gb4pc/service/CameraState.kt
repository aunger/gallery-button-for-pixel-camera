package com.gb4pc.service

/**
 * Tracks which camera IDs are currently unavailable (held by an app).
 * Used to implement DT-05: only deactivate overlay when ALL cameras are available.
 */
class CameraState {

    private val unavailableCameras = mutableSetOf<String>()

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
