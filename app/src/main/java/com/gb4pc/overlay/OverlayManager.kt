package com.gb4pc.overlay

import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.PixelFormat
import android.graphics.drawable.AdaptiveIconDrawable
import android.graphics.drawable.Drawable
import android.os.Build
import android.view.Gravity
import android.view.WindowManager
import android.widget.ImageView
import android.widget.Toast
import com.gb4pc.Constants
import com.gb4pc.R
import com.gb4pc.data.AspectRatioUtil
import com.gb4pc.data.PrefsManager
import com.gb4pc.ui.picker.PickerActivity
import com.gb4pc.util.DebugLog
import com.gb4pc.util.PermissionHelper
import com.gb4pc.viewer.SecureViewerActivity

/**
 * Manages the overlay window that covers Pixel Camera's gallery button (§4).
 */
class OverlayManager(
    private val context: Context,
    private val prefsManager: PrefsManager
) {
    private val windowManager = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager
    private val keyguardManager = context.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
    private var overlayView: ImageView? = null
    private var isShowing = false

    fun show() {
        if (isShowing) {
            updateIcon()
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

    private fun createOverlayView(): ImageView {
        val imageView = ImageView(context)
        imageView.scaleType = ImageView.ScaleType.FIT_CENTER
        imageView.clipToOutline = true

        updateIconDrawable(imageView)
        imageView.setOnClickListener { handleTap() }

        return imageView
    }

    private fun updateIcon() {
        overlayView?.let { updateIconDrawable(it) }
    }

    /**
     * WG-01: Extract icon live from PackageManager each time.
     * WG-02: Use adaptive icon shape mask.
     * AC-04: Show placeholder if gallery app uninstalled.
     */
    private fun updateIconDrawable(imageView: ImageView) {
        val galleryPackage = prefsManager.galleryPackage
        val icon = getGalleryIcon(galleryPackage)
        imageView.setImageDrawable(icon)
    }

    private fun getGalleryIcon(packageName: String?): Drawable {
        if (packageName != null) {
            try {
                return context.packageManager.getApplicationIcon(packageName)
            } catch (_: PackageManager.NameNotFoundException) {
                // Gallery app uninstalled — fall through to placeholder
            }
        }
        // AC-03/AC-04: placeholder icon
        return context.getDrawable(R.drawable.ic_gallery_placeholder)
            ?: context.getDrawable(android.R.drawable.ic_menu_gallery)!!
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

        when {
            // AC-03: No gallery app configured
            galleryPackage == null -> {
                if (isLocked) {
                    Toast.makeText(context, R.string.toast_unlock_to_setup, Toast.LENGTH_SHORT).show()
                } else {
                    launchPicker()
                }
            }
            // AC-04: Gallery app uninstalled
            !isGalleryInstalled -> {
                if (isLocked) {
                    Toast.makeText(context, R.string.toast_gallery_not_found, Toast.LENGTH_SHORT).show()
                } else {
                    launchPicker()
                }
            }
            // AC-02: Device locked — open secure viewer
            isLocked -> {
                launchSecureViewer()
            }
            // AC-01: Device unlocked — launch gallery app
            else -> {
                launchGalleryApp(galleryPackage)
            }
        }
    }

    private fun launchGalleryApp(packageName: String) {
        val intent = context.packageManager.getLaunchIntentForPackage(packageName)
        if (intent != null) {
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            context.startActivity(intent)
            DebugLog.log("Launched gallery app: $packageName")
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
        val display = context.resources.displayMetrics
        val displayWidth = display.widthPixels
        val displayHeight = display.heightPixels

        val aspectRatio = AspectRatioUtil.quantize(displayWidth, displayHeight)
        val position = prefsManager.getOverlayPosition(aspectRatio)

        val sizePx = OverlayPositionCalculator.calculateSizePx(
            position.sizePercent, displayWidth, displayHeight
        )
        val xPx = OverlayPositionCalculator.calculateXPx(
            position.xPercent, displayWidth, sizePx
        )
        val yPx = OverlayPositionCalculator.calculateYPx(
            position.yPercent, displayHeight, sizePx
        )

        return WindowManager.LayoutParams(
            sizePx,
            sizePx,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = xPx
            y = yPx
        }
    }
}
