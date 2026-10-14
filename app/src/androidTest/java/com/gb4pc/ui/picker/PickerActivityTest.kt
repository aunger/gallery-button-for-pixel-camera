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
import androidx.test.platform.app.InstrumentationRegistry
import com.gb4pc.R
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

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val filteredHint = context.getString(R.string.picker_filtered_hint)
    private val showAll = context.getString(R.string.picker_show_all)

    @Test
    fun pickerScreen_showsTitle() {
        composeRule.onNodeWithText(context.getString(R.string.picker_title)).assertIsDisplayed()
    }

    @Test
    fun pickerScreen_showsSearchBar() {
        composeRule.onNodeWithText(context.getString(R.string.picker_search_hint)).assertIsDisplayed()
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
        if (composeRule.onAllNodes(hasText(filteredHint)).fetchSemanticsNodes().isNotEmpty()) {
            appList.performScrollToNode(hasText(showAll))
            composeRule.onNodeWithText(showAll).performClick()
        }
        // Fail here, rather than as a timeout further down, if the picker is still filtered.
        composeRule.onNodeWithText(filteredHint).assertDoesNotExist()

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
