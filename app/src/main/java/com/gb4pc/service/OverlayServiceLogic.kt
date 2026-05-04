package com.gb4pc.service

import android.os.Handler
import com.gb4pc.Constants
import com.gb4pc.overlay.OverlayManager
import com.gb4pc.util.DebugLog
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
        DebugLog.log("Logic: camera $cameraId unavailable; unavailable=${cameraState.getUnavailableCameraIds()}")
        cancelPendingDeactivation()
        evaluateForeground()
    }

    fun onCameraAvailable(cameraId: String) {
        cameraState.setCameraAvailable(cameraId)
        val allAvailable = cameraState.areAllCamerasAvailable()
        DebugLog.log("Logic: camera $cameraId available; allAvailable=$allAvailable, remaining=${cameraState.getUnavailableCameraIds()}, overlayActive=$isOverlayActive")
        // Issue #89: If the overlay is not active there is nothing to deactivate.
        if (!isOverlayActive) {
            cancelActivationRetry()
            return
        }
        // DT-04/DT-05: Only schedule deactivation when ALL cameras have been released
        if (allAvailable) {
            cancelActivationRetry()
            // Issue #46: If Pixel Camera is no longer in the foreground, skip the debounce delay.
            // The debounce is only needed for transient camera switches (where hardware briefly
            // shows available between switching cameras); for a true app-close we want 0 ms.
            //
            // Issue #81: When the device is locked, UsageStats returns null for the foreground
            // package, so isPixelCameraPackage() would always be false — incorrectly using 0 ms
            // and prematurely hiding the overlay during a transient camera switch on the lock
            // screen.  Take the conservative (debounceMs) path whenever the keyguard is locked.
            val delay = if (isKeyguardLocked()) {
                debounceMs
            } else {
                val pkg = foregroundDetector.getForegroundPackage()
                if (ForegroundDetector.isPixelCameraPackage(pkg)) debounceMs else 0L
            }
            DebugLog.log("Logic: all cameras released; scheduling deactivation delay=${delay}ms (keyguardLocked=${isKeyguardLocked()})")
            scheduleDeactivation(delay)
        }
    }

    // ── Core logic ──────────────────────────────────────────────────────────

    /**
     * DT-02/DT-03: Check if Pixel Camera is the foreground app and show/hide overlay.
     * DT-06a: If the foreground event hasn't appeared in UsageStats yet (lag), schedule a retry.
     *
     * Lock-screen bypass (Issue #81): When the device is locked,
     * [android.app.usage.UsageStatsManager] does not emit MOVE_TO_FOREGROUND events, so
     * [ForegroundDetector.getForegroundPackage] always returns null regardless of which app
     * opened the camera.  On a locked device the camera can only be launched via the lock-screen
     * camera shortcut — which is always Pixel Camera — so we bypass the UsageStats check and
     * activate the overlay directly.
     */
    fun evaluateForeground() {
        if (!hasUsageStatsPermission()) {
            DebugLog.log("Logic: evaluateForeground — usage-stats permission missing; overlayActive=$isOverlayActive")
            if (isOverlayActive) {
                overlayManager.hide()
                isOverlayActive = false
                onOverlayStateChanged(false)
                onUsageAccessLost()
            }
            cancelActivationRetry()
            return
        }

        // Issue #81: UsageStats does not report foreground events while the device is locked.
        // When locked and the camera is in use, skip the UsageStats lookup entirely:
        // - If the overlay is not yet active, activate it (the lock-screen camera shortcut can
        //   only launch Pixel Camera).
        // - If the overlay is already active, return early so we never call getForegroundPackage()
        //   on a locked device (it would return null and could cause incorrect side-effects).
        if (isKeyguardLocked() && cameraState.anyCameraUnavailable()) {
            if (!isOverlayActive) {
                DebugLog.log("Logic: evaluateForeground — device locked with camera in use; activating overlay without UsageStats lookup")
                cancelActivationRetry()
                showOverlay()
            } else {
                DebugLog.log("Logic: evaluateForeground — device locked, overlay already active; skipping UsageStats lookup")
            }
            return
        }

        val pkg = foregroundDetector.getForegroundPackage()
        val isPixelCamera = ForegroundDetector.isPixelCameraPackage(pkg)
        DebugLog.log("Logic: evaluateForeground — overlayActive=$isOverlayActive, anyCameraUnavailable=${cameraState.anyCameraUnavailable()}")

        if (isPixelCamera && !isOverlayActive) {
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
            DebugLog.log("Logic: showOverlay — overlay permission missing; cannot show")
            onOverlayPermissionLost()
            return
        }
        val locked = isKeyguardLocked()
        DebugLog.log("Logic: showOverlay — showing overlay; keyguardLocked=$locked")
        overlayManager.show()
        isOverlayActive = true
        onOverlayStateChanged(true)
        onRegisterThumbnailObserver()   // always register thumbnail observer on activation

        // SF-01: If device is locked at activation time, begin a secure session immediately.
        // H3: If unlocked, onScreenOff() will start the session when the screen locks.
        if (locked) {
            DebugLog.log("Logic: device locked at activation — starting secure session immediately")
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
        DebugLog.log("Logic: scheduleDeactivation delay=${delayMs}ms")
        cancelPendingDeactivation()
        deactivateRunnable = Runnable {
            val allAvailable = cameraState.areAllCamerasAvailable()
            DebugLog.log("Logic: deactivation runnable fired; allCamerasAvailable=$allAvailable, overlayActive=$isOverlayActive")
            if (allAvailable) {
                hideOverlayAndCleanup()
            } else {
                DebugLog.log("Logic: deactivation skipped — camera still in use")
            }
        }
        handler.postDelayed(deactivateRunnable!!, delayMs)
    }

    /**
     * Hides the overlay, marks it inactive, fires [onOverlayStateChanged], unregisters the
     * thumbnail observer, and ends any active secure session (unregistering the media observer).
     *
     * This is the single place responsible for all teardown that must accompany every hide —
     * used by [scheduleDeactivation], [onGalleryLaunched], and [scheduleGalleryLaunchRecheck].
     */
    private fun hideOverlayAndCleanup() {
        overlayManager.hide()
        isOverlayActive = false
        onOverlayStateChanged(false)
        onUnregisterThumbnailObserver()   // unregister thumbnail observer on deactivation
        if (sessionTracker.isSessionActive) {
            DebugLog.log("Logic: ending secure session on overlay deactivation")
            sessionTracker.endSession()
            onUnregisterMediaObserver()
        }
    }

    fun cancelPendingDeactivation() {
        deactivateRunnable?.let {
            DebugLog.log("Logic: cancelling pending deactivation")
            handler.removeCallbacks(it)
            deactivateRunnable = null
        }
    }

    // DT-06a: Retry activation after UsageStats lag — one shot per camera-open event.
    private fun scheduleActivationRetry() {
        if (activationRetryPending) return  // already scheduled or currently executing
        DebugLog.log("Logic: scheduling activation retry in ${Constants.ACTIVATION_RETRY_MS}ms")
        activationRetryPending = true
        val runnable = Runnable {
            activationRetryRunnable = null
            DebugLog.log("Logic: activation retry firing")
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
            DebugLog.log("Logic: cancelling pending activation retry")
            handler.removeCallbacks(it)
            activationRetryRunnable = null
        }
        activationRetryPending = false
    }

    /** Called from onDestroy to clean up mutable state. */
    fun reset() {
        cancelPendingDeactivation()
        cancelActivationRetry()
        cancelGalleryLaunchRecheck()
        onUnregisterThumbnailObserver()
        isOverlayActive = false
    }

    private fun cancelGalleryLaunchRecheck() {
        galleryLaunchRecheckRunnable?.let {
            DebugLog.log("Logic: cancelling pending gallery-launch re-check")
            handler.removeCallbacks(it)
            galleryLaunchRecheckRunnable = null
        }
    }

    // ── Gallery-launch early hide (Issue #91) ───────────────────────────────

    /**
     * Called immediately after the gallery app is launched via the overlay button (Issue #91).
     *
     * Launching the gallery normally closes Pixel Camera. The camera-available event has high
     * latency, so we check the foreground app proactively:
     * - If PC is no longer in the foreground → hide the overlay immediately.
     * - If PC is still in the foreground (e.g. split-screen) → schedule a one-shot re-check
     *   after [debounceMs]; hide if PC is gone by then.
     *
     * Note: on a locked device [getForegroundPackage] returns null (UsageStats does not emit
     * MOVE_TO_FOREGROUND events while the keyguard is up), so [isPixelCameraPackage] will be
     * false and we always take the immediate-hide path. This is intentional: the gallery button
     * is not visible on the lock screen, so if we reach this code the device must have been
     * unlocked when the button was pressed — hiding immediately is correct.
     */
    fun onGalleryLaunched() {
        if (!isOverlayActive) return
        DebugLog.log("Logic: gallery launched — checking foreground app (Issue #91)")
        // Cancel any pending deactivation so its runnable cannot fire after we hide here,
        // which could call hide() a second time or unregister observers on a fresh activation.
        cancelPendingDeactivation()
        val pkg = foregroundDetector.getForegroundPackage()
        if (!ForegroundDetector.isPixelCameraPackage(pkg)) {
            DebugLog.log("Logic: PC not in foreground after gallery launch — hiding overlay immediately")
            hideOverlayAndCleanup()
        } else {
            DebugLog.log("Logic: PC still in foreground after gallery launch — scheduling re-check in ${debounceMs}ms")
            scheduleGalleryLaunchRecheck()
        }
    }

    private var galleryLaunchRecheckRunnable: Runnable? = null

    private fun scheduleGalleryLaunchRecheck() {
        galleryLaunchRecheckRunnable?.let { handler.removeCallbacks(it) }
        val runnable = Runnable {
            galleryLaunchRecheckRunnable = null
            if (!isOverlayActive) return@Runnable
            val pkg = foregroundDetector.getForegroundPackage()
            DebugLog.log("Logic: gallery-launch re-check fired — foreground=$pkg, overlayActive=$isOverlayActive")
            if (!ForegroundDetector.isPixelCameraPackage(pkg)) {
                hideOverlayAndCleanup()
            }
        }
        galleryLaunchRecheckRunnable = runnable
        handler.postDelayed(runnable, debounceMs)
    }

    // ── Focusable-overlay focus callbacks (Issue #55) ────────────────────────

    /**
     * Called by [com.gb4pc.overlay.OverlayManager] when the overlay window loses focus.
     * Indicates a system surface (task-switcher, notification shade, etc.) has covered the
     * camera app. Hides the overlay so it does not obscure the system surface.
     * Only fired when the experimental focusable-overlay preference is enabled.
     */
    fun onOverlayFocusLost() {
        DebugLog.log("Logic: overlay focus lost; overlayActive=$isOverlayActive")
        if (!isOverlayActive) return
        overlayManager.hide()
        isOverlayActive = false
        onOverlayStateChanged(false)
    }

    /**
     * Called by [com.gb4pc.overlay.OverlayManager] when the overlay window regains focus.
     * Indicates the camera app is back in the foreground. Re-shows the overlay.
     * Only fired when the experimental focusable-overlay preference is enabled.
     */
    fun onOverlayFocusGained() {
        DebugLog.log("Logic: overlay focus gained; overlayActive=$isOverlayActive")
        if (isOverlayActive) return
        if (!hasOverlayPermission()) {
            DebugLog.log("Logic: overlay focus gained but overlay permission missing")
            onOverlayPermissionLost()
            return
        }
        overlayManager.show()
        isOverlayActive = true
        onOverlayStateChanged(true)
    }
}
