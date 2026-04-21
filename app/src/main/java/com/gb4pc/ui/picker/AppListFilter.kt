package com.gb4pc.ui.picker

import android.content.Context
import android.content.Intent
import com.gb4pc.Constants

/**
 * Represents an installed app for the picker list.
 */
data class AppInfo(
    val label: String,
    val packageName: String
)

/**
 * Filters and sorts the app list for the gallery app picker (UI-05, UI-06, UI-09).
 */
object AppListFilter {
    fun filter(apps: List<AppInfo>, query: String, ownPackage: String): List<AppInfo> {
        val excluded = setOf(Constants.PIXEL_CAMERA_PACKAGE, ownPackage)
        return apps
            .filter { it.packageName !in excluded }
            .filter { app ->
                if (query.isBlank()) true
                else app.label.contains(query, ignoreCase = true) ||
                    app.packageName.contains(query, ignoreCase = true)
            }
            .sortedBy { it.label.lowercase() }
    }

    /**
     * Returns the set of package names that are plausibly photo-related, built from
     * the union of two PackageManager queries:
     *   1. Apps declaring CATEGORY_APP_GALLERY
     *   2. Apps that can handle ACTION_VIEW with image/* MIME type
     */
    fun buildPhotoRelatedPackages(context: Context): Set<String> {
        val pm = context.packageManager
        val packages = mutableSetOf<String>()
        runCatching {
            val galleryIntent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_APP_GALLERY)
            pm.queryIntentActivities(galleryIntent, 0).forEach { packages.add(it.activityInfo.packageName) }
        }
        runCatching {
            val imageIntent = Intent(Intent.ACTION_VIEW).apply { type = "image/*" }
            pm.queryIntentActivities(imageIntent, 0).forEach { packages.add(it.activityInfo.packageName) }
        }
        return packages
    }
}
