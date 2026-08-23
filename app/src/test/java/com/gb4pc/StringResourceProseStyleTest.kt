package com.gb4pc

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import java.lang.reflect.Modifier

/**
 * Regression guard for issue #935.
 *
 * `.claude/rules/prose-style.md` applies to every file in the repo, `strings.xml` included, and
 * asks prose to prefer widely compatible characters: no em or en dashes, no ellipsis character,
 * and no angled or smart quotes. Eleven user-visible strings had drifted past that rule before
 * issue #935 swept them, and a twelfth was caught only by a human reviewing PR #934. Copy is
 * written by hand, often pasted from an editor that substitutes these characters on its own, so
 * nothing but a test keeps them out.
 *
 * The banned characters are spelled as escapes below so that this file, which has to name them,
 * is not itself a place they can be copied out of by accident.
 *
 * This reads resources through [Context.getString] rather than parsing `strings.xml`, so it checks
 * exactly what the app displays: the escape-decoded value of every string this module ships.
 * `android.nonTransitiveRClass=true` (see `gradle.properties`) keeps [R.string] to this module's
 * own resources, so a dependency's copy cannot fail this test.
 */
@RunWith(RobolectricTestRunner::class)
class StringResourceProseStyleTest {
    /** Characters the prose-style rule bans, mapped to the wording used in the failure message. */
    private val bannedCharacters =
        mapOf(
            '\u2014' to "an em dash (use a comma, semicolon, parentheses, or '--')",
            '\u2013' to "an en dash (use a comma, semicolon, parentheses, or '-')",
            '\u2026' to "an ellipsis character (use three periods)",
            '\u2018' to "a smart single quote (use ')",
            '\u2019' to "a smart single quote (use ')",
            '\u201c' to "a smart double quote (use \")",
            '\u201d' to "a smart double quote (use \")",
            '\u00ab' to "an angle quote (use \")",
            '\u00bb' to "an angle quote (use \")",
        )

    /** Every `public static final int` on [R.string], which is one per string resource. */
    private fun stringResourceFields() =
        R.string::class.java.fields.filter {
            Modifier.isStatic(it.modifiers) && it.type == Int::class.javaPrimitiveType
        }

    private fun bannedCharactersIn(value: String) = value.toSet().sorted().mapNotNull { bannedCharacters[it] }

    @Test
    fun `no string resource uses a character the prose-style rule bans`() {
        val context: Context = ApplicationProvider.getApplicationContext()

        val violations =
            stringResourceFields()
                .flatMap { field ->
                    val value = context.getString(field.getInt(null))
                    bannedCharactersIn(value).map { "R.string.${field.name} contains $it: \"$value\"" }
                }.sorted()

        assertEquals(emptyList<String>(), violations)
    }

    /**
     * A scan that finds nothing passes whether the copy is clean or the scan is broken. This pins
     * the two halves the guard above depends on: that reflection plus Robolectric really do hand
     * back this module's resource values, and that a banned character is recognized when present.
     *
     * The reach check names one resource per section of `strings.xml` rather than asserting the
     * field list is merely non-empty, since a filter that regressed to returning a single field
     * would satisfy "non-empty" and leave the guard above passing over almost nothing. It names
     * resources rather than pinning a count, so adding or retiring copy does not break it.
     */
    @Test
    fun `the scan reads real resources and recognizes a banned character`() {
        val context: Context = ApplicationProvider.getApplicationContext()

        val scannedNames = stringResourceFields().map { it.name }.toSet()
        val oneResourcePerSection =
            listOf(
                "app_name",
                "notification_running",
                "setup_media_desc",
                "settings_gallery_not_set",
                "picker_search_hint",
                "advanced_camera_debounce_ms",
                "log_viewer_title",
                "viewer_no_photos",
                "toast_gallery_not_found",
            )
        assertEquals(
            "the scan must reach every section of strings.xml",
            emptyList<String>(),
            oneResourcePerSection.filterNot { it in scannedNames },
        )
        assertEquals("GB4PC", context.getString(R.string.app_name))
        assertEquals(
            listOf("an em dash (use a comma, semicolon, parentheses, or '--')"),
            bannedCharactersIn("Not set \u2014 tap to choose"),
        )
    }
}
