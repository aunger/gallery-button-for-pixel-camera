package com.gb4pc.overlay

import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.PixelFormat
import android.graphics.drawable.AdaptiveIconDrawable
import android.graphics.drawable.BitmapDrawable
import android.graphics.drawable.Drawable
import android.graphics.drawable.LayerDrawable
import android.os.Build
import android.view.Gravity
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.WindowManager
import android.widget.ImageView
import android.widget.Toast
import androidx.core.content.ContextCompat
import com.gb4pc.R
import com.gb4pc.data.AspectRatioUtil
import com.gb4pc.data.PrefsManager
import com.gb4pc.ui.picker.PickerActivity
import com.gb4pc.util.DebugLog
import com.gb4pc.util.PermissionHelper
import com.gb4pc.viewer.SecureViewerActivity
import kotlin.math.min
import kotlin.math.roundToInt

// ── Overlay-position pixel conversions (PS-01) ──────────────────────────────
// Pure functions: testable in plain JVM unit tests (see OverlayManagerTest).

/** Overlay edge length in pixels: [sizePercent]% of `min(displayWidth, displayHeight)`. */
internal fun calculateOverlaySizePx(sizePercent: Float, displayWidth: Int, displayHeight: Int): Int {
    val minDimension = min(displayWidth, displayHeight)
    return (minDimension * sizePercent / 100f).roundToInt()
}

/** Left edge X of an overlay whose centre lies at [xPercent]% of [displayWidth]. */
internal fun calculateOverlayXPx(xPercent: Float, displayWidth: Int, overlaySize: Int): Int {
    val centerX = (displayWidth * xPercent / 100f).roundToInt()
    return centerX - overlaySize / 2
}

/** Top edge Y of an overlay whose centre lies at [yPercent]% of [displayHeight]. */
internal fun calculateOverlayYPx(yPercent: Float, displayHeight: Int, overlaySize: Int): Int {
    val centerY = (displayHeight * yPercent / 100f).roundToInt()
    return centerY - overlaySize / 2
}

/**
 * Manages the overlay window that covers Pixel Camera's gallery button (§4).
 */
