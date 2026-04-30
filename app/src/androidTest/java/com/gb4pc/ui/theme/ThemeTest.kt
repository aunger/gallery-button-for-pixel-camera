package com.gb4pc.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Verifies that GB4PCTheme forwards the correct color scheme based on [darkTheme].
 */
@RunWith(AndroidJUnit4::class)
class ThemeTest {

    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun gb4pcTheme_rendersContent() {
        composeRule.setContent {
            GB4PCTheme {
                Text("hello")
            }
        }
        composeRule.onNodeWithText("hello").assertIsDisplayed()
    }

    @Test
    fun gb4pcTheme_lightMode_usesLightColorScheme() {
        var background = androidx.compose.ui.graphics.Color.Unspecified
        composeRule.setContent {
            GB4PCTheme(darkTheme = false) {
                background = MaterialTheme.colorScheme.background
            }
        }
        assertEquals(lightColorScheme().background, background)
    }

    @Test
    fun gb4pcTheme_darkMode_usesDarkColorScheme() {
        var background = androidx.compose.ui.graphics.Color.Unspecified
        composeRule.setContent {
            GB4PCTheme(darkTheme = true) {
                background = MaterialTheme.colorScheme.background
            }
        }
        assertEquals(darkColorScheme().background, background)
    }
}
