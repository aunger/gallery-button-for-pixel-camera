package com.gb4pc.data

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.gb4pc.Constants
import com.gb4pc.R
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.xmlpull.v1.XmlPullParser

/**
 * Checks the backup rules against the prefs file names in [Constants] (issue #1235).
 *
 * The rules name the per-install file by a literal path, so nothing but this test notices if
 * [Constants.INSTALL_PREFS_NAME] is renamed, or if one of the two rule files drifts from the other.
 * A rule that no longer matches the file excludes nothing, and the setup, permission-dialog and
 * service flags would then follow the user to a phone where none of that is true.
 */
@RunWith(RobolectricTestRunner::class)
class BackupRulesRobolectricTest {
    private val context: Context = ApplicationProvider.getApplicationContext()

    private data class Rule(
        val section: String,
        val tag: String,
        val domain: String?,
        val path: String?,
    )

    /** Every `<include>` and `<exclude>` in [resId], tagged with the section it sits in. */
    private fun rules(resId: Int): List<Rule> {
        val parser = context.resources.getXml(resId)
        val rules = mutableListOf<Rule>()
        var section = ""
        while (parser.next() != XmlPullParser.END_DOCUMENT) {
            if (parser.eventType != XmlPullParser.START_TAG) continue
            when (parser.name) {
                "include", "exclude" -> {
                    rules +=
                        Rule(
                            section = section,
                            tag = parser.name,
                            domain = parser.getAttributeValue(null, "domain"),
                            path = parser.getAttributeValue(null, "path"),
                        )
                }

                else -> {
                    section = parser.name
                }
            }
        }
        return rules
    }

    private fun excludeInstallPrefs(section: String) = Rule(section, "exclude", "sharedpref", Constants.INSTALL_PREFS_NAME + ".xml")

    @Test
    fun `data extraction rules exclude only the per-install file from cloud backup and device transfer`() {
        assertEquals(
            listOf(excludeInstallPrefs("cloud-backup"), excludeInstallPrefs("device-transfer")),
            rules(R.xml.data_extraction_rules),
        )
    }

    @Test
    fun `full backup content excludes only the per-install file on API 30 and below`() {
        assertEquals(
            listOf(excludeInstallPrefs("full-backup-content")),
            rules(R.xml.backup_rules),
        )
    }
}