class OverlayManager(
    private val context: Context,
    private val prefsManager: PrefsManager,
    /**
     * Called when the overlay window loses focus (hasFocus == false), which signals that a
     * system surface (task-switcher, notification shade, etc.) has covered the camera app.
     * Only fired when [PrefsManager.focusableOverlay] is true.
     */
    private val onFocusLost: () -> Unit = {},
    /**
     * Called when the overlay window regains focus (hasFocus == true), signalling that the
     * camera app is back in front.
     * Only fired when [PrefsManager.focusableOverlay] is true.
     */
    private val onFocusGained: () -> Unit = {},
    /**
     * Called immediately after the gallery app is launched via the overlay button (Issue #91).
     * Allows the service to hide the overlay early when Pixel Camera is no longer in the
     * foreground, without waiting for the camera-available event.
     */
    private val onGalleryLaunched: () -> Unit = {},
) {
    private val windowManager = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager
    private val keyguardManager = context.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
    private var overlayView: ImageView? = null
    private var isShowing = false

    fun show() {
        if (isShowing) {
            return
        }

        val view = createOverlayView()
        val params = createLayoutParams()

        try {
            windowManager.addView(view, params)
            overlayView = view
            isShowing = true
            DebugLog.log("Overlay shown")
        } catch (e: Exception) {
            DebugLog.log("Failed to show overlay: ${e.message}")
        }
    }

    fun hide() {
        overlayView?.let { view ->
            try {
                windowManager.removeView(view)
            } catch (_: Exception) {
                // View may already be removed
            }
        }
        overlayView = null
        isShowing = false
        DebugLog.log("Overlay hidden")
    }

    fun updatePosition() {
        if (!isShowing || overlayView == null) return
        val params = createLayoutParams()
        try {
            windowManager.updateViewLayout(overlayView, params)
        } catch (_: Exception) {}
    }

    /**
     * Re-apply window flags by hiding and re-showing the overlay.
     * Call this after [PrefsManager.focusableOverlay] changes while the overlay is visible,
     * so the new FLAG_NOT_FOCUSABLE / FLAG_NOT_TOUCH_MODAL setting takes effect.
     */
    fun reshow() {
        if (!isShowing) return
        hide()
        show()
    }

    fun showLatestPhotoThumbnail(photoUri: String) {
        val targetView = overlayView ?: return
        val uri = android.net.Uri.parse(photoUri)
        Thread {
            val bitmap = loadThumbnailBitmap(uri)
            bitmap?.let { bmp ->
                android.os.Handler(android.os.Looper.getMainLooper()).post {
                    // Issue #188: wrap the bitmap in SquircleDrawable so the thumbnail is
                    // clipped to the superellipse squircle shape, just like the gallery icon.
                    targetView.setImageDrawable(
                        SquircleDrawable(BitmapDrawable(targetView.resources, bmp))
                    )
                }
            }
        }.start()
    }

    /**
     * Loads a thumbnail-sized bitmap for [uri].
     *
     * On API 29+, tries [ContentResolver.loadThumbnail] first. This can fail on a locked
     * device if the thumbnail cache lives in credential-encrypted storage (the photos
     * themselves on external storage remain accessible). In that case, falls back to
     * decoding a downsampled copy of the original file via [ContentResolver.openInputStream].
     *
     * On pre-API 29, always uses the stream-based path.
     *
     * Returns null (and logs) if all attempts fail.
     */
    internal fun loadThumbnailBitmap(uri: android.net.Uri): android.graphics.Bitmap? {
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.Q) {
            try {
                return context.contentResolver.loadThumbnail(uri, android.util.Size(200, 200), null)
            } catch (e: Exception) {
                DebugLog.log("loadThumbnail failed (locked device?), falling back to stream decode: ${e.message}")
            }
        }
        // Fallback: decode a downsampled version of the original file directly.
        return try {
            context.contentResolver.openInputStream(uri)?.use { stream ->
                val opts = android.graphics.BitmapFactory.Options().apply { inSampleSize = 4 }
                android.graphics.BitmapFactory.decodeStream(stream, null, opts)
            }
        } catch (e: Exception) {
            DebugLog.log("Failed to load thumbnail via stream: ${e.message}")
            null
        }
    }

    private fun createOverlayView(): ImageView {
        // When focusable overlay is enabled we need a custom subclass to handle key and focus
        // events on the root view.
        val imageView = object : ImageView(context) {
            /**
             * Do not consume key events — pass them through to the camera app.
             *
             * NOTE (Issue #55 open question): Even with dispatchKeyEvent returning false, a
             * focusable TYPE_APPLICATION_OVERLAY window may steal input focus from the camera
             * app when first shown. If that happens, volume-as-shutter and zoom keys will be
             * broken regardless of this override. This must be verified on a real device.
             */
            override fun dispatchKeyEvent(event: KeyEvent): Boolean = false

            /**
             * Touch-routing diagnostic for Issue #230 / #397. Every prior "the tap misses"
             * conclusion was inferred from screenshots and green-coverage percentages; no run
             * ever recorded whether a pointer event actually reached the overlay window. Logging
             * each MotionEvent (with its raw screen coordinates) makes that observable in a CI
             * logcat: if a DOWN at the icon's centre appears here, the touch reached this window
             * and the fault is downstream of input routing; if nothing appears after tapOverlay(),
             * the small window's touchable region never received the event. Paired with the
             * existing "Overlay tapped" log in handleTap(), this distinguishes a routing miss from
             * a click-detector or launch failure.
             */
            override fun dispatchTouchEvent(event: MotionEvent): Boolean {
                DebugLog.log(
                    "Overlay dispatchTouchEvent: action=${event.actionMasked} " +
                        "raw=(${event.rawX}, ${event.rawY}) local=(${event.x}, ${event.y})"
                )
                return super.dispatchTouchEvent(event)
            }

            override fun onWindowFocusChanged(hasFocus: Boolean) {
                super.onWindowFocusChanged(hasFocus)
                // Only invoke focus callbacks when the focusable-overlay mode is active.
                // With FLAG_NOT_FOCUSABLE (default), the window never receives focus, so
                // onWindowFocusChanged(false) fires immediately after show() — calling
                // onFocusLost() here would hide the overlay the instant it appears (Issue #66).
                if (!prefsManager.focusableOverlay) return
                if (hasFocus) {
                    DebugLog.log("Overlay gained window focus")
                    onFocusGained()
                } else {
                    DebugLog.log("Overlay lost window focus — task switcher or system surface active")
                    onFocusLost()
                }
            }
        }
        imageView.scaleType = ImageView.ScaleType.FIT_CENTER
        updateIconDrawable(imageView)
        imageView.setOnClickListener { handleTap() }
        return imageView
    }

    /**
     * WG-01: Extract icon live from PackageManager each time.
     * WG-02: Wrap icon in SquircleDrawable so the squircle shape is applied at draw time,
     *        independent of the device launcher's adaptive-icon mask (Issue #188).
     * AC-04: Show placeholder if gallery app uninstalled.
     */
    private fun updateIconDrawable(imageView: ImageView) {
        val galleryPackage = prefsManager.galleryPackage
        val icon = getGalleryIcon(galleryPackage)
        imageView.setImageDrawable(icon)
    }

    /**
     * Returns a [SquircleDrawable] wrapping the gallery app icon, ensuring the overlay is
     * squircle-shaped regardless of the device launcher's adaptive-icon mask (Issue #188).
     *
     * On [google_apis] API-35 emulators the launcher clips adaptive icons to a circle.
     * Loading the raw [AdaptiveIconDrawable] resource (Issue #39) is not sufficient because
     * [AdaptiveIconDrawable.draw] still applies the device's icon mask internally. Wrapping in
     * [SquircleDrawable] draws the background and foreground layers directly, bypassing that mask.
     */
    private fun getGalleryIcon(packageName: String?): Drawable {
        return SquircleDrawable(getRawGalleryIcon(packageName))
    }

    private fun getRawGalleryIcon(packageName: String?): Drawable {
        if (packageName != null) {
            try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    val appInfo = context.packageManager.getApplicationInfo(packageName, 0)
                    if (appInfo.icon != 0) {
                        val pkgResources = context.packageManager.getResourcesForApplication(appInfo)
                        val rawIcon = pkgResources.getDrawable(appInfo.icon, null)
                        if (rawIcon is AdaptiveIconDrawable) {
                            return rawIcon
                        }
                    }
                }
                // Pre-API 26 or non-adaptive icon: fall back to getApplicationIcon().
                return context.packageManager.getApplicationIcon(packageName)
            } catch (_: PackageManager.NameNotFoundException) {
                // Gallery app uninstalled — fall through to warning placeholder (AC-04)
            } catch (_: Exception) {
                // Resource load failed — fall back to getApplicationIcon()
                try {
                    return context.packageManager.getApplicationIcon(packageName)
                } catch (_: PackageManager.NameNotFoundException) {
                    // Gallery app uninstalled — fall through to warning placeholder (AC-04)
                }
            }
            // AC-04/M3: Gallery configured but uninstalled — show placeholder with warning badge.
            return buildWarningPlaceholder()
        }
        // AC-03: No gallery configured — plain placeholder.
        // L7: guarantee non-null via fallback chain.
        return ContextCompat.getDrawable(context, R.drawable.ic_gallery_placeholder)
            ?: ContextCompat.getDrawable(context, android.R.drawable.ic_menu_gallery)!!
    }

    /**
     * AC-04/M3: Combines the placeholder icon with a small warning badge in the bottom-right
     * corner using LayerDrawable, so the user knows the configured gallery app is missing.
     * Uses android.R.drawable.ic_dialog_alert scaled to ~25% of the icon as the badge.
     */
    private fun buildWarningPlaceholder(): Drawable {
        // L7: guarantee non-null at each step
        val placeholder: Drawable = ContextCompat.getDrawable(context, R.drawable.ic_gallery_placeholder)
            ?: ContextCompat.getDrawable(context, android.R.drawable.ic_menu_gallery)!!
        val badge: Drawable = ContextCompat.getDrawable(context, android.R.drawable.ic_dialog_alert)
            ?: return placeholder // if badge unavailable, fall back to plain placeholder

        // Position the badge in the bottom-right quadrant (inset by 50% from top-left).
        val layers = arrayOf(placeholder, badge)
        val layered = LayerDrawable(layers)
        val badgeLayerIndex = 1
        // Inset: badge occupies the bottom-right quarter of the icon bounds.
        layered.setLayerInsetRelative(badgeLayerIndex, placeholder.intrinsicWidth / 2, placeholder.intrinsicHeight / 2, 0, 0)
        return layered
    }

    /**
     * AC-01 through AC-04: Handle tap based on lock state and configuration.
     */
    private fun handleTap() {
        val isLocked = keyguardManager.isKeyguardLocked
        val galleryPackage = prefsManager.galleryPackage
        val isGalleryInstalled = galleryPackage != null &&
            PermissionHelper.isAppInstalled(context, galleryPackage)

        DebugLog.log("Overlay tapped: locked=$isLocked, gallery=$galleryPackage, installed=$isGalleryInstalled")

        val action = TapActionResolver.resolve(isLocked, galleryPackage, isGalleryInstalled)
        executeTapAction(action)
    }

    private fun executeTapAction(action: TapAction) {
        when (action) {
            is TapAction.LaunchGallery -> launchGalleryApp(action.packageName)
            is TapAction.LaunchSecureViewer -> launchSecureViewer()
            is TapAction.LaunchPicker, is TapAction.LaunchPickerGalleryMissing -> launchPicker()
            is TapAction.ShowUnlockToSetupToast ->
                Toast.makeText(context, R.string.toast_unlock_to_setup, Toast.LENGTH_SHORT).show()
            is TapAction.ShowGalleryNotFoundToast ->
                Toast.makeText(context, R.string.toast_gallery_not_found, Toast.LENGTH_SHORT).show()
        }
    }

    private fun launchGalleryApp(packageName: String) {
        val intent = context.packageManager.getLaunchIntentForPackage(packageName)
        if (intent != null) {
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            context.startActivity(intent)
            DebugLog.log("Launched gallery app: $packageName")
            onGalleryLaunched()
        }
    }

    private fun launchPicker() {
        val intent = Intent(context, PickerActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            putExtra(PickerActivity.EXTRA_LAUNCH_AFTER_PICK, true)
        }
        context.startActivity(intent)
        DebugLog.log("Launched gallery app picker (JIT)")
    }

    private fun launchSecureViewer() {
        val intent = Intent(context, SecureViewerActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        context.startActivity(intent)
        DebugLog.log("Launched secure viewer")
    }

    private fun createLayoutParams(): WindowManager.LayoutParams {
        @Suppress("DEPRECATION")
        val showWhenLockedFlag = WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED

        // M8: On API 30+ use currentWindowMetrics for correct bounds in split-screen;
        // fall back to displayMetrics on older API.
        val (displayWidth, displayHeight) = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val bounds = windowManager.currentWindowMetrics.bounds
            bounds.width() to bounds.height()
        } else {
            @Suppress("DEPRECATION")
            val dm = android.util.DisplayMetrics()
            windowManager.defaultDisplay.getMetrics(dm)
            dm.widthPixels to dm.heightPixels
        }

        val aspectRatio = AspectRatioUtil.quantize(displayWidth, displayHeight)
        val position = prefsManager.getOverlayPosition(aspectRatio)

        val sizePx = calculateOverlaySizePx(position.sizePercent, displayWidth, displayHeight)
        val xPx = calculateOverlayXPx(position.xPercent, displayWidth, sizePx)
        val yPx = calculateOverlayYPx(position.yPercent, displayHeight, sizePx)

        // FLAG_NOT_FOCUSABLE: safe default — overlay never steals input focus.
        // Experimental focusable path: omit FLAG_NOT_FOCUSABLE so the window can receive focus
        // events, enabling onWindowFocusChanged(false) as a task-switcher signal.
        // FLAG_NOT_TOUCH_MODAL is also set to keep touch events outside the overlay's bounds
        // passing through to the camera app. Trade-off: the focusable window may steal
        // volume/power key events from the camera app even when dispatchKeyEvent returns false.
        //
        // FLAG_LAYOUT_IN_SCREEN: without this flag, a TYPE_APPLICATION_OVERLAY window's
        // Gravity.TOP|START origin is offset below the system status bar, so x/y (computed
        // above relative to the full display size) land the overlay too far down the screen
        // (Issue #229 — the overlay rendered ~128 px lower than the configured yPercent).
        // This flag makes (x, y) relative to the true physical-screen origin (0, 0), matching
        // calculateOverlayXPx/calculateOverlayYPx's assumptions, and on its own is sufficient to
        // place the surface correctly (test1a confirms this; it does not require
        // FLAG_LAYOUT_NO_LIMITS). The focusable branch additionally keeps FLAG_LAYOUT_NO_LIMITS,
        // but only because that path is not exercised by the failing default-prefs test, not
        // because positioning needs it.
        //
        // FLAG_NOT_TOUCH_MODAL (both branches): the overlay is a small (sizePx x sizePx)
        // window. This flag forwards pointer events that fall *outside* the window bounds to the
        // windows behind it (so the surrounding camera-app touches pass through), while in-bounds
        // touches go to this window's clickable ImageView.
        //
        // FLAG_LAYOUT_NO_LIMITS is deliberately NOT set on the non-focusable branch (Issue #230 /
        // #397). The overlay icon is small and positioned well inside the display (default 20% /
        // 69%, ~16% of the min dimension), so it never needs to extend past the screen limits.
        // FLAG_NOT_TOUCH_MODAL alone (with FLAG_LAYOUT_NO_LIMITS kept) was observed in CI to still
        // leave test2a_emptyGalleryNoGreenAfterTap a no-op: the green camera feed stayed
        // full-screen (~87%) and handleTap() never fired, even though the tap lands dead-centre on
        // the rendered icon (test1a confirms the surface position). The remaining suspect is the
        // window's touchable input region: with FLAG_LAYOUT_NO_LIMITS the frame the WM uses to
        // derive the touchable region can diverge from the on-screen surface for a small window,
        // so the injected in-bounds tap is not routed to this window. A full-screen
        // FLAG_NOT_TOUCH_MODAL window *did* deliver the tap (but swallowed every camera touch, so
        // it was reverted); the only structural difference from the small window was its size /
        // touchable extent. Dropping FLAG_LAYOUT_NO_LIMITS keeps the small window's frame within
        // screen limits so its touchable region matches the FLAG_LAYOUT_IN_SCREEN-placed surface.
        //
        // The focusable branch keeps FLAG_LAYOUT_NO_LIMITS unchanged (it is not exercised by the
        // failing default-prefs test path).
        val windowFlags = if (prefsManager.focusableOverlay) {
            WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                showWhenLockedFlag
        } else {
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                showWhenLockedFlag
        }

        return WindowManager.LayoutParams(
            sizePx,
            sizePx,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            windowFlags,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = xPx
            y = yPx
        }
    }
}
