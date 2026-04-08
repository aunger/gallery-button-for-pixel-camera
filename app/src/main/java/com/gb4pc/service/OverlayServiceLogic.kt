package com.gb4pc.service

import android.os.Handler
import com.gb4pc.Constants
import com.gb4pc.overlay.OverlayManager
import com.gb4pc.viewer.SessionTracker

/**
 * Core overlay activation / deactivation logic extracted for unit-testability.
 *
 * All Android-framework side-effects are accessed only through constructor lambdas,
 * so this class can be exercised in plain JVM unit tests without Robolectric.
 *
 * Wired to [OverlayService] via constructor injection in onCreate().
 */
class OverlayServiceLogic(
    private val hasUsageStatsPermission: () -> Boolean,
    private val hasOverlayPermission: () -> Boolean,
    private val overlayManager: OverlayManager,
    private val cameraState: CameraState,
    private val foregroundDetector: ForegroundDetectorPort,
    private val sessionTracker: SessionTracker,
    private val handler: Handler,
    private val debounceMs: Long = Constants.CAMERA_DEBOUNCE_MS,
    private val activationRetryMs: Long = Constants.ACTIVATION_RETRY_MS,
    /** Called when usage-stats permission is lost while the overlay is active (PM-03). */
    private val onUsageAccessLost: () -> Unit,
    /** Called when the overlay DRAW_OVERLAYS permission is missing on showOverlay() (PM-04). */
    private val onOverlayPermissionLost: () -> Unit,
    private val isKeyguardLocked: () -> Boolean,
    private val onRegisterMediaObserver: () -> Unit,
    private val onUnregisterMediaObserver: () -> Unit,
) {
    var isOverlayActive: Boolean = false
        private set

    private var deactivateRunnable: Runnable? = null
    private var activationRetryRunnable: Runnable? = null

    // ── Camera callback delegation ──────────────────────────────────────────

    fun onCameraUnavailable(cameraId: String) {
        cameraState.setCameraUnavailable(cameraId)
        cancelPendingDeactivation()
        evaluateForeground()
        // EC-09: If UsageStats hasn't delivered the MOVE_TO_FOREGROUND event yet, the
        // overlay won't have activated. Schedule one retry so the overlay appears even
        // when the camera opens before UsageStats catches up.
        if (!isOverlayActive) {
            scheduleActivationRetry()
        }
    }

    fun onCameraAvailable(cameraId: String) {
        cameraState.setCameraAvailable(cameraId)
        // DT-04/DT-05: Only schedule deactivation when ALL cameras have been released
        if (cameraState.areAllCamerasAvailable()) {
            cancelActivationRetry()
            scheduleDeactivation()
        }
    }

    // ── Core logic ──────────────────────────────────────────────────────────

    /**
     * DT-02/DT-03: Check if Pixel Camera is the foreground app and show/hide overlay.
     */
    fun evaluateForeground() {
        if (!hasUsageStatsPermission()) {
            if (isOverlayActive) {
                overlayManager.hide()
                isOverlayActive = false
                onUsageAccessLost()
            }
            return
        }

        val pkg = foregroundDetector.getForegroundPackage()
        if (ForegroundDetector.isPixelCameraPackage(pkg) && !isOverlayActive) {
            showOverlay()
        }
    }

    /**
     * PM-04 / SF-01: Show the overlay, guarding against missing permissions.
     * Starts a secure session if the device is already locked at activation time.
     */
    fun showOverlay() {
        if (!hasOverlayPermission()) {
            onOverlayPermissionLost()
            return
        }
        overlayManager.show()
        isOverlayActive = true

        // SF-01: If device is locked at activation time, begin a secure session immediately.
        // H3: If unlocked, onScreenOff() will start the session when the screen locks.
        if (isKeyguardLocked()) {
            sessionTracker.startSession()
            onRegisterMediaObserver()
        }
    }

    /**
     * DT-04: Schedule overlay deactivation with a debounce delay.
     */
    fun scheduleDeactivation() {
        cancelPendingDeactivation()
        deactivateRunnable = Runnable {
            if (cameraState.areAllCamerasAvailable()) {
                overlayManager.hide()
                isOverlayActive = false
                if (sessionTracker.isSessionActive) {
                    sessionTracker.endSession()
                    onUnregisterMediaObserver()
                }
            }
        }
        handler.postDelayed(deactivateRunnable!!, debounceMs)
    }

    fun cancelPendingDeactivation() {
        deactivateRunnable?.let {
            handler.removeCallbacks(it)
            deactivateRunnable = null
        }
    }

    private fun scheduleActivationRetry() {
        cancelActivationRetry()
        activationRetryRunnable = Runnable {
            activationRetryRunnable = null
            if (cameraState.anyCameraUnavailable() && !isOverlayActive) {
                evaluateForeground()
            }
        }
        handler.postDelayed(activationRetryRunnable!!, activationRetryMs)
    }

    private fun cancelActivationRetry() {
        activationRetryRunnable?.let {
            handler.removeCallbacks(it)
            activationRetryRunnable = null
        }
    }

    /** Called from onDestroy to clean up mutable state. */
    fun reset() {
        cancelPendingDeactivation()
        cancelActivationRetry()
        isOverlayActive = false
    }
}
