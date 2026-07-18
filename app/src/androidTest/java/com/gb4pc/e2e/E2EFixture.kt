package com.gb4pc.e2e

import android.app.KeyguardManager
import android.app.UiAutomation
import android.content.BroadcastReceiver
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Color
import android.media.MediaScannerConnection
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.os.ParcelFileDescriptor
import android.provider.MediaStore
import android.util.Log
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.UiDevice
import com.gb4pc.Constants
import com.gb4pc.data.AspectRatioUtil
import com.gb4pc.data.PrefsManager
import com.gb4pc.e2e.visual.ColorMatch
import com.gb4pc.e2e.visual.Rgb
import com.gb4pc.e2e.visual.Screenshot
import com.gb4pc.service.OverlayService
import com.gb4pc.viewer.SessionTracker
import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Shared setup, teardown, and Pixel-Camera shell-command helpers for E2E tests.
 *
 * E2E tests run against a real OverlayService and the mock-camera APK installed under the
 * Pixel Camera package name (see [Constants.PIXEL_CAMERA_PACKAGE]). Every E2E test needs
 * the same boilerplate:
 *
 *   1. Verify the mock-camera APK is installed; fail loudly otherwise. (Silent skip would
 *      let an unconfigured emulator hide real regressions.)
 *   2. Mark setup as completed and enable the service in PrefsManager.
 *   3. Start OverlayService and wait for camera-availability callbacks to register.
 *   4. Force-stop Pixel Camera so each test starts from a known state.
 *
 * This fixture centralises that boilerplate so adding a new E2E test class is roughly
 *   `private val fixture = E2EFixture(...)`
 *   `@Before fun setUp() = fixture.setUp()`
 *
 * The shell-command helpers (`launchPixelCamera`, `goHome`, `stopPixelCamera`) and the
 * polling helper (`waitForCondition`) live here too; they are used by every E2E test
 * and have no other natural home.
 */
