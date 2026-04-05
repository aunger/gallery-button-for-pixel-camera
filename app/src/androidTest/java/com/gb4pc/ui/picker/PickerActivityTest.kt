package com.gb4pc.ui.picker

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
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
        composeRule.onNodeWithText("Search apps\u2026").assertIsDisplayed()
    }

    @Test
    fun pickerScreen_loadsApps_andShowsSettingsApp() {
        // "Settings" is present on every Android device/emulator.
        // Waiting up to 10 s covers the async IO query introduced in issue #8.
        composeRule.waitUntil(timeoutMillis = 10_000) {
            composeRule
                .onAllNodes(androidx.compose.ui.test.hasText("Settings"))
                .fetchSemanticsNodes()
                .isNotEmpty()
        }
        composeRule.onNodeWithText("Settings").assertIsDisplayed()
    }
}
