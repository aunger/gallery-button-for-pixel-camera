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
    private val onRegisterThumbnailObserver: () -> Unit = {},
    private val onUnregisterThumbnailObserver: () -> Unit = {},
    /** Called whenever the overlay visibility changes; default no-op. Used by tests and UI. */
    private val onOverlayStateChanged: (Boolean) -> Unit = {},
) {
    var isOverlayActive: Boolean = false
        private set

    private var deactivateRunnable: Runnable? = null
    // DT-06a: Retry runnable for UsageStats lag — fires if foreground not detected on first check.
    // activationRetryPending gates re-scheduling: it stays true while the runnable is executing
    // so that evaluateForeground() inside the runnable cannot queue a second retry.
    private var activationRetryRunnable: Runnable? = null
    private var activationRetryPending = false

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
            cancelActivationRetry()
            // Issue #46: If Pixel Camera is no longer in the foreground, skip the debounce delay.
            // The debounce is only needed for transient camera switches (where hardware briefly
            // shows available between switching cameras); for a true app-close we want 0 ms.
            val pkg = foregroundDetector.getForegroundPackage()
            val delay = if (ForegroundDetector.isPixelCameraPackage(pkg)) debounceMs else 0L
            scheduleDeactivation(delay)
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
        onRegisterThumbnailObserver()   // always register thumbnail observer on activation

        // SF-01: If device is locked at activation time, begin a secure session immediately.
        // H3: If unlocked, onScreenOff() will start the session when the screen locks.
        if (isKeyguardLocked()) {
            sessionTracker.startSession()
            onRegisterMediaObserver()
        }
    }

    /**
     * DT-04: Schedule overlay deactivation with an optional delay.
     * Defaults to [debounceMs] for transient camera switches; pass 0L when the app has already
     * left the foreground (Issue #46).
     */
    fun scheduleDeactivation(delayMs: Long = debounceMs) {
        cancelPendingDeactivation()
        deactivateRunnable = Runnable {
            if (cameraState.areAllCamerasAvailable()) {
                overlayManager.hide()
                isOverlayActive = false
                onOverlayStateChanged(false)
                onUnregisterThumbnailObserver()   // unregister thumbnail observer on deactivation
                if (sessionTracker.isSessionActive) {
                    sessionTracker.endSession()
                    onUnregisterMediaObserver()
                }
            }
        }
        handler.postDelayed(deactivateRunnable!!, delayMs)
    }

    fun cancelPendingDeactivation() {
        deactivateRunnable?.let {
            handler.removeCallbacks(it)
            deactivateRunnable = null
        }
    }

    // DT-06a: Retry activation after UsageStats lag — one shot per camera-open event.
    private fun scheduleActivationRetry() {
        if (activationRetryPending) return  // already scheduled or currently executing
        activationRetryPending = true
        val runnable = Runnable {
            activationRetryRunnable = null
            // activationRetryPending stays true while evaluateForeground() runs, so any
            // scheduleActivationRetry() call inside cannot queue a second retry.
            evaluateForeground()
            activationRetryPending = false
        }
        activationRetryRunnable = runnable
        handler.postDelayed(runnable, Constants.ACTIVATION_RETRY_MS)
    }

    private fun cancelActivationRetry() {
        activationRetryRunnable?.let {
            handler.removeCallbacks(it)
            activationRetryRunnable = null
        }
        activationRetryPending = false
    }

    /** Called from onDestroy to clean up mutable state. */
    fun reset() {
        cancelPendingDeactivation()
        cancelActivationRetry()
        onUnregisterThumbnailObserver()
        isOverlayActive = false
    }
}
