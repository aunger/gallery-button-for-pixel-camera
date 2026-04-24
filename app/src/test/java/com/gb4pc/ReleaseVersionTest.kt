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
 *  1. versionName is either semver (MAJOR.MINOR.PATCH) for tagged releases, or
 *     dev.N for CI/pre-release builds (where N is github.run_number).
 *  2. versionCode is either a valid yyyyMMdd build date (local dev builds) or a
 *     positive CI run number (github.run_number in CI builds).
 */
class ReleaseVersionTest {

    @Test
    fun `versionName is semver or dev build label`() {
        val versionName = BuildConfig.VERSION_NAME
        val semver = Regex("""^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$""")
        val devLabel = Regex("""^dev(\.\d+)?$""")
        assertTrue(
            "versionName '$versionName' must be semver (X.Y.Z) or a dev label (dev.N)",
            semver.matches(versionName) || devLabel.matches(versionName)
        )
    }

    @Test
    fun `versionCode is a valid yyyyMMdd date or positive CI run number`() {
        val code = BuildConfig.VERSION_CODE
        val str = code.toString()
        assertTrue("versionCode must be positive, was $code", code > 0)
        if (str.length == 8) {
            // Local dev build: validate as yyyyMMdd within sane range.
            val buildDate = try {
                LocalDate.parse(str, DateTimeFormatter.ofPattern("yyyyMMdd"))
            } catch (e: DateTimeParseException) {
                fail("8-digit versionCode '$code' is not a valid yyyyMMdd date: ${e.message}")
                return
            }
            val projectEpoch = LocalDate.of(2024, 1, 1)
            val tomorrow = LocalDate.now(ZoneOffset.UTC).plusDays(1)
            assertTrue(
                "versionCode date '$buildDate' is before project epoch ($projectEpoch)",
                !buildDate.isBefore(projectEpoch)
            )
            assertFalse(
                "versionCode date '$buildDate' should not be in the future",
                buildDate.isAfter(tomorrow)
            )
        }
        // else: CI build — github.run_number is any positive integer, already checked above.
    }
}
