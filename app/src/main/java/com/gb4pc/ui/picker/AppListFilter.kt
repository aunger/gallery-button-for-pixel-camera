package com.gb4pc.ui.picker

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
}
