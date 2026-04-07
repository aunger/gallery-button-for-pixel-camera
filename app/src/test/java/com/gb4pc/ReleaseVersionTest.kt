package com.gb4pc

import org.junit.Assert.*
import org.junit.Test
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException

/**
 * Automated release test suite (issue #16).
 *
 * Verifies:
 *  1. versionName follows semver (MAJOR.MINOR.PATCH).
 *  2. versionCode is a valid yyyyMMdd build date.
 *  3. The GitHub tag convention (v{versionName}) is satisfied.
 */
class ReleaseVersionTest {

    @Test
    fun `versionName follows semver MAJOR dot MINOR dot PATCH`() {
        val versionName = BuildConfig.VERSION_NAME
        val semver = Regex("""^\d+\.\d+\.\d+$""")
        assertTrue(
            "versionName '$versionName' must follow semver MAJOR.MINOR.PATCH",
            semver.matches(versionName)
        )
    }

    @Test
    fun `versionCode is a valid yyyyMMdd build date`() {
        val code = BuildConfig.VERSION_CODE
        val str = code.toString()
        assertEquals(
            "versionCode must be exactly 8 digits (yyyyMMdd format), was '$str'",
            8, str.length
        )
        try {
            LocalDate.parse(str, DateTimeFormatter.ofPattern("yyyyMMdd"))
        } catch (e: DateTimeParseException) {
            fail("versionCode '$code' is not a valid yyyyMMdd date: ${e.message}")
        }
    }

    @Test
    fun `versionCode build date is not in the future by more than one day`() {
        val code = BuildConfig.VERSION_CODE
        val buildDate = LocalDate.parse(code.toString(), DateTimeFormatter.ofPattern("yyyyMMdd"))
        val tomorrow = LocalDate.now().plusDays(1)
        assertFalse(
            "versionCode build date '$buildDate' should not be in the future",
            buildDate.isAfter(tomorrow)
        )
    }

    @Test
    fun `github tag v-versionName is a valid tag name`() {
        // Convention: each GitHub release must be tagged v{versionName} (e.g. v0.0.1).
        val versionName = BuildConfig.VERSION_NAME
        val githubTag = "v$versionName"
        val validTag = Regex("""^v\d+\.\d+\.\d+$""")
        assertTrue(
            "GitHub tag '$githubTag' must match pattern v{semver}",
            validTag.matches(githubTag)
        )
    }
}
