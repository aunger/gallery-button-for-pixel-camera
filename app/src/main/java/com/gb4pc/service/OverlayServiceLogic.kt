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

    // Issue #608: set when the secure viewer is launched from a locked-screen tap.
    // Launching the viewer closes Pixel Camera, which releases the camera and drives the
    // overlay's normal deactivation. That deactivation must hide the overlay WITHOUT ending
    // the secure session, because the just-opened SecureViewerActivity renders that session's
    // media reactively (#552); ending it would empty the flow and flip the viewer to the
    // "no photos" state (the reported flash). The session instead ends on device unlock
    // (OverlayService.onUserPresent), per SF-01(b). The flag is one-shot: it is consumed by the
    // next hideOverlayAndCleanup() and cleared on any fresh activation.
    private var secureViewerLaunched = false

    // DT-06a: Retry runnable for UsageStats lag--fires if foreground not detected on first check.
    // activationRetryPending gates re-scheduling: it is true exactly while a retry runnable is
    // posted to the handler, so a burst of evaluateForeground() calls (e.g. several camera events)
    // cannot stack multiple retries for the same lag.
    //
    // UsageStats can lag the camera callback by more than one ACTIVATION_RETRY_MS interval, so the
    // retry is not one-shot: when a fired retry still finds no foreground package, it re-schedules
    // itself until the overlay activates, the camera is released, or
    // Constants.ACTIVATION_RETRY_MAX_ATTEMPTS attempts have been made. activationRetryAttempts
    // counts attempts made in the current camera-open sequence and is reset by cancelActivationRetry().
    private var activationRetryRunnable: Runnable? = null
    private var activationRetryPending = false
    private var activationRetryAttempts = 0

    // Issue #907: episodes of the Issue #86 race seen by this instance, i.e. over the lifetime of
    // the service. cameraForegroundRaceEpisode is the running total and never resets; it is the
    // number the log signal reports. openRaceEpisode is the episode currently being observed, or
    // 0 when none is: it suppresses the duplicate sightings the DT-06a retry chain would otherwise
    // produce, and lives exactly as long as the camera-open sequence it belongs to. It ends either
    // at [logCameraForegroundRaceResolved], when the overlay really did appear, or silently at
    // [closeRaceEpisodeUnresolved] when the cameras are released without one. See
    // [logCameraForegroundRace].
    private var cameraForegroundRaceEpisode = 0
    private var openRaceEpisode = 0

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
        DebugLog.log(
            "Logic: camera $cameraId available; allAvailable=$allAvailable, remaining=${cameraState.getUnavailableCameraIds()}, overlayActive=$isOverlayActive",
        )
        // Issue #907: the camera-open sequence is over once every camera is back. Any race
        // episode still open at this point never ended in an overlay, which is the miss the
        // signal exists to count, so it closes here without a resolution line.
        if (allAvailable) closeRaceEpisodeUnresolved()
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
            // package, so isPixelCameraPackage() would always be false, incorrectly using 0 ms
            // and prematurely hiding the overlay during a transient camera switch on the lock
            // screen.  Take the conservative (debounceMs) path whenever the keyguard is locked.
            val delay =
                if (isKeyguardLocked()) {
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
     * camera shortcut (which is always Pixel Camera), so we bypass the UsageStats check and
     * activate the overlay directly.
     */
    fun evaluateForeground() {
        if (!hasUsageStatsPermission()) {
            DebugLog.log("Logic: evaluateForeground: usage-stats permission missing; overlayActive=$isOverlayActive")
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
                DebugLog.log("Logic: evaluateForeground: device locked with camera in use; activating overlay without UsageStats lookup")
                cancelActivationRetry()
                showOverlay("lock-screen bypass, Issue #81")
            } else {
                DebugLog.log("Logic: evaluateForeground: device locked, overlay already active; skipping UsageStats lookup")
            }
            return
        }

        val pkg = foregroundDetector.getForegroundPackage()
        val isPixelCamera = ForegroundDetector.isPixelCameraPackage(pkg)
        val cameraHeld = cameraState.anyCameraUnavailable()
        DebugLog.log(
            "Logic: evaluateForeground: overlayActive=$isOverlayActive, anyCameraUnavailable=$cameraHeld",
        )
        if (!isPixelCamera && cameraHeld) logCameraForegroundRace(pkg)

        if (isPixelCamera && !isOverlayActive) {
            cancelActivationRetry()
            showOverlay("Pixel Camera won a later foreground lookup")
        } else if (!isOverlayActive && cameraHeld) {
            // UsageStats may not have caught up yet; schedule a retry (DT-06a).
            scheduleActivationRetry()
        }
    }

    /**
     * Issue #907: logs the fingerprint of the Issue #86 race as one named, counted signal.
     *
     * The caller has already established the first two thirds of that fingerprint: a camera is
     * held, and the latest foreground event belongs to some app other than Pixel Camera. This adds
     * the third, that Pixel Camera nevertheless produced a foreground event inside the same query
     * window, so it is an app the detector saw and did not pick. That combination is exactly the
     * case where Pixel Camera holds the camera while a later event from another app wins the
     * foreground lookup, and today it is silent: the overlay simply does not appear, and the
     * evidence is split across a [ForegroundDetector] log line and an [evaluateForeground] one.
     *
     * Counted per *episode*, not per evaluation, because those are very different numbers. One
     * camera open drives [evaluateForeground] up to
     * `1 + `[Constants.ACTIVATION_RETRY_MAX_ATTEMPTS] times as the DT-06a retry chain re-checks
     * for UsageStats lag, and for a race the whole chain fits inside
     * [Constants.USAGE_STATS_WINDOW_MS], so every attempt re-reads the same window and re-sees the
     * same fingerprint. Logging each of those would report a single failure as six, and an
     * analyst counting lines on a device would overstate the rate by that factor. Only the first
     * sighting of an episode is logged; the episode closes when every camera is released, so the
     * next camera-open sequence reports again.
     *
     * An episode that ends with the overlay appearing anyway was not a missed overlay, and
     * [logCameraForegroundRaceResolved] marks it as such so those can be subtracted from the
     * count. It is called from the two places the overlay actually becomes visible, both past
     * their own permission guard, so an activation that PM-04 refuses is never claimed as one.
     * An episode with no resolution line is therefore a camera open where the overlay never
     * appeared, whether it was the race, a revoked permission, or anything else.
     *
     * Purely diagnostic. It reports the condition and changes nothing about activation, so that a
     * fix for #86 can be chosen (or declined) against evidence of how often this really fires.
     *
     * The message carries `overlayActive` because the condition is benign while the overlay is
     * already up: another app has come to the front over a camera the overlay is already tracking.
     * Note that `overlayActive=false` alone does not prove a miss either. Issue #91's
     * [onGalleryLaunched] hides the overlay while Pixel Camera may still hold the camera, so a
     * camera event in that gap logs the fingerprint with nothing wrong. Weigh an episode by its
     * resolution line and its surrounding log context, not by the flag alone.
     */
    private fun logCameraForegroundRace(foregroundPackage: String?) {
        val candidates = foregroundDetector.lastForegroundCandidates
        if (Constants.PIXEL_CAMERA_PACKAGE !in candidates) return
        if (openRaceEpisode != 0) return // same episode, re-observed by the DT-06a retry chain
        cameraForegroundRaceEpisode++
        openRaceEpisode = cameraForegroundRaceEpisode
        DebugLog.log(
            "Logic: $CAMERA_FOREGROUND_RACE #$cameraForegroundRaceEpisode: camera held " +
                "(unavailable=${cameraState.getUnavailableCameraIds()}) but foreground=$foregroundPackage " +
                "while ${Constants.PIXEL_CAMERA_PACKAGE} is among the window's FG apps=$candidates; " +
                "overlayActive=$isOverlayActive (Issue #86)",
        )
    }

    /**
     * Issue #907: closes an open race episode that ended with the overlay activating anyway.
     *
     * If a race was reported for this camera-open sequence and the overlay then appeared, the
     * episode was not the silent failure #86 describes, whatever eventually put the overlay up.
     * Saying so on the same marker keeps both numbers greppable: episodes reported, and of those,
     * episodes that recovered. The difference is the number #86 is waiting on, which is what makes
     * the absence of this line load-bearing: it is what lets an episode read as a real miss.
     *
     * That rule holds only if this is called wherever the overlay becomes visible, and *after*
     * whatever guard could still refuse. So it lives at the two places `isOverlayActive` turns
     * true -- inside [showOverlay] past the PM-04 check, and in [onOverlayFocusGained] past its
     * own -- rather than at the call sites that lead there, which cannot see an early return.
     * That also means a future caller of [showOverlay] is accounted for without knowing this
     * exists. [how] is supplied by the caller, since only it knows why the overlay went up.
     *
     * The only other way an episode may end is [closeRaceEpisodeUnresolved], the miss, which is
     * silent. Clears the episode itself so neither path double-reports one.
     */
    private fun logCameraForegroundRaceResolved(how: String?) {
        if (openRaceEpisode == 0) return
        val cause = if (how == null) "" else " ($how)"
        DebugLog.log(
            "Logic: $CAMERA_FOREGROUND_RACE #$openRaceEpisode resolved$cause: the overlay " +
                "activated after all; this episode was not a missed overlay",
        )
        openRaceEpisode = 0
    }

    /**
     * Issue #907: closes an open race episode that ended without the overlay ever appearing.
     *
     * Deliberately silent. The absence of a resolution line is what marks an episode as a real
     * miss, so this only frees the number for the next camera-open sequence to use.
     *
     * Called when every camera has been released, which is the honest end of a camera-open
     * sequence, and on teardown. Not from [cancelActivationRetry]: that runs mid-sequence too,
     * immediately before each activation attempt, and closing the episode there would retire it a
     * moment before the code could find out whether the overlay actually appeared.
     */
    private fun closeRaceEpisodeUnresolved() {
        openRaceEpisode = 0
    }

    /**
     * PM-04 / SF-01: Show the overlay, guarding against missing permissions.
     * Starts a secure session if the device is already locked at activation time.
     *
     * @param raceResolutionCause Issue #907: why this activation happened, for the resolution
     *   line of an open race episode. Passed in because only the caller knows, and read only
     *   after the PM-04 guard, so an activation this method refuses never claims to have
     *   happened. Null (the default) still resolves the episode, just without naming a cause:
     *   the overlay is up either way, which is the part the metric counts.
     */
    fun showOverlay(raceResolutionCause: String? = null) {
        if (!hasOverlayPermission()) {
            DebugLog.log("Logic: showOverlay: overlay permission missing; cannot show")
            onOverlayPermissionLost()
            return
        }
        logCameraForegroundRaceResolved(raceResolutionCause)
        val locked = isKeyguardLocked()
        DebugLog.log("Logic: showOverlay: showing overlay; keyguardLocked=$locked")
        // Issue #608: a fresh activation starts a new session; clear any stale
        // secure-viewer-launch preservation flag left over from a prior cycle.
        secureViewerLaunched = false
        overlayManager.show()
        isOverlayActive = true
        onOverlayStateChanged(true)
        onRegisterThumbnailObserver() // always register thumbnail observer on activation

        // SF-01: If device is locked at activation time, begin a secure session immediately.
        // H3: If unlocked, onScreenOff() will start the session when the screen locks.
        if (locked) {
            DebugLog.log("Logic: device locked at activation; starting secure session immediately")
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
        deactivateRunnable =
            Runnable {
                val allAvailable = cameraState.areAllCamerasAvailable()
                DebugLog.log("Logic: deactivation runnable fired; allCamerasAvailable=$allAvailable, overlayActive=$isOverlayActive")
                if (allAvailable) {
                    hideOverlayAndCleanup()
                } else {
                    DebugLog.log("Logic: deactivation skipped; camera still in use")
                }
            }
        handler.postDelayed(deactivateRunnable!!, delayMs)
    }

    /**
     * Hides the overlay, marks it inactive, fires [onOverlayStateChanged], unregisters the
     * thumbnail observer, and ends any active secure session (unregistering the media observer).
     *
     * This is the single place responsible for all teardown that must accompany every hide,
     * used by [scheduleDeactivation], [onGalleryLaunched], and [scheduleGalleryLaunchRecheck].
     */
    private fun hideOverlayAndCleanup() {
        overlayManager.hide()
        isOverlayActive = false
        onOverlayStateChanged(false)
        onUnregisterThumbnailObserver() // unregister thumbnail observer on deactivation
        if (secureViewerLaunched) {
            // Issue #608: the overlay is being torn down only because launching the secure
            // viewer closed Pixel Camera. Keep the secure session (and its media observer)
            // alive so the open SecureViewerActivity keeps rendering the session's media
            // instead of flashing to the empty state. The session ends on unlock (SF-01(b)).
            DebugLog.log("Logic: overlay deactivated after secure-viewer launch; keeping secure session alive")
            secureViewerLaunched = false
            return
        }
        if (sessionTracker.isSessionActive) {
            DebugLog.log("Logic: ending secure session on overlay deactivation")
            sessionTracker.endSession()
            onUnregisterMediaObserver()
        }
    }

    /**
     * Issue #608: Called immediately after the secure viewer is launched from a locked-screen tap.
     * Marks the next overlay deactivation (caused by Pixel Camera closing behind the viewer) so it
     * preserves the secure session rather than ending it; see [hideOverlayAndCleanup].
     */
    fun onSecureViewerLaunched() {
        if (!isOverlayActive) return
        DebugLog.log("Logic: secure viewer launched; preserving session across the ensuing overlay deactivation")
        secureViewerLaunched = true
    }

    fun cancelPendingDeactivation() {
        deactivateRunnable?.let {
            DebugLog.log("Logic: cancelling pending deactivation")
            handler.removeCallbacks(it)
            deactivateRunnable = null
        }
    }

    // DT-06a: Retry activation after UsageStats lag. Re-schedules itself up to
    // ACTIVATION_RETRY_MAX_ATTEMPTS times per camera-open event so that UsageStats lag longer than
    // a single ACTIVATION_RETRY_MS interval is still tolerated.
    private fun scheduleActivationRetry() {
        if (activationRetryPending) return // a retry is already posted for this lag
        if (activationRetryAttempts >= Constants.ACTIVATION_RETRY_MAX_ATTEMPTS) {
            DebugLog.log(
                "Logic: activation retry budget exhausted after $activationRetryAttempts attempts; " +
                    "giving up until the next camera-open event",
            )
            return
        }
        activationRetryAttempts++
        DebugLog.log(
            "Logic: scheduling activation retry $activationRetryAttempts/" +
                "${Constants.ACTIVATION_RETRY_MAX_ATTEMPTS} in ${Constants.ACTIVATION_RETRY_MS}ms",
        )
        activationRetryPending = true
        val runnable =
            Runnable {
                activationRetryRunnable = null
                // Clear the pending flag before evaluating so that, if UsageStats still has not caught
                // up, evaluateForeground() can schedule the next attempt (up to the attempt cap).
                activationRetryPending = false
                DebugLog.log("Logic: activation retry firing (attempt $activationRetryAttempts)")
                evaluateForeground()
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
        activationRetryAttempts = 0
    }

    /** Called from onDestroy to clean up mutable state. */
    fun reset() {
        cancelPendingDeactivation()
        cancelActivationRetry()
        cancelGalleryLaunchRecheck()
        closeRaceEpisodeUnresolved()
        onUnregisterThumbnailObserver()
        isOverlayActive = false
        secureViewerLaunched = false
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
     * unlocked when the button was pressed; hiding immediately is correct.
     */
    fun onGalleryLaunched() {
        if (!isOverlayActive) return
        DebugLog.log("Logic: gallery launched; checking foreground app (Issue #91)")
        // Cancel any pending deactivation so its runnable cannot fire after we hide here,
        // which could call hide() a second time or unregister observers on a fresh activation.
        cancelPendingDeactivation()
        val pkg = foregroundDetector.getForegroundPackage()
        if (!ForegroundDetector.isPixelCameraPackage(pkg)) {
            DebugLog.log("Logic: PC not in foreground after gallery launch; hiding overlay immediately")
            hideOverlayAndCleanup()
        } else {
            DebugLog.log("Logic: PC still in foreground after gallery launch; scheduling re-check in ${debounceMs}ms")
            scheduleGalleryLaunchRecheck()
        }
    }

    private var galleryLaunchRecheckRunnable: Runnable? = null

    private fun scheduleGalleryLaunchRecheck() {
        galleryLaunchRecheckRunnable?.let { handler.removeCallbacks(it) }
        val runnable =
            Runnable {
                galleryLaunchRecheckRunnable = null
                if (!isOverlayActive) return@Runnable
                val pkg = foregroundDetector.getForegroundPackage()
                DebugLog.log("Logic: gallery-launch re-check fired; foreground=$pkg, overlayActive=$isOverlayActive")
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
     *
     * Issue #92: Must cancel any pending camera-available deactivation runnable before hiding,
     * otherwise the runnable fires after focus-loss hides the overlay and calls
     * hideOverlayAndCleanup() on an already-hidden overlay (double-firing onOverlayStateChanged
     * and incorrectly unregistering the thumbnail observer a second time.
     * Uses hideOverlayAndCleanup() (not a bare hide()) so the thumbnail observer and any active
     * secure session are torn down correctly on focus loss.
     */
    fun onOverlayFocusLost() {
        DebugLog.log("Logic: overlay focus lost; overlayActive=$isOverlayActive")
        if (!isOverlayActive) return
        cancelPendingDeactivation()
        hideOverlayAndCleanup()
    }

    /**
     * Called by [com.gb4pc.overlay.OverlayManager] when the overlay window regains focus.
     * Indicates the camera app is back in the foreground. Re-shows the overlay.
     * Only fired when the experimental focusable-overlay preference is enabled.
     *
     * Issue #92: Must re-register the thumbnail observer (via onRegisterThumbnailObserver) so
     * the overlay button thumbnail works correctly after focus is regained, matching the
     * behaviour of showOverlay().
     *
     * Issue #92 (lock-screen): mirrors [showOverlay]'s lock-screen path; if the device is
     * locked when focus is regained, re-start the secure session and re-register the media
     * observer so newly captured photos update the thumbnail button.
     */
    fun onOverlayFocusGained() {
        DebugLog.log("Logic: overlay focus gained; overlayActive=$isOverlayActive")
        if (isOverlayActive) return
        if (!hasOverlayPermission()) {
            DebugLog.log("Logic: overlay focus gained but overlay permission missing")
            onOverlayPermissionLost()
            return
        }
        logCameraForegroundRaceResolved("overlay focus regained, Issue #92")
        overlayManager.show()
        isOverlayActive = true
        onOverlayStateChanged(true)
        onRegisterThumbnailObserver()
        if (isKeyguardLocked()) {
            DebugLog.log("Logic: overlay focus gained on locked device; starting secure session")
            sessionTracker.startSession()
            onRegisterMediaObserver()
        }
    }

    companion object {
        /**
         * Marker word in the Issue #907 diagnostic log line, chosen to be greppable on its own.
         *
         * Device logcat carries it under [com.gb4pc.util.DebugLog.LOGCAT_TAG], as does the in-app
         * debug log. The E2E logcat CI artifact carries it only because this exact string is
         * whitelisted in `scripts/ci/test-support/filter_logcat.sh`: that filter keeps a short
         * list of tags, `GB4PC` is not among them, so without the entry every line below would be
         * dropped before the artifact was written. Changing this constant means changing the
         * filter with it, and `test_filter_logcat.sh` fails if the two drift apart.
         */
        const val CAMERA_FOREGROUND_RACE = "CAMERA_FOREGROUND_RACE"
    }
}
