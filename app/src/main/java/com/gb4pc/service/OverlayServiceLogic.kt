package com.gb4pc.service

import com.gb4pc.Constants

/**
 * Extracted testable logic for OverlayService's camera callback handling.
 * Manages the decision of when to evaluate foreground and when to debounce deactivation.
 */
class OverlayServiceLogic(
    val cameraState: CameraState,
    private val foregroundDetector: ForegroundDetector,
    private val hasUsageStatsPermission: () -> Boolean,
    private val hasOverlayPermission: () -> Boolean,
    private val isKeyguardLocked: () -> Boolean,
) {
    var isOverlayActive: Boolean = false
        private set

    /**
     * Called when a camera becomes unavailable. Returns the action to take.
     */
    fun onCameraUnavailable(cameraId: String): ServiceAction {
        cameraState.setCameraUnavailable(cameraId)
        return evaluateForeground()
    }

    /**
     * Called when a camera becomes available. Returns the action to take.
     */
    fun onCameraAvailable(cameraId: String): ServiceAction {
        cameraState.setCameraAvailable(cameraId)
        return if (cameraState.areAllCamerasAvailable()) {
            ServiceAction.ScheduleDeactivation(Constants.CAMERA_DEBOUNCE_MS)
        } else {
            ServiceAction.None
        }
    }

    /**
     * Called when the debounce timer fires. Returns the action to take.
     */
    fun onDeactivationTimerFired(): ServiceAction {
        if (cameraState.areAllCamerasAvailable()) {
            isOverlayActive = false
            return ServiceAction.Deactivate
        }
        return ServiceAction.None
    }

    /**
     * Evaluates whether the overlay should be shown based on foreground app.
     */
    fun evaluateForeground(): ServiceAction {
        if (!hasUsageStatsPermission()) {
            if (isOverlayActive) {
                isOverlayActive = false
                return ServiceAction.HideAndNotifyPermissionLost
            }
            return ServiceAction.None
        }

        val foregroundPackage = foregroundDetector.getForegroundPackage()

        if (ForegroundDetector.isPixelCameraPackage(foregroundPackage)) {
            if (!isOverlayActive) {
                if (!hasOverlayPermission()) {
                    return ServiceAction.NotifyOverlayPermissionLost
                }
                isOverlayActive = true
                val startSession = isKeyguardLocked()
                return ServiceAction.ShowOverlay(startSession = startSession)
            }
        }
        return ServiceAction.None
    }
}

/**
 * Actions that the service should execute in response to logic decisions.
 */
sealed class ServiceAction {
    object None : ServiceAction()
    data class ShowOverlay(val startSession: Boolean) : ServiceAction()
    data class ScheduleDeactivation(val delayMs: Long) : ServiceAction()
    object Deactivate : ServiceAction()
    object HideAndNotifyPermissionLost : ServiceAction()
    object NotifyOverlayPermissionLost : ServiceAction()
}
