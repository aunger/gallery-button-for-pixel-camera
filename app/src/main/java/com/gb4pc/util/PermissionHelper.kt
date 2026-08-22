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
     *
     * [hasBeenRequestedBefore] is an app-side shadow of state the platform owns, so it is only as
     * good as what wrote it. It is empty for an install upgraded from a build before it existed,
     * which is why [seedPermissionRequestHistoryForUpgrade] backfills it once at startup; without
     * that, an already permanently denied permission would read as "never asked" here and route to
     * a dialog Android refuses to show.
     *
     * The shadow can still drift from the platform, in either direction. Neither drift is a dead
     * end, because whichever route it picks can still collect the grant:
     *
     * - No recorded ask while the platform holds a denial: the `!hasBeenRequestedBefore`
     *   short-circuit routes to the dialog, Android shows nothing, and that tap records the ask,
     *   so the next one reaches Settings. `E2EFixture.resetPermissionRequestHistory` puts a suite
     *   into exactly this state on purpose, to force the dialog route for a suite that drives the
     *   real dialog.
     * - A recorded ask while the platform would still show a dialog: clearing app data does this.
     *   It resets the flag and the platform's own grant state together, but leaves the install
     *   timestamps alone, so on an already-updated install the backfill fires again and assumes an
     *   ask that no longer happened. The route then degrades to Settings where the dialog would
     *   have served: the safe direction rather than the ideal one, and it stays that way, since
     *   only the dialog branch ever records anything.
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
     * Backfills [PrefsManager.hasRequestedRuntimePermission] once, for an install that predates
     * the flag existing at all (issue #572).
     *
     * The routing in [permissionRequestRoute] leans on an app-side record of the first ask, and
     * that record is empty for every install upgraded from a build before this one. Without this
     * backfill the very user the issue describes, someone who already denied a permission into the
     * permanently denied state, would be read as "never asked" and sent to a dialog Android
     * refuses to show: the identical silent no-op, on the first tap after upgrading.
     *
     * `firstInstallTime != lastUpdateTime` is the platform's own statement that this install
     * predates the running build, which is exactly the population whose history was never
     * recorded. A fresh install has equal times, is left alone, and records its own history
     * accurately from its first ask onward.
     *
     * Only *ungranted* permissions are seeded. A granted one has no route to choose, and if the
     * user later revokes it from Settings, Android will show a dialog again, which an unseeded
     * flag correctly routes to.
     *
     * Seeding errs toward [PermissionRequestRoute.SETTINGS] for a permission the user in fact
     * never denied (they skipped the step instead). That costs a trip to Settings for a grant the
     * dialog could have taken in one tap, once, on the upgrade. The opposite error costs a button
     * that silently does nothing, which is the bug being fixed, so the asymmetry is deliberate.
     */
    fun seedPermissionRequestHistoryForUpgrade(
        context: Context,
        prefsManager: PrefsManager,
    ) {
        if (prefsManager.isPermissionHistorySeeded) return
        if (isUpgradedInstall(context)) {
            if (!hasMediaPermission(context)) {
                prefsManager.setRuntimePermissionRequested(mediaPermission, true)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && !hasNotificationPermission(context)) {
                prefsManager.setRuntimePermissionRequested(android.Manifest.permission.POST_NOTIFICATIONS, true)
            }
        }
        prefsManager.isPermissionHistorySeeded = true
    }

    private fun isUpgradedInstall(context: Context): Boolean =
        try {
            val info = context.packageManager.getPackageInfo(context.packageName, 0)
            info.firstInstallTime != info.lastUpdateTime
        } catch (_: PackageManager.NameNotFoundException) {
            false
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
