package com.gb4pc.util

import android.app.AppOpsManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import com.gb4pc.Constants

/**
 * Centralized permission and installation checks (§2).
 */
object PermissionHelper {
    fun isPixelCameraInstalled(context: Context): Boolean =
        try {
            context.packageManager.getPackageInfo(Constants.PIXEL_CAMERA_PACKAGE, 0)
            true
        } catch (_: PackageManager.NameNotFoundException) {
            false
        }

    fun isAppInstalled(
        context: Context,
        packageName: String,
    ): Boolean = context.packageManager.getLaunchIntentForPackage(packageName) != null

    fun hasUsageStatsPermission(context: Context): Boolean {
        val appOps = context.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
        val mode =
            appOps.unsafeCheckOpNoThrow(
                AppOpsManager.OPSTR_GET_USAGE_STATS,
                android.os.Process.myUid(),
                context.packageName,
            )
        return mode == AppOpsManager.MODE_ALLOWED
    }

    fun hasOverlayPermission(context: Context): Boolean = Settings.canDrawOverlays(context)

    fun isBatteryOptimizationExcluded(context: Context): Boolean {
        val pm = context.getSystemService(Context.POWER_SERVICE) as PowerManager
        return pm.isIgnoringBatteryOptimizations(context.packageName)
    }

    fun hasNotificationPermission(context: Context): Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            context.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) ==
                PackageManager.PERMISSION_GRANTED
        } else {
            true // Not required before API 33
        }

    /**
     * True when the app holds *full* read access to the shared image collection, which it
     * needs to see photos Pixel Camera writes to MediaStore (issue #509).
     *
     * On API 33+ this is `READ_MEDIA_IMAGES`; below that it is `READ_EXTERNAL_STORAGE`. Both
     * are dangerous runtime permissions: declaring them in the manifest does not grant them,
     * and without the grant a MediaStore query silently returns only rows owned by this app
     * (scoped storage, API 29+), never Pixel Camera's, so the overlay thumbnail can never update.
     *
     * On API 34+ a user may instead choose "Select photos" (partial access), which grants only
     * `READ_MEDIA_VISUAL_USER_SELECTED`. That subset can never include a photo taken seconds ago,
     * so it is insufficient for this feature. This check deliberately requires the full-access
     * `READ_MEDIA_IMAGES` grant and treats partial access as not granted, so the setup step and
     * the main-screen banner keep prompting until the user allows all photos.
     *
     * This single check is correct only because the manifest also declares
     * `READ_MEDIA_VISUAL_USER_SELECTED`. That declaration opts the app out of Android's
     * backward-compatibility mode, under which a partial grant would *temporarily* report
     * `READ_MEDIA_IMAGES` as granted (flagged `PackageManager.FLAG_PERMISSION_REVOKED_COMPAT`) while
     * the app is foregrounded, and this check would then mistake partial access for full access
     * (issue #568). With the permission declared, `checkSelfPermission(READ_MEDIA_IMAGES)` returns
     * `PERMISSION_DENIED` for a partial grant, so no flag inspection is needed here (a normal app
     * cannot read `FLAG_PERMISSION_REVOKED_COMPAT` anyway; `getPermissionFlags()` is privileged).
     */
    fun hasMediaPermission(context: Context): Boolean {
        val permission =
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                android.Manifest.permission.READ_MEDIA_IMAGES
            } else {
                android.Manifest.permission.READ_EXTERNAL_STORAGE
            }
        return context.checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED
    }
}
