package com.gb4pc

import org.junit.Assert.*
import org.junit.Test
import java.time.LocalDate
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException

/**
 * Automated release test suite (issue #16).
 *
 * These tests read [BuildConfig] constants that are baked in at compile time — they
 * validate the build that produced this APK, not future builds.
 *
 * Verifies:
 *  1. versionName follows semver (MAJOR.MINOR.PATCH, no leading zeros).
 *  2. versionCode is a valid yyyyMMdd build date (UTC) within a sane range.
 */
class ReleaseVersionTest {

    @Test
    fun `versionName follows semver MAJOR dot MINOR dot PATCH without leading zeros`() {
        val versionName = BuildConfig.VERSION_NAME
        // Strict semver: no leading zeros per spec item 2.
        val semver = Regex("""^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$""")
        assertTrue(
            "versionName '$versionName' must follow semver MAJOR.MINOR.PATCH (no leading zeros)",
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
    fun `versionCode build date is within sane bounds`() {
        val code = BuildConfig.VERSION_CODE
        val buildDate = LocalDate.parse(code.toString(), DateTimeFormatter.ofPattern("yyyyMMdd"))
        val projectEpoch = LocalDate.of(2024, 1, 1)
        val tomorrow = LocalDate.now(ZoneOffset.UTC).plusDays(1)
        assertTrue(
            "versionCode build date '$buildDate' is before project epoch ($projectEpoch)",
            !buildDate.isBefore(projectEpoch)
        )
        assertFalse(
            "versionCode build date '$buildDate' should not be in the future",
            buildDate.isAfter(tomorrow)
        )
    }
}
