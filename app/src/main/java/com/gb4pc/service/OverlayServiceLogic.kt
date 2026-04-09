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
    private val foregroundDetector: ForegroundDetector,
    private val sessionTracker: SessionTracker,
    private val handler: Handler,
    private val debounceMs: Long = Constants.CAMERA_DEBOUNCE_MS,
    /** Called when usage-stats permission is lost while the overlay is active (PM-03). */
    private val onUsageAccessLost: () -> Unit,
    /** Called when the overlay DRAW_OVERLAYS permission is missing on showOverlay() (PM-04). */
    private val onOverlayPermissionLost: () -> Unit,
    private val isKeyguardLocked: () -> Boolean,
    private val onRegisterMediaObserver: () -> Unit,
    private val onUnregisterMediaObserver: () -> Unit,
    /** Called whenever the overlay visibility changes; default no-op. Used by tests and UI. */
    private val onOverlayStateChanged: (Boolean) -> Unit = {},
) {
    var isOverlayActive: Boolean = false
        private set

    private var deactivateRunnable: Runnable? = null
    // DT-06a: Retry runnable for UsageStats lag — fires if foreground not detected on first check.
    private var activationRetryRunnable: Runnable? = null

    // ── Camera callback delegation ──────────────────────────────────────────

    fun onCameraUnavailable(cameraId: String) {
        cameraState.setCameraUnavailable(cameraId)
        cancelPendingDeactivation()
        evaluateForeground()
    }

    fun onCameraAvailable(cameraId: String) {
        cameraState.setCameraAvailable(cameraId)
        // DT-04/DT-05: Only schedule deactivation when ALL cameras have been released
        if (cameraState.areAllCamerasAvailable()) {
            scheduleDeactivation()
        }
    }

    // ── Core logic ──────────────────────────────────────────────────────────

    /**
     * DT-02/DT-03: Check if Pixel Camera is the foreground app and show/hide overlay.
     * DT-06a: If the foreground event hasn't appeared in UsageStats yet (lag), schedule a retry.
     */
    fun evaluateForeground() {
        if (!hasUsageStatsPermission()) {
            if (isOverlayActive) {
                overlayManager.hide()
                isOverlayActive = false
                onOverlayStateChanged(false)
                onUsageAccessLost()
            }
            cancelActivationRetry()
            return
        }

        val pkg = foregroundDetector.getForegroundPackage()
        if (ForegroundDetector.isPixelCameraPackage(pkg) && !isOverlayActive) {
            cancelActivationRetry()
            showOverlay()
        } else if (!isOverlayActive && cameraState.anyCameraUnavailable()) {
            // UsageStats may not have caught up yet; schedule a retry (DT-06a).
            scheduleActivationRetry()
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
        onOverlayStateChanged(true)

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
                onOverlayStateChanged(false)
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

    // DT-06a: Retry activation after UsageStats lag.
    private fun scheduleActivationRetry() {
        if (activationRetryRunnable != null) return  // already scheduled
        activationRetryRunnable = Runnable {
            activationRetryRunnable = null
            evaluateForeground()
        }
        handler.postDelayed(activationRetryRunnable!!, Constants.ACTIVATION_RETRY_MS)
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
