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
import android.view.WindowManager
import android.widget.FrameLayout
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

    /**
     * The full-screen host window actually added to the [WindowManager]. The gallery icon is a
     * positioned child of this host (see [createOverlayHost]).
     *
     * The window spans the whole display so its input/touchable region coincides with its
     * rendered surface. A small positioned [TYPE_APPLICATION_OVERLAY] window's touchable region
     * is derived from the decor-inset-fitted frame, which diverges from the surface after
     * [WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN] shifts the surface to the physical
     * origin (Issue #229's fix). That divergence left taps on the rendered icon landing outside
     * the window's touchable region, so the tap never reached [handleTap] (Issue #230). With a
     * full-screen window there is no inset to subtract, so a tap on the icon's pixels hits the
     * icon child and fires its click listener.
     */
    private var hostView: FrameLayout? = null

    /** The gallery icon child view. Holds the icon/thumbnail drawable and the tap listener. */
    private var overlayView: ImageView? = null
    private var isShowing = false

    fun show() {
        if (isShowing) {
            return
        }

        val (host, icon) = createOverlayHost()
        val params = createLayoutParams()

        try {
            windowManager.addView(host, params)
            hostView = host
            overlayView = icon
            isShowing = true
            DebugLog.log("Overlay shown")
        } catch (e: Exception) {
            DebugLog.log("Failed to show overlay: ${e.message}")
        }
    }

    fun hide() {
        hostView?.let { view ->
            try {
                windowManager.removeView(view)
            } catch (_: Exception) {
                // View may already be removed
            }
        }
        hostView = null
        overlayView = null
        isShowing = false
        DebugLog.log("Overlay hidden")
    }

    fun updatePosition() {
        val icon = overlayView ?: return
        val host = hostView ?: return
        if (!isShowing) return
        // The window is full-screen; only the icon child's offset/size changes when the
        // configured position changes. Re-apply the child's layout params in place.
        icon.layoutParams = iconLayoutParams()
        try {
            host.requestLayout()
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

    /**
     * Builds the full-screen host [FrameLayout] and its gallery-icon [ImageView] child.
     *
     * The host fills the window (which spans the whole display) and is NOT clickable, so touches
     * outside the icon are not consumed by the view hierarchy and -- together with
     * [WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL] on the window -- pass through to the
     * camera app below. Only the icon child is clickable, so a tap on the icon fires [handleTap]
     * while taps elsewhere reach the camera (Issue #230).
     *
     * The key and focus overrides that used to live on the icon view now live on the host, since
     * the host is the attached root that receives window-level key and focus events.
     */
    private fun createOverlayHost(): Pair<FrameLayout, ImageView> {
        // When focusable overlay is enabled we need a custom subclass to handle key and focus
        // events on the root (host) view.
        val host = object : FrameLayout(context) {
            /**
             * Do not consume key events — pass them through to the camera app.
             *
             * NOTE (Issue #55 open question): Even with dispatchKeyEvent returning false, a
             * focusable TYPE_APPLICATION_OVERLAY window may steal input focus from the camera
             * app when first shown. If that happens, volume-as-shutter and zoom keys will be
             * broken regardless of this override. This must be verified on a real device.
             */
            override fun dispatchKeyEvent(event: KeyEvent): Boolean = false

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
        // The host itself must not be clickable, so touches outside the icon are not consumed and
        // pass through to the camera app.
        host.isClickable = false

        val imageView = ImageView(context)
        imageView.scaleType = ImageView.ScaleType.FIT_CENTER
        updateIconDrawable(imageView)
        imageView.setOnClickListener { handleTap() }

        host.addView(imageView, iconLayoutParams())
        return host to imageView
    }

    /**
     * Computes the icon child's [FrameLayout.LayoutParams]: its size and its top/left offset
     * within the full-screen host, derived from the configured overlay position via the same
     * pure functions used for the previous per-window x/y ([calculateOverlaySizePx],
     * [calculateOverlayXPx], [calculateOverlayYPx]). Keeping these functions unchanged means the
     * icon renders at the same pixel position as before, so the position assertion in
     * test1a_overlayShowsBlueAtConfiguredPosition still holds.
     */
    private fun iconLayoutParams(): FrameLayout.LayoutParams {
        val (displayWidth, displayHeight) = displayBounds()
        val aspectRatio = AspectRatioUtil.quantize(displayWidth, displayHeight)
        val position = prefsManager.getOverlayPosition(aspectRatio)

        val sizePx = calculateOverlaySizePx(position.sizePercent, displayWidth, displayHeight)
        val xPx = calculateOverlayXPx(position.xPercent, displayWidth, sizePx)
        val yPx = calculateOverlayYPx(position.yPercent, displayHeight, sizePx)

        return FrameLayout.LayoutParams(sizePx, sizePx).apply {
            gravity = Gravity.TOP or Gravity.START
            leftMargin = xPx
            topMargin = yPx
        }
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

    /**
     * The full display bounds in pixels.
     *
     * M8: On API 30+ use currentWindowMetrics for correct bounds in split-screen; fall back to
     * displayMetrics on older API. The same bounds drive the full-screen window size and the icon
     * child's offset, so the icon lands at the configured percent of the real display.
     */
    private fun displayBounds(): Pair<Int, Int> {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val bounds = windowManager.currentWindowMetrics.bounds
            bounds.width() to bounds.height()
        } else {
            @Suppress("DEPRECATION")
            val dm = android.util.DisplayMetrics()
            @Suppress("DEPRECATION")
            windowManager.defaultDisplay.getMetrics(dm)
            dm.widthPixels to dm.heightPixels
        }
    }

    private fun createLayoutParams(): WindowManager.LayoutParams {
        @Suppress("DEPRECATION")
        val showWhenLockedFlag = WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED

        // The overlay window spans the whole display. A full-screen window's input/touchable
        // region matches its rendered surface (no decor-inset subtraction), so a tap on the
        // icon's pixels reaches the icon child (Issue #230). The icon is positioned within this
        // window by iconLayoutParams(), not by the window's own x/y.
        //
        // FLAG_NOT_TOUCH_MODAL is REQUIRED on this full-screen window: without it the window is
        // touch-modal and would swallow every touch on screen, blocking the camera app. With it,
        // only the icon child (the sole clickable view) is touchable; touches elsewhere pass
        // through to the camera below.
        //
        // FLAG_NOT_FOCUSABLE (non-focusable default): the overlay never steals input focus.
        // Focusable path: omit FLAG_NOT_FOCUSABLE so the window can receive focus events,
        // enabling onWindowFocusChanged(false) as a task-switcher signal. Trade-off: the
        // focusable window may steal volume/power key events from the camera app even when
        // dispatchKeyEvent returns false.
        //
        // FLAG_LAYOUT_IN_SCREEN + FLAG_LAYOUT_NO_LIMITS keep the full-screen window anchored at
        // the true physical-screen origin (0, 0) and allowed to extend into the system-bar areas,
        // so the icon child's (leftMargin, topMargin) are relative to the real display origin,
        // matching calculateOverlayXPx/calculateOverlayYPx's assumptions (Issue #229).
        val windowFlags = if (prefsManager.focusableOverlay) {
            WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                showWhenLockedFlag
        } else {
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                showWhenLockedFlag
        }

        return WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            windowFlags,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = 0
            y = 0
        }
    }
}
