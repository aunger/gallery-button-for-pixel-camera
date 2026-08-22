package com.gb4pc.util

import android.app.Activity
import android.app.AppOpsManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import com.gb4pc.Constants
import com.gb4pc.data.PrefsManager

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
    fun hasMediaPermission(context: Context): Boolean = context.checkSelfPermission(mediaPermission) == PackageManager.PERMISSION_GRANTED

    /**
     * The dangerous runtime permission that backs [hasMediaPermission]: `READ_MEDIA_IMAGES` on
     * API 33+, `READ_EXTERNAL_STORAGE` below. Exposed so the screens that *request* it name the
     * same permission the check reads, rather than each re-deriving the API-level split.
     */
    val mediaPermission: String
        get() =
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                android.Manifest.permission.READ_MEDIA_IMAGES
            } else {
                android.Manifest.permission.READ_EXTERNAL_STORAGE
            }

    /**
     * Which of Android's two re-request routes can still move [permission] forward (issue #572).
     *
     * Once Android considers a permission permanently denied (an explicit "Don't ask again", or
     * simply enough prior denials), `requestPermissions()` returns DENIED synchronously without
     * showing any dialog at all. There is no user-visible failure; the button just does nothing.
     * A screen that hardcodes the dialog route is therefore a dead end for any user in that state,
     * and a screen that hardcodes the Settings route sends a first-time user on a detour through
     * system Settings for a grant the in-app dialog could have collected in one tap. Deciding at
     * the point of use keeps every touchpoint correct regardless of how the user got there.
     *
     * `shouldShowRequestPermissionRationale()` is the platform's own signal for "a fresh request
     * will still show UI", but it cannot carry the decision alone: it returns `false` *both*
     * before the first-ever ask and after a permanent denial. [hasBeenRequestedBefore] (persisted
     * by [PrefsManager.hasRequestedRuntimePermission]) is what separates those two states, so:
     *
     * - never asked yet -> [PermissionRequestRoute.DIALOG] (the first ask always shows UI)
     * - asked before, rationale `true` -> [PermissionRequestRoute.DIALOG] (denied, but not fixed)
     * - asked before, rationale `false` -> [PermissionRequestRoute.SETTINGS] (permanently denied)
     */
    fun permissionRequestRoute(
        activity: Activity,
        permission: String,
        hasBeenRequestedBefore: Boolean,
    ): PermissionRequestRoute =
        if (!hasBeenRequestedBefore || activity.shouldShowRequestPermissionRationale(permission)) {
            PermissionRequestRoute.DIALOG
        } else {
            PermissionRequestRoute.SETTINGS
        }

    /**
     * Ask for [permission] by whichever route [permissionRequestRoute] says can still work, and
     * record the ask so a later call can tell "never asked" from "permanently denied".
     *
     * [launchDialog] fires the caller's own `ActivityResultContracts.RequestPermission` launcher;
     * it is a lambda rather than the launcher itself so this helper stays independent of the
     * activity-result plumbing (and testable without it).
     */
    fun requestRuntimePermission(
        activity: Activity,
        permission: String,
        prefsManager: PrefsManager,
        launchDialog: () -> Unit,
    ) {
        val route =
            permissionRequestRoute(
                activity = activity,
                permission = permission,
                hasBeenRequestedBefore = prefsManager.hasRequestedRuntimePermission(permission),
            )
        when (route) {
            PermissionRequestRoute.DIALOG -> {
                prefsManager.setRuntimePermissionRequested(permission, true)
                launchDialog()
            }

            PermissionRequestRoute.SETTINGS -> {
                activity.startActivity(appDetailsSettingsIntent(activity))
            }
        }
    }

    /**
     * The app's own page in system Settings, where a permanently denied runtime permission can
     * still be toggled back on. Unlike Usage Access or Draw Over Apps, an ordinary dangerous
     * permission has no dedicated settings sub-screen to deep-link to.
     */
    fun appDetailsSettingsIntent(context: Context): Intent =
        Intent(
            Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
            Uri.parse("package:${context.packageName}"),
        )
}

/**
 * The two ways Android lets an app move a *dangerous runtime* permission forward, and which one
 * is actually usable right now (issue #572).
 */
enum class PermissionRequestRoute {
    /** Fire the system permission dialog. Only works while Android will still show it. */
    DIALOG,

    /** Send the user to the app's page in system Settings, where the grant toggle lives. */
    SETTINGS,
}
