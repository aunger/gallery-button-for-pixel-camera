package com.gb4pc.ui.picker

import org.junit.Assert.*
import org.junit.Test

class AppListFilterTest {

    private val apps = listOf(
        AppInfo("Gallery", "com.example.gallery"),
        AppInfo("Photos", "com.google.photos"),
        AppInfo("Camera", "com.google.android.GoogleCamera"),
        AppInfo("GB4PC", "com.gb4pc"),
        AppInfo("Simple Gallery", "com.simplemobiletools.gallery")
    )

    @Test
    fun `filter excludes Pixel Camera and GB4PC per UI-09`() {
        val filtered = AppListFilter.filter(apps, "", "com.gb4pc")
        assertFalse(filtered.any { it.packageName == "com.google.android.GoogleCamera" })
        assertFalse(filtered.any { it.packageName == "com.gb4pc" })
        assertEquals(3, filtered.size)
    }

    @Test
    fun `filter by search query is case insensitive`() {
        val filtered = AppListFilter.filter(apps, "gall", "com.gb4pc")
        assertEquals(2, filtered.size) // Gallery and Simple Gallery
    }

    @Test
    fun `filter with empty query returns all except excluded`() {
        val filtered = AppListFilter.filter(apps, "", "com.gb4pc")
        assertEquals(3, filtered.size)
    }

    @Test
    fun `filter results are sorted alphabetically per UI-05`() {
        val filtered = AppListFilter.filter(apps, "", "com.gb4pc")
        assertEquals("Gallery", filtered[0].label)
        assertEquals("Photos", filtered[1].label)
        assertEquals("Simple Gallery", filtered[2].label)
    }

    @Test
    fun `filter matches on package name too`() {
        val filtered = AppListFilter.filter(apps, "simplemobile", "com.gb4pc")
        assertEquals(1, filtered.size)
        assertEquals("Simple Gallery", filtered[0].label)
    }
}

data class AppInfo(val label: String, val packageName: String)

object AppListFilter {
    fun filter(apps: List<AppInfo>, query: String, ownPackage: String): List<AppInfo> {
        val excluded = setOf("com.google.android.GoogleCamera", ownPackage)
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
