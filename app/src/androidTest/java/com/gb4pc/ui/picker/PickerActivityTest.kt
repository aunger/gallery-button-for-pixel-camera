package com.gb4pc.ui.picker

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onChildren
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

private const val FILTERED_HINT = "Showing photo-related apps"
private const val SHOW_ALL = "Show all apps"

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
        val appList = composeRule.onNodeWithTag(PICKER_APP_LIST_TEST_TAG)

        // The picker opens on its photo-related filter, which "Settings" never matches, so
        // switch to the full list of UI-05. Scroll to the button rather than expect it on
        // screen: it is the list's last item, below every photo-related row. The picker
        // makes the switch itself when it finds no photo-related app, and then shows no
        // button; the hint is the list's first item, so it is composed whenever the filter
        // is still on, whatever the button is doing.
        if (composeRule.onAllNodes(hasText(FILTERED_HINT)).fetchSemanticsNodes().isNotEmpty()) {
            appList.performScrollToNode(hasText(SHOW_ALL))
            composeRule.onNodeWithText(SHOW_ALL).performClick()
        }
        // Fail here, rather than as a timeout further down, if the picker is still filtered.
        composeRule.onNodeWithText(FILTERED_HINT).assertDoesNotExist()

        // Wait for the async app-list query introduced in issue #8. The unfiltered list has
        // no rows at all until that query lands, and composes its first row as soon as it
        // does, wherever the list happens to be scrolled.
        composeRule.waitUntil(timeoutMillis = 10_000) {
            appList.onChildren().fetchSemanticsNodes().isNotEmpty()
        }

        // "Settings" is installed on every Android device and emulator, and sorts far
        // enough down the alphabetical list of UI-05 to start below the fold. A LazyColumn
        // composes only the rows on screen, so scroll the list to that row: waiting alone
        // never composes it, however long the timeout.
        appList.performScrollToNode(hasText("Settings"))
        composeRule.onNodeWithText("Settings").assertIsDisplayed()
    }
}