class E2EFixture(
    private val context: Context,
    private val uiAutomation: UiAutomation,
) {
    private val pcPackage = Constants.PIXEL_CAMERA_PACKAGE

    companion object {
        // Tag for the diagnostic logcat lines emitted by launchPixelCamera() (issue #233). These
        // make the bounded-relaunch path observable in a CI run's logcat: when the first-launch
        // teardown race occurs, the log shows which attempt re-issued `am start` and which one the
        // overlay finally activated on, instead of leaving the retry/recovery to be inferred.
        private const val TAG = "GB4PC_E2E"

        /** Package name of the mock gallery APK (see `:e2e-mock-gallery` module). */
        private const val MOCK_GALLERY_PACKAGE = "com.gb4pc.mockgallery"

        // Issue #233: bounded relaunch for the first-launch teardown race in launchPixelCamera().
        //
        // The per-attempt verify windows must not false-positive a *healthy* launch as a failed
        // one. waitForOverlayActive() documents that on the CI emulator a healthy activation can
        // take ~9 s (camera open) + ~1 s (UsageStats) and budgets 20 s for it. So the first
        // attempt is given a window in that range (LAUNCH_FIRST_VERIFY_MS): a slow-but-healthy
        // first launch activates the overlay inside that window and returns without the loop ever
        // re-issuing `am start`. Re-issuing on a healthy, mid-resume activity would itself race the
        // OPEN transition (a second `input swipe` + `am start` over the resuming activity), i.e.
        // re-create the very failure this fix targets, so the first window deliberately errs long.
        //
        // Only the *teardown* failure mode (activity force-removed ~3 ms after START, before
        // onResume(), so the camera never opens and the overlay can never activate) survives that
        // first window. Recovery from it is fast: every observed non-first launch in CI opens the
        // camera within ~30 ms, so the follow-up attempts use the shorter LAUNCH_RETRY_VERIFY_MS.
        //
        // Worst-case budget for a genuinely broken launch:
        //   baseline inactive wait (LAUNCH_BASELINE_MS, only consumed if a prior overlay is still
        //   active going in--itself a separate failure, not the healthy path)
        //   + LAUNCH_FIRST_VERIFY_MS + (LAUNCH_ATTEMPTS - 1) x LAUNCH_RETRY_VERIFY_MS
        //   = 3 + 12 + 2 x 6 = 27 s, under the 30 s overlayAppearsWhenViewfinderOpens assertion,
        // so launchPixelCamera() returns and lets the caller's own assertion fail rather than hang.
        private const val LAUNCH_ATTEMPTS = 3
        private const val LAUNCH_FIRST_VERIFY_MS = 12_000L
        private const val LAUNCH_RETRY_VERIFY_MS = 6_000L
        private const val LAUNCH_BASELINE_MS = 3_000L

        /** Overall budget for [dismissSecureKeyguard]'s verify-and-retry loop. */
        private const val DISMISS_KEYGUARD_TIMEOUT_MS = 10_000L
    }

    /**
     * Run from `@Before`. Performs all preconditions; tests that pass `setUp()` are
     * guaranteed: Pixel Camera installed, OverlayService running and registered, PC
     * not currently running, display is on, and overlay is inactive.
     */
    fun setUp() {
        ensurePixelCameraInstalled()
        // Wake the display and dismiss the swipe keyguard so screenshots during tests
        // capture actual UI, not a black screen or the gray (#E9E8EF) lock screen.
        wakeAndDismissKeyguard()
        startServiceWithSetupCompleted()
        // Allow the service time to register camera callbacks.
        Thread.sleep(1000)
        // Ensure PC is not running at test start.
        stopPixelCamera()
        // Ensure the mock gallery is not left foregrounded from a previous test (Issue #230 /
        // #397). Once test2a's tap opens the gallery (com.gb4pc.mockgallery), it can stay in
        // front into the next test; setUp() only stopped Pixel Camera, so a later test that
        // expects the green camera feed (e.g. test1a's BLUE-centroid check) could screenshot the
        // stale gallery instead. Force-stopping it here, mirroring stopPixelCamera(), gives each
        // test a clean foreground baseline.
        stopMockGallery()
        // Wait for the overlay to deactivate so each test starts from a known inactive state.
        // This prevents stale isOverlayActive=true from a previous test from causing
        // waitForOverlayActive() to return immediately with a stale flag.
        waitForOverlayInactive()
    }

    fun launchPixelCamera() {
        // Wake and dismiss the swipe keyguard first. STILL_IMAGE_CAMERA is a non-secure
        // launch, so if the keyguard is up the activity starts behind it and screenshots
        // capture the gray (#E9E8EF) lock screen instead of the green camera View--the
        // root cause of the test0 smoke flake (issue #235). The screen can re-sleep or
        // re-lock between setUp() and a later test's launch (and between CI steps), so the
        // dismissal must run on every launch, not only once in setUp().
        wakeAndDismissKeyguard()

        // Issue #233: the first STILL_IMAGE_CAMERA launch of a suite is frequently torn down before
        // the activity reaches onResume(), so the overlay never activates. launchAndAwaitOverlay
        // retries the launch (bounded) until the overlay activates; see its kdoc for the rationale.
        launchAndAwaitOverlay(
            label = "launchPixelCamera",
            amCommand = "am start -a android.media.action.STILL_IMAGE_CAMERA -p $pcPackage",
            // The screen can re-sleep between attempts, so re-dismiss the swipe keyguard before
            // re-issuing `am start` on this non-secure launch.
            onBeforeRetry = { wakeAndDismissKeyguard() },
        )
    }

    /**
     * Issues [amCommand] up to [LAUNCH_ATTEMPTS] times, returning as soon as
     * [OverlayService.isOverlayActive] becomes true (the observable proof that the mock camera
     * reached onResume() and opened the camera, firing onCameraUnavailable).
     *
     * Issue #233: On the API-35 CI emulator the *first* camera launch of a suite is frequently
     * torn down inside its own OPEN window-transition before the activity reaches onResume().
     * WindowManager force-removes the just-created ActivityRecord ("Force removing
     * ActivityRecord{... MockCameraActivity}"), so MockCameraActivity never calls openCamera(),
     * CameraManager.AvailabilityCallback.onCameraUnavailable never fires, and the overlay never
     * activates. A re-issued `am start` after the transition has settled reliably brings the
     * activity up (every non-first launch in CI opens the camera within ~30 ms), so this retries
     * until the overlay activates. The retry is bounded so a genuinely broken launch still fails
     * the caller's own activation assertion rather than hanging.
     *
     * The activation check treats isOverlayActive becoming true as proof that *this* launch
     * reached onResume(). That proof is only sound from a known-inactive baseline: if a previous
     * test's overlay is still active when this is called, waitForCondition would read the stale
     * true on its first poll and return before this launch's `am start` had any effect. So a
     * clean inactive baseline is awaited first (bounded, so it cannot hang).
     *
     * @param label        Prefix for the diagnostic logcat lines (the calling helper's name).
     * @param amCommand    The full `am start ...` shell command to (re-)issue each attempt.
     * @param onBeforeRetry Run before each *re-issued* attempt (not before the first), e.g. to
     *                      re-dismiss a swipe keyguard. The secure-camera path passes a no-op so
     *                      the keyguard it relies on is left in place.
     */
    private fun launchAndAwaitOverlay(
        label: String,
        amCommand: String,
        onBeforeRetry: () -> Unit,
    ) {
        waitForCondition(LAUNCH_BASELINE_MS) { !OverlayService.isOverlayActive }
        repeat(LAUNCH_ATTEMPTS) { attempt ->
            Log.i(TAG, "$label: am start attempt ${attempt + 1}/$LAUNCH_ATTEMPTS")
            uiAutomation.executeShellCommand(amCommand).close()
            // The first attempt gets the full healthy-activation window so a slow-but-healthy
            // launch is never mistaken for a failed one and re-issued (which would itself race the
            // OPEN transition). Only the teardown failure mode survives that window, and recovery
            // from it is fast, so later attempts use the shorter retry window. See the
            // companion-object note for the budget rationale.
            val verifyMs = if (attempt == 0) LAUNCH_FIRST_VERIFY_MS else LAUNCH_RETRY_VERIFY_MS
            if (waitForCondition(verifyMs) { OverlayService.isOverlayActive }) {
                Log.i(TAG, "$label: overlay active on attempt ${attempt + 1}")
                return
            }
            if (attempt < LAUNCH_ATTEMPTS - 1) {
                // The previous launch was torn down before the camera opened. Try again.
                Log.w(
                    TAG,
                    "$label: overlay still inactive after $verifyMs ms on attempt " +
                        "${attempt + 1} (first-launch teardown race); re-issuing am start",
                )
                onBeforeRetry()
            } else {
                Log.w(
                    TAG,
                    "$label: overlay still inactive after $LAUNCH_ATTEMPTS attempts; " +
                        "letting the caller's own assertion fail",
                )
            }
        }
    }

    /**
     * Wakes the display and dismisses the emulator's swipe-style lock screen.
     *
     * Sends KEYCODE_WAKEUP (224) to turn the screen on regardless of API level, then performs
     * an upward swipe to dismiss the swipe keyguard the CI emulator shows after boot or after
     * the display sleeps. This mirrors the CI pre-flight smoke check (`build.yml`), which uses
     * the same wake + swipe sequence; `wm dismiss-keyguard` did not reliably dismiss the
     * swipe-type lock screen on the API-35 CI emulator. When the screen is already unlocked the
     * swipe is a harmless gesture over the foreground activity.
     *
     * An upward swipe is preferred over KEYCODE_MENU (82): KEYCODE_MENU can open a foreground
     * activity's options/overflow menu, obscuring the camera View.
     *
     * **Not sufficient against the secure keyguard `scripts/setup-e2e-emulator.sh` configures for
     * this whole E2E suite** (a PIN, so `wm dismiss-keyguard` alone leaves it engaged, per that
     * script's own step 9 comment). A swipe gesture does nothing against a PIN prompt. Most E2E
     * tests never need to care, because the CI workflow's `stay_on_while_plugged_in 7` keeps the
     * one-time dismissal from that setup script in effect for the rest of the job, unless
     * something re-engages the keyguard (explicitly via [lockScreen], or implicitly if enough
     * real time passes between CI steps for it to reassert itself, as happened with
     * `SetupActivityDeniedE2ETest`, issue #509 PR #564). If you actually need to guarantee the
     * secure keyguard is dismissed, use [dismissSecureKeyguard] instead.
     */
    fun wakeAndDismissKeyguard() {
        uiAutomation.executeShellCommand("input keyevent 224").close() // KEYCODE_WAKEUP
        Thread.sleep(300)
        uiAutomation.executeShellCommand("input swipe 300 1000 300 300").close()
        Thread.sleep(300)
    }

    /**
     * Wakes the display and dismisses the PIN-secured keyguard `scripts/setup-e2e-emulator.sh`
     * configures for this E2E suite (PIN `1234`), by replaying that script's own step-9 sequence:
     * wake, request dismissal, type the PIN, submit ENTER. [wakeAndDismissKeyguard]'s swipe
     * gesture does nothing against this secure keyguard; it only dismisses the emulator's
     * non-secure swipe-style lock screen (see that method's doc).
     *
     * The CI workflow's `stay_on_while_plugged_in 7` keeps the keyguard dismissed for most of a
     * job once the setup script's one-time dismissal runs, so most E2E tests never need this.
     * Reach for it when a test's own activity launch can race a keyguard re-engagement the
     * one-time setup dismissal no longer covers, e.g. because enough wall-clock time or enough
     * other E2E steps have passed since setup ran (see [SetupActivityDeniedE2ETest]'s class doc
     * for the incident that prompted this method, issue #509 PR #564).
     *
     * ### Verifies dismissal instead of trusting a fixed delay (issue #604)
     *
     * The sequence below is not instantaneous: each shell command is real IPC to the system UI,
     * and its total duration can stretch under extra CPU load on the emulator (for example, the
     * `screenrecord` process issue #604 added to suites that call this method). A single fire-
     * and-forget pass raced exactly that stretched duration in CI: `SetupActivityDeniedE2ETest`
     * and `SetupActivityPermissionDialogE2ETest` (both callers of this method) failed their very
     * first post-launch assertion with `SetupActivity` reaching `RESUMED` then `PAUSED` again
     * shortly after, because the compose rule's activity launch--which runs immediately after
     * this method returns--started before the keyguard had actually cleared. Polling
     * [KeyguardManager.isKeyguardLocked] and retrying the whole dismissal sequence until it
     * reports unlocked (or [DISMISS_KEYGUARD_TIMEOUT_MS] elapses) makes this method robust to that
     * variance instead of assuming a fixed wall-clock budget is always enough.
     *
     * ### Fails loudly on timeout instead of racing a downstream assertion (issue #642)
     *
     * If the keyguard is still locked when [DISMISS_KEYGUARD_TIMEOUT_MS] elapses, this method
     * fails immediately with [fail], the same way [lockScreen] fails loudly on its own timeout
     * path, instead of silently returning and letting the caller's next assertion fail with a
     * symptom (e.g. an activity bouncing `RESUMED` then `PAUSED`) that does not point back to the
     * keyguard as the actual cause. This exact silent-return path was implicated in a confusing
     * intermittent CI failure (PR #637) before the retry loop above was added; failing loudly here
     * makes any future recurrence of that race immediately diagnosable instead of requiring the
     * same investigation to be repeated.
     *
     * @throws AssertionError if the keyguard is still locked after [DISMISS_KEYGUARD_TIMEOUT_MS].
     */
    fun dismissSecureKeyguard() {
        val keyguardManager = context.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
        val deadline = System.currentTimeMillis() + DISMISS_KEYGUARD_TIMEOUT_MS
        do {
            uiAutomation.executeShellCommand("input keyevent 224").close() // KEYCODE_WAKEUP
            Thread.sleep(300)
            uiAutomation.executeShellCommand("wm dismiss-keyguard").close()
            Thread.sleep(300)
            uiAutomation.executeShellCommand("input text 1234").close()
            Thread.sleep(300)
            uiAutomation.executeShellCommand("input keyevent 66").close() // KEYCODE_ENTER
            Thread.sleep(300)
        } while (keyguardManager.isKeyguardLocked && System.currentTimeMillis() < deadline)
        if (keyguardManager.isKeyguardLocked) {
            fail(
                "dismissSecureKeyguard(): KeyguardManager.isKeyguardLocked still true after " +
                    "$DISMISS_KEYGUARD_TIMEOUT_MS ms; the dismissal sequence did not clear the " +
                    "secure keyguard within its retry budget.",
            )
        }
    }

    fun goHome() {
        uiAutomation
            .executeShellCommand(
                "am start -a android.intent.action.MAIN -c android.intent.category.HOME",
            ).close()
    }

    fun stopPixelCamera() {
        uiAutomation.executeShellCommand("am force-stop $pcPackage").close()
    }

    /**
     * Force-stops the mock gallery app so it is not left foregrounded between tests (Issue #230 /
     * #397). Mirrors [stopPixelCamera]; called from [setUp] to give each test a clean foreground.
     */
    fun stopMockGallery() {
        uiAutomation.executeShellCommand("am force-stop $MOCK_GALLERY_PACKAGE").close()
    }

    /**
     * Polls [condition] every 100 ms until it returns true or [timeoutMs] elapses. Returns
     * the final value of [condition].
     */
    fun waitForCondition(
        timeoutMs: Long,
        condition: () -> Boolean,
    ): Boolean {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            if (condition()) return true
            Thread.sleep(100)
        }
        return condition()
    }

    // ── Phase 4 extensions ────────────────────────────────────────────────────

    /**
     * Seeds PrefsManager with a gallery package selection and marks setup complete.
     *
     * Sets [PrefsManager.galleryPackage] = [pkg], [PrefsManager.isSetupCompleted] = true,
     * and [PrefsManager.isServiceEnabled] = true. All other prefs are left untouched.
     */
    fun seedGalleryPrefs(pkg: String) {
        val prefs = PrefsManager(context)
        prefs.galleryPackage = pkg
        prefs.isSetupCompleted = true
        prefs.isServiceEnabled = true
    }

    /**
     * Empties the external-images MediaStore of every row, regardless of which package owns
     * it, and asserts none remain.
     *
     * Two deletion passes run, because no single API caller can remove every row:
     *
     *  1. [ContentResolver.delete] from this process (`com.gb4pc`). Under scoped storage
     *     (API 29+) this only removes rows `com.gb4pc` itself owns; rows inserted by other
     *     packages (e.g. the mock camera's captured photos, owned by
     *     `com.google.android.GoogleCamera`) are silently skipped, because an app cannot
     *     delete another app's MediaStore rows without a user-confirmed
     *     `createDeleteRequest` consent dialog, which is unavailable in an unattended test.
     *
     *  2. A `content delete` shell command via [runShellCommand]. `uiAutomation`-issued shell
     *     commands run under the `shell` UID, which holds broad MediaStore access and deletes
     *     rows owned by *any* package without a consent dialog. This is the cross-package
     *     cleanup tracked by issue #406: it removes the mock camera's leftover GREEN capture
     *     (owned by `com.google.android.GoogleCamera`) that pass 1 cannot touch, so a later
     *     test (e.g. `test4a`) genuinely starts from an empty roll rather than inheriting
     *     `test3a`'s photo.
     *
     * Pass 1 is kept rather than relying solely on pass 2 so the common case (clearing
     * `com.gb4pc`'s own rows) still works directly through the ContentResolver, and the shell
     * pass is purely additive for the cross-package rows.
     *
     * The post-condition is now an *unfiltered* count: after both passes the entire external
     * camera roll must be empty. Asserting the unfiltered count (rather than only the rows
     * `com.gb4pc` owns) is what makes the "empty gallery" assumption in `test2a`/`test4a`
     * actually true.
     */
    fun clearCameraRoll() {
        // Pass 1: delete this package's own rows via the ContentResolver.
        context.contentResolver.delete(
            MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
            null,
            null,
        )

        // Pass 2: delete cross-package rows via the shell UID, which is not bound by the
        // scoped-storage ownership restriction that limits pass 1 (issue #406).
        val deleteOutput =
            runShellCommand(
                "content delete --uri ${MediaStore.Images.Media.EXTERNAL_CONTENT_URI}",
            )
        Log.i(TAG, "clearCameraRoll: shell content delete output: $deleteOutput")

        // Post-condition: the entire external camera roll must now be empty, across all owners.
        val count = countMediaStoreImages()
        assertEquals(
            "MediaStore external images should be empty after clearCameraRoll() " +
                "(both the com.gb4pc ContentResolver pass and the cross-package shell pass)",
            0,
            count,
        )
    }

    /**
     * Inserts one small image into the shared-images MediaStore and returns its content [Uri], so
     * a caller that then opens the system photo picker is guaranteed at least one selectable item.
     *
     * [PartialAccessPhotoPickerE2ETest] needs the picker grid to be non-empty for two reasons: so a
     * thumbnail exists to tap, and so the resulting grant is a *genuine* partial grant
     * (`READ_MEDIA_VISUAL_USER_SELECTED` over a real selected item), which is exactly what issue
     * #568's H2 case must exercise -- confirming an empty selection would not produce that grant.
     * The emulator's photo library is otherwise non-deterministic here (earlier E2E suites both
     * capture into DCIM and call [clearCameraRoll]), so this seeds a known row rather than relying
     * on residual state.
     *
     * Writing the app's *own* media to MediaStore needs no runtime permission on API 29+ (scoped
     * storage), so this works even with `READ_MEDIA_IMAGES` revoked (`-PmediaPermissionGranted=false`).
     */
    fun seedOnePhoto(): Uri {
        val resolver = context.contentResolver
        val values =
            ContentValues().apply {
                put(MediaStore.Images.Media.DISPLAY_NAME, "gb4pc-e2e-seed-${System.currentTimeMillis()}.jpg")
                put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg")
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    put(MediaStore.Images.Media.RELATIVE_PATH, "${Environment.DIRECTORY_DCIM}/gb4pc-e2e")
                    // IS_PENDING hides the half-written row from other apps (incl. the picker)
                    // until the JPEG bytes are flushed and it is cleared below.
                    put(MediaStore.Images.Media.IS_PENDING, 1)
                }
            }
        val uri =
            requireNotNull(
                resolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values),
            ) { "seedOnePhoto(): MediaStore.insert returned null; could not seed a picker item" }

        val bitmap = Bitmap.createBitmap(16, 16, Bitmap.Config.ARGB_8888)
        bitmap.eraseColor(Color.rgb(0, 200, 83))
        requireNotNull(resolver.openOutputStream(uri)) {
            "seedOnePhoto(): openOutputStream returned null for $uri"
        }.use { out -> bitmap.compress(Bitmap.CompressFormat.JPEG, 90, out) }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            resolver.update(
                uri,
                ContentValues().apply { put(MediaStore.Images.Media.IS_PENDING, 0) },
                null,
                null,
            )
        }

        // Confirm the row is now visible via an unfiltered query, so the caller does not open the
        // picker before the insert is observable.
        waitForCondition(5_000L) { countMediaStoreImages() > 0 }
        return uri
    }

    /**
     * Triggers the mock camera's shutter path and waits for the new image to appear in
     * MediaStore.
     *
     * Records the current row count, sends the `ACTION_SHUTTER` broadcast to the mock
     * camera process, and polls MediaStore until the count increments or 10 seconds elapse.
     * Falls back to [MediaScannerConnection.scanFile] if the row does not appear promptly
     * (e.g. if the MediaStore indexer has not picked up the new file from the filesystem).
     *
     * The `ACTION_SHUTTER` / `ACTION_SHUTTER_DONE` action strings are defined in
     * `MockCameraActivity` in the `:e2e-mock-camera` module. They are referenced here as
     * string literals because `:e2e-mock-camera` is a separate application module (not a
     * library) and its classes are not available at test compile time.
     *
     * @throws AssertionError if no new image row appears within 15 seconds.
     */
    fun captureOnePhoto() {
        val rowsBefore = countMediaStoreImages()

        // These constants must match MockCameraActivity.ACTION_SHUTTER / ACTION_SHUTTER_DONE.
        val actionShutter = "com.gb4pc.mockcamera.ACTION_SHUTTER"
        val actionShutterDone = "com.gb4pc.mockcamera.ACTION_SHUTTER_DONE"

        // Listen for ACTION_SHUTTER_DONE so we know the mock camera finished writing.
        val latch = CountDownLatch(1)
        val receiver =
            object : BroadcastReceiver() {
                override fun onReceive(
                    ctx: Context,
                    intent: Intent,
                ) {
                    if (intent.action == actionShutterDone) {
                        latch.countDown()
                    }
                }
            }
        val filter = IntentFilter(actionShutterDone)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            // RECEIVER_EXPORTED is required so MockCameraActivity (running under the
            // Pixel Camera UID, com.google.android.GoogleCamera) can deliver
            // ACTION_SHUTTER_DONE across UIDs back into this test process (com.gb4pc).
            // On API 33+ RECEIVER_NOT_EXPORTED silently drops cross-UID broadcasts,
            // which would cause the latch to time out and the test to fall back to
            // MediaStore/MediaScanner polling; defeating the intent of the
            // ACTION_SHUTTER_DONE handshake. The receiver is only registered inside
            // the instrumented E2E test process and ACTION_SHUTTER_DONE is a private
            // action string, so exporting the receiver carries no production risk.
            context.registerReceiver(receiver, filter, Context.RECEIVER_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            context.registerReceiver(receiver, filter)
        }

        try {
            // Send the shutter trigger to the mock camera.
            val shutterIntent = Intent(actionShutter).apply { setPackage(pcPackage) }
            context.sendBroadcast(shutterIntent)

            // Wait up to 10 s for the shutter-done broadcast.
            latch.await(10, TimeUnit.SECONDS)
        } finally {
            context.unregisterReceiver(receiver)
        }

        // Poll until the row count increases or 10 s pass.
        val appeared = waitForCondition(10_000L) { countMediaStoreImages() > rowsBefore }

        if (!appeared) {
            // Fall back: trigger a media scan in case the indexer hasn't picked up the file.
            val dcimPath =
                Environment
                    .getExternalStoragePublicDirectory(
                        Environment.DIRECTORY_DCIM,
                    ).absolutePath
            val scanLatch = CountDownLatch(1)
            MediaScannerConnection.scanFile(
                context,
                arrayOf(dcimPath),
                arrayOf("image/jpeg"),
            ) { _, _ -> scanLatch.countDown() }
            scanLatch.await(5, TimeUnit.SECONDS)

            val appearedAfterScan = waitForCondition(5_000L) { countMediaStoreImages() > rowsBefore }
            if (!appearedAfterScan) {
                fail(
                    "captureOnePhoto(): new image row did not appear in MediaStore within 15 s " +
                        "(even after MediaScannerConnection.scanFile fallback)",
                )
            }
        }
    }

    /**
     * Waits for the current secure session to contain at least one media item.
     *
     * The [OverlayService]'s MediaStore [android.database.ContentObserver] calls
     * [SessionTracker.addMedia] asynchronously after a photo is committed.
     * [captureOnePhoto] returns as soon as the MediaStore row exists, but the
     * ContentObserver's [android.database.ContentObserver.onChange] may not have
     * fired yet. Calling this helper after [captureOnePhoto] ensures the session is
     * populated before the caller acts on it (e.g. tapping the overlay to open
     * SecureViewer), avoiding a race that causes SecureViewer to open with an empty
     * session.
     *
     * @param timeoutMs Maximum time to wait. Default is 5 s, which is well above the
     *   service's 500 ms ContentObserver retry delay.
     * @throws AssertionError if the session remains empty after [timeoutMs].
     */
    fun waitForSessionMedia(timeoutMs: Long = 5_000L) {
        val appeared =
            waitForCondition(timeoutMs) {
                SessionTracker.instance.getSessionMedia().isNotEmpty()
            }
        if (!appeared) {
            fail(
                "waitForSessionMedia(): session media did not appear within ${timeoutMs}ms. " +
                    "The ContentObserver may not have fired, or the captured photo was not " +
                    "added to the active secure session.",
            )
        }
    }

    /**
     * Taps the overlay button at its configured screen position.
     *
     * Reads the active [com.gb4pc.data.OverlayPosition] from [PrefsManager], computes pixel
     * coordinates from [android.view.WindowMetrics] (API 30+) or [android.util.DisplayMetrics]
     * (API 26-29), then dispatches a tap via [UiDevice].
     *
     * If the overlay is not rendered (e.g. blocked in secure-camera mode) this call taps empty
     * screen space and has no visible effect; the plan requires this silent behaviour for
     * Tests 4a/5a's baseline-failure scenario.
     */
    fun tapOverlay() {
        val (displayWidth, displayHeight) = displaySize()

        val aspectRatio = AspectRatioUtil.quantize(displayWidth, displayHeight)
        val pos = PrefsManager(context).getOverlayPosition(aspectRatio)

        val x = (pos.xPercent / 100f * displayWidth).toInt()
        val y = (pos.yPercent / 100f * displayHeight).toInt()

        UiDevice.getInstance(InstrumentationRegistry.getInstrumentation()).click(x, y)
    }

    /**
     * Taps the overlay, then polls screenshots until [expected] accepts [stableSamples]
     * consecutive captures, returning the last capture (see [captureScreenUntil]).
     *
     * [tapOverlay] is silent when the overlay is not on screen, so every tap test must await
     * its expected post-tap screen; a fixed pause races the tapped activity's cold start
     * (issues #241, #705). On timeout the returned screenshot still shows the wrong screen,
     * and the caller's assertion fails on the frame that proves it.
     */
    fun tapOverlayAndAwait(
        timeoutMs: Long = 15_000L,
        stableSamples: Int = 1,
        expected: (Bitmap) -> Boolean,
    ): Bitmap {
        tapOverlay()
        return captureScreenUntil(timeoutMs = timeoutMs, stableSamples = stableSamples, predicate = expected)
    }

    /** Package name of the current foreground window, per UiAutomator; null if undetermined. */
    fun foregroundPackage(): String? = UiDevice.getInstance(InstrumentationRegistry.getInstrumentation()).currentPackageName

    /**
     * Locks the screen by sending KEYCODE_SLEEP (puts display to sleep unconditionally)
     * and then waits (up to 5 s) for [KeyguardManager.isKeyguardLocked] to return true.
     *
     * KEYCODE_SLEEP (223) is preferred over KEYCODE_POWER (26) because KEYCODE_POWER
     * toggles the display state: if the screen was already off it wakes the device
     * instead of locking it, causing the keyguard check to fail intermittently. Sending
     * KEYCODE_SLEEP first ensures the display goes off regardless of its current state.
     *
     * @throws AssertionError if the keyguard does not engage within 5 seconds.
     */
    fun lockScreen() {
        uiAutomation.executeShellCommand("input keyevent 223").close() // KEYCODE_SLEEP
        val locked =
            waitForCondition(5_000L) {
                (context.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager).isKeyguardLocked
            }
        if (!locked) {
            fail("lockScreen(): KeyguardManager.isKeyguardLocked did not become true within 5 s")
        }
    }

    /**
     * Launches the mock camera via the secure-camera action, waits for the overlay to activate
     * over the keyguard, and asserts the keyguard is still locked.
     *
     * Targets the mock-camera package explicitly with `-p $pcPackage`. Without `-p`, an
     * `am start -a android.media.action.STILL_IMAGE_CAMERA_SECURE` lands on the system intent
     * resolver / disambiguation chooser instead of launching [com.gb4pc.mockcamera.MockCameraActivity]
     * directly (the CI logcat shows a `ResolverListAdapter` entry and *no* `CameraDevice.onOpened`),
     * so the activity never reaches onResume(), the camera never opens,
     * `CameraManager.AvailabilityCallback.onCameraUnavailable` never fires, and the overlay never
     * activates; `waitForOverlayActive()` then times out. Pinning the package launches the mock
     * camera the same way [launchPixelCamera] does for the non-secure action.
     *
     * Uses the same bounded relaunch-until-overlay-active path as [launchPixelCamera] (issue #233):
     * the first secure launch can also be torn down before onResume(), and a re-issued `am start`
     * recovers it. The retry's `onBeforeRetry` is a no-op here so the keyguard this secure flow
     * relies on is left in place (unlike the non-secure launch, which re-dismisses the swipe
     * keyguard between attempts).
     *
     * After the launch, [KeyguardManager.isKeyguardLocked] must still be true: a silent keyguard
     * dismissal would make Tests 4a/5a pass for the wrong reason. Call [lockScreen] before this
     * method to guarantee the device is locked first.
     */
    fun launchSecureCamera() {
        launchAndAwaitOverlay(
            label = "launchSecureCamera",
            amCommand = "am start -a android.media.action.STILL_IMAGE_CAMERA_SECURE -p $pcPackage",
            // Do NOT dismiss the keyguard between attempts: this secure flow requires it locked.
            onBeforeRetry = {},
        )

        val keyguard = context.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
        if (!keyguard.isKeyguardLocked) {
            fail(
                "launchSecureCamera(): KeyguardManager.isKeyguardLocked == false after launching " +
                    "STILL_IMAGE_CAMERA_SECURE; the adb path dismissed the keyguard. " +
                    "Call lockScreen() before launchSecureCamera() and confirm the emulator " +
                    "honours the secure-camera launch path.",
            )
        }
    }

    /**
     * Pauses the current thread for [ms] milliseconds.
     *
     * Explicit helper for the spec's "pause N ms" steps; wraps [Thread.sleep] so test code
     * stays readable without raw sleep calls.
     */
    fun pause(ms: Long) {
        Thread.sleep(ms)
    }

    /**
     * Polls [OverlayService.isOverlayActive] until it returns false or [timeoutMs] elapses.
     *
     * Call this after [stopPixelCamera] (or in [setUp]) to guarantee the overlay is inactive
     * before launching the camera in each test. Without this guard, a stale
     * [OverlayService.isOverlayActive] == true from a previous test causes
     * [waitForOverlayActive] to return immediately, before the camera is actually running,
     * making subsequent screenshot assertions unreliable.
     *
     * Tolerates the case where the overlay was never active (returns immediately when already
     * false). Logs a warning but does not fail if the timeout expires, because some tests
     * (e.g. test0) intentionally run without activating the overlay.
     *
     * @param timeoutMs Maximum wait time; defaults to 5 s (enough for the 500 ms camera
     *                  debounce plus WM compositing overhead).
     */
    fun waitForOverlayInactive(timeoutMs: Long = 5_000L) {
        waitForCondition(timeoutMs) { !OverlayService.isOverlayActive }
        // No assertion: it is acceptable if the overlay never became active in the first place.
    }

    /**
     * Polls [OverlayService.isOverlayActive] until it returns true or [timeoutMs] elapses.
     *
     * Use this after [launchPixelCamera] to wait for the CameraManager → UsageStats →
     * overlay activation chain to complete before taking screenshots that depend on the
     * overlay being visible.
     *
     * The default timeout is 20 s to accommodate slow CI emulators where camera open
     * can take ~9 s and UsageStats detection adds up to 1 s more.
     *
     * @throws AssertionError if the overlay does not become active within [timeoutMs].
     */
    fun waitForOverlayActive(timeoutMs: Long = 20_000L) {
        val appeared = waitForCondition(timeoutMs) { OverlayService.isOverlayActive }
        if (!appeared) {
            fail(
                "waitForOverlayActive: OverlayService.isOverlayActive did not become true " +
                    "within $timeoutMs ms. Check that the service started, permissions are " +
                    "granted, and Pixel Camera is in the foreground.",
            )
        }
    }

    /**
     * Polls screenshots until the central 60% of the screen has at least [minCoverage] GREEN
     * (#00C853) pixels, or [timeoutMs] elapses.
     *
     * Mirrors the CI pre-flight retry loop so a slow-starting mock camera does not flake
     * the smoke test. Returns the final measured coverage regardless of whether the threshold
     * was reached.
     *
     * @param minCoverage  Fraction of the central region that must be GREEN before stopping.
     * @param timeoutMs    Maximum wait time in milliseconds.
     * @param intervalMs   Sleep between successive capture attempts.
     */
    fun waitForGreenCoverage(
        minCoverage: Float = 0.70f,
        timeoutMs: Long = 15_000L,
        intervalMs: Long = 500L,
    ): Float {
        var lastCoverage = 0f
        captureScreenUntil(timeoutMs, intervalMs) { screen ->
            val margin = (1f - 0.60f) / 2f
            val region =
                android.graphics.Rect(
                    (screen.width * margin).toInt(),
                    (screen.height * margin).toInt(),
                    (screen.width * (1f - margin)).toInt(),
                    (screen.height * (1f - margin)).toInt(),
                )
            lastCoverage = ColorMatch.coverageFraction(ColorMatch.mask(screen, Rgb.GREEN), region)
            lastCoverage >= minCoverage
        }
        return lastCoverage
    }

    /**
     * Polls screenshots until [predicate] accepts [stableSamples] consecutive captures, or
     * [timeoutMs] elapses, and returns the last captured screenshot either way, so the caller
     * asserts against the exact frame the poll measured rather than a separately-captured one.
     *
     * [stableSamples] > 1 hardens absence-style predicates (e.g. "no green on screen"): a
     * single transient frame, such as an app-transition starting window, cannot end the wait.
     *
     * @param timeoutMs     Maximum wait time in milliseconds.
     * @param intervalMs    Sleep between successive capture attempts.
     * @param stableSamples Consecutive predicate-satisfying captures required to stop early.
     * @param predicate     Decides whether a captured screenshot is the awaited screen state.
     */
    fun captureScreenUntil(
        timeoutMs: Long = 15_000L,
        intervalMs: Long = 500L,
        stableSamples: Int = 1,
        predicate: (Bitmap) -> Boolean,
    ): Bitmap {
        require(stableSamples >= 1) { "stableSamples must be >= 1" }
        val deadline = System.currentTimeMillis() + timeoutMs
        var streak = 0
        while (true) {
            val screen = Screenshot.captureScreen()
            streak = if (predicate(screen)) streak + 1 else 0
            if (streak >= stableSamples || System.currentTimeMillis() >= deadline) return screen
            Thread.sleep(intervalMs)
        }
    }

    /**
     * Polls screenshots until at least one pixel matches [color], or [timeoutMs] elapses, and
     * returns the screenshot it last captured -- so the caller asserts against the exact same
     * image that proved (or failed to prove) the color's presence, not a separately-captured one.
     *
     * Issue #556: [waitForOverlayActive] only observes [OverlayService.isOverlayActive], an
     * internal UsageStats-based flag that can flip true before the corresponding frame (camera
     * feed plus overlay) is actually composited by the WindowManager -- especially on a
     * cold-started activity, which briefly shows Android's mandatory splash-screen frame first.
     * A fixed post-activation pause is a guess at how long compositing takes and silently stops
     * being enough as CI load changes (e.g. issue #520's concurrent `screenrecord`). Polling for
     * the actual pixel evidence removes that guess: `ScreenshotTestRule.failed()` proved the
     * content was correct just moments after a fixed-pause capture went stale (it takes its own
     * screenshot after the assertion throws and consistently shows the overlay rendered exactly
     * at the configured position), so the content was never wrong -- only the single early
     * capture was.
     *
     * @param color      The [Rgb] color to poll for.
     * @param timeoutMs  Maximum wait time in milliseconds.
     * @param intervalMs Sleep between successive capture attempts.
     */
    fun captureScreenUntilColorVisible(
        color: Rgb,
        timeoutMs: Long = 5_000L,
        intervalMs: Long = 200L,
    ): Bitmap = captureScreenUntil(timeoutMs, intervalMs) { ColorMatch.mask(it, color).pixelCount > 0 }

    // ── Private helpers ───────────────────────────────────────────────────────

    /**
     * Runs [command] via [UiAutomation.executeShellCommand] under the `shell` UID and returns
     * its combined stdout/stderr as a trimmed string.
     *
     * Unlike the fire-and-forget `executeShellCommand(...).close()` pattern used elsewhere in
     * this fixture, this helper drains the command's output so callers can log or inspect it.
     * The `shell` UID has broader MediaStore privileges than this test's own UID (`com.gb4pc`),
     * which is why it is used for the cross-package `content delete` in [clearCameraRoll]
     * (issue #406).
     */
    private fun runShellCommand(command: String): String =
        ParcelFileDescriptor.AutoCloseInputStream(uiAutomation.executeShellCommand(command)).use {
            it.readBytes().toString(Charsets.UTF_8).trim()
        }

    private fun countMediaStoreImages(): Int {
        val cursor =
            context.contentResolver.query(
                MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                arrayOf(MediaStore.Images.Media._ID),
                null,
                null,
                null,
            )
        val count = cursor?.count ?: 0
        cursor?.close()
        return count
    }

    internal fun displaySize(): Pair<Int, Int> =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val wm = context.getSystemService(Context.WINDOW_SERVICE) as android.view.WindowManager
            val bounds = wm.currentWindowMetrics.bounds
            bounds.width() to bounds.height()
        } else {
            val wm = context.getSystemService(Context.WINDOW_SERVICE) as android.view.WindowManager
            val dm = android.util.DisplayMetrics()
            @Suppress("DEPRECATION")
            wm.defaultDisplay.getMetrics(dm)
            dm.widthPixels to dm.heightPixels
        }

    private fun ensurePixelCameraInstalled() {
        try {
            context.packageManager.getPackageInfo(pcPackage, 0)
        } catch (e: PackageManager.NameNotFoundException) {
            fail(
                "Pixel Camera ($pcPackage) is not installed. " +
                    "Run 'scripts/setup-e2e-emulator.sh' (or 'adb install e2e/pixel-camera.apk') " +
                    "before executing the E2E suite.",
            )
        }
    }

    private fun startServiceWithSetupCompleted() {
        val prefs = PrefsManager(context)
        prefs.isSetupCompleted = true
        prefs.isServiceEnabled = true
        OverlayService.start(context)
    }
}
