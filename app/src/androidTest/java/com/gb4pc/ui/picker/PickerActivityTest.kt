package com.gb4pc.ui.picker

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Instrumented tests for PickerActivity.
 * Verifies that the gallery-app picker screen renders correctly and that its
 * asynchronous app-list query completes within a reasonable timeout.
 */
@RunWith(AndroidJUnit4::class)
class PickerActivityTest {
    @get:Rule
    val composeRule = createAndroidComposeRule<PickerActivity>()

    @Test
    fun pickerScreen_showsTitle() {
        composeRule.onNodeWithText("Choose Gallery App").assertIsDisplayed()
    }

    @Test
    fun pickerScreen_showsSearchBar() {
        composeRule.onNodeWithText("Search apps...").assertIsDisplayed()
    }

    @Test
    fun pickerScreen_loadsApps_andShowsSettingsApp() {
        // The picker defaults to a "photo-related apps" filter (UI-09), which "Settings"
        // never matches. Wait for the async app-list query introduced in issue #8 to
        // finish, then switch to the full app list (always present on every Android
        // device/emulator) so "Settings" is shown.
        composeRule.waitUntil(timeoutMillis = 10_000) {
            composeRule
                .onAllNodes(hasText("Settings") or hasText("Show all apps"))
                .fetchSemanticsNodes()
                .isNotEmpty()
        }

        val showAllButton = composeRule.onAllNodes(hasText("Show all apps")).fetchSemanticsNodes()
        if (showAllButton.isNotEmpty()) {
            composeRule.onNodeWithText("Show all apps").performClick()
            composeRule.waitUntil(timeoutMillis = 10_000) {
                composeRule
                    .onAllNodes(hasText("Settings"))
                    .fetchSemanticsNodes()
                    .isNotEmpty()
            }
        }

        composeRule.onNodeWithText("Settings").assertIsDisplayed()
    }
}
