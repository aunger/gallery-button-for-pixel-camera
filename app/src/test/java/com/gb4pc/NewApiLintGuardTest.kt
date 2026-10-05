package com.gb4pc

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Element
import java.io.File
import javax.xml.parsers.DocumentBuilderFactory
import kotlin.io.path.createTempDirectory

/**
 * Regression guard for issue #1239.
 *
 * Android Lint's NewApi check is the one guard against a call to an API above `minSdk`: the
 * compiler accepts any symbol up to `compileSdk`, and the E2E emulator runs a single API level.
 * The `lint { }` block in `app/build.gradle.kts` fails `:app:lintDebug` on any finding, but a
 * later change could switch NewApi off, or absorb its findings into a baseline, and the task
 * would still pass. This test fails instead.
 *
 * It does not read `app/build.gradle.kts` as text. The `unitTests.all { }` block there passes in
 * the settings as Gradle resolved them, as `gb4pc.lint.*` system properties, so every way of
 * writing the DSL lands in the same values. A property that is missing fails the test, so removing
 * that wiring cannot quietly turn the guard off.
 *
 * NewApi counts as switched off when any of these holds:
 * - it is in `disable` or `ignore` (in AGP 9.1 `ignore` returns the `disable` set, so one entry
 *   reports on both lines);
 * - it is in `informational`, which reports it without failing the task;
 * - `checkOnly` is set and leaves it out;
 * - a `baseline` file is configured, whatever it holds today;
 * - a `lint.xml` names it, or names `all`, in an `<issue>` element. That is the `lintConfig` file,
 *   a `lint.xml` at the repository root, or a `lint.xml` anywhere in the module outside its
 *   `build` output: for each source file, Lint reads the nearest `lint.xml` in the folders between
 *   it and the root, so one in `src/main` covers every file beneath it.
 *
 * The last rule is stricter than Lint: a `lint.xml` could name NewApi only to keep it an error.
 * Telling those apart means modelling Lint's severity and path rules, and NewApi is an error by
 * default, so a `lint.xml` has no reason to name it.
 *
 * A call-site `@SuppressLint("NewApi")` or `tools:ignore="NewApi"` is not checked. The `lint { }`
 * block asks for a check that does not apply to be suppressed where it fires, with the reason,
 * and that suppression is read at the site in review; this test covers the module-wide switches,
 * which silence every call at once.
 */
class NewApiLintGuardTest {
    /** The resolved `lint { }` settings that decide whether NewApi runs and fails the task. */
    data class LintSettings(
        val disable: List<String>,
        val ignore: List<String>,
        val informational: List<String>,
        val checkOnly: List<String>,
        val baseline: List<String>,
        val configFiles: List<File>,
    )

    /** Every `lint.xml` under [moduleDir], except in its `build` output, which Lint does not analyze. */
    private fun lintXmlFilesIn(moduleDir: File): List<File> {
        val buildDir = File(moduleDir, "build")
        return moduleDir
            .walkTopDown()
            .onEnter { it != buildDir }
            .filter { it.isFile && it.name == "lint.xml" }
            .toList()
    }

    private fun names(ids: Collection<String>) = ids.flatMap { it.split(',') }.map { it.trim() }

    private fun namesNewApi(ids: Collection<String>) = names(ids).any { it.equals(NEW_API, ignoreCase = true) }

    /** The `<issue>` ids in [config] that name NewApi or `all`. */
    private fun newApiIssueIdsIn(config: File): List<String> {
        val document = DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(config)
        val issues = document.getElementsByTagName("issue")
        return (0 until issues.length)
            .map { (issues.item(it) as Element).getAttribute("id") }
            .filter { id -> names(listOf(id)).any { it.equals(NEW_API, true) || it.equals("all", true) } }
    }

    /** Every way [settings] switch NewApi off, one line each; empty when NewApi runs and fails the task. */
    private fun violations(settings: LintSettings): List<String> {
        val found = mutableListOf<String>()
        if (namesNewApi(settings.disable)) found += "lint.disable includes NewApi"
        if (namesNewApi(settings.ignore)) found += "lint.ignore includes NewApi"
        if (namesNewApi(settings.informational)) found += "lint.informational includes NewApi"
        if (settings.checkOnly.isNotEmpty() && !namesNewApi(settings.checkOnly)) {
            found += "lint.checkOnly is set and leaves out NewApi: ${settings.checkOnly}"
        }
        settings.baseline.forEach { found += "lint.baseline is configured: $it" }
        settings.configFiles.filter { it.isFile }.forEach { config ->
            newApiIssueIdsIn(config).forEach { found += "$config has an <issue id=\"$it\"> element" }
        }
        return found
    }

    private fun property(name: String): List<String> {
        val value =
            System.getProperty("gb4pc.lint.$name")
                ?: throw AssertionError(
                    "System property gb4pc.lint.$name is not set. app/build.gradle.kts passes the lint { } " +
                        "settings to unit tests in testOptions.unitTests.all { }; run this test through Gradle.",
                )
        return value.lines().filter { it.isNotBlank() }
    }

    private fun moduleDir() = File(property("moduleDir").single())

    private fun resolvedSettings() =
        LintSettings(
            disable = property("disable"),
            ignore = property("ignore"),
            informational = property("informational"),
            checkOnly = property("checkOnly"),
            baseline = property("baseline"),
            configFiles = property("configFiles").map(::File) + lintXmlFilesIn(moduleDir()),
        )

    @Test
    fun `the app module's Lint configuration leaves NewApi on and unbaselined`() {
        assertEquals(emptyList<String>(), violations(resolvedSettings()))
    }

    /**
     * A guard that finds nothing passes whether the configuration is clean or the guard is broken.
     * This pins both halves: that the settings really arrive from Gradle, and that each route
     * above is recognized when taken.
     */
    @Test
    fun `the guard receives the real settings and recognizes each way of switching NewApi off`() {
        val resolved = resolvedSettings()
        // The lint { } block disables other checks, so an empty set here means the wiring broke.
        assertTrue("lint.disable arrived empty: $resolved", resolved.disable.isNotEmpty())
        val moduleDir = moduleDir()
        assertTrue("moduleDir is not this module: $moduleDir", File(moduleDir, "src/main/AndroidManifest.xml").isFile)

        val clean = LintSettings(listOf("GradleDependency"), emptyList(), emptyList(), emptyList(), emptyList(), emptyList())
        assertEquals(emptyList<String>(), violations(clean))
        assertEquals(emptyList<String>(), violations(clean.copy(checkOnly = listOf("NewApi", "InlinedApi"))))

        // A module tree with a nested lint.xml, which counts, and one in build output, which does not.
        val tree = createTempDirectory("module").toFile()
        val config = File(tree, "src/main/java/lint.xml").apply { parentFile.mkdirs() }
        try {
            config.writeText("<lint />")
            File(tree, "build/intermediates/lint.xml").apply { parentFile.mkdirs() }.writeText("<lint />")
            assertEquals(listOf(config), lintXmlFilesIn(tree))

            config.writeText("""<lint><issue id="InlinedApi,NewApi" severity="ignore" /></lint>""")
            val switchedOff =
                mapOf(
                    "disable" to clean.copy(disable = listOf("GradleDependency", "NewApi")),
                    "ignore" to clean.copy(ignore = listOf("NewApi")),
                    "informational" to clean.copy(informational = listOf("newapi")),
                    "checkOnly" to clean.copy(checkOnly = listOf("InlinedApi")),
                    "baseline" to clean.copy(baseline = listOf("lint-baseline.xml")),
                    "lint.xml" to clean.copy(configFiles = listOf(config)),
                )
            switchedOff.forEach { (route, settings) ->
                assertEquals("violations for $route", 1, violations(settings).size)
            }

            config.writeText("""<lint><issue id="all" severity="ignore" /></lint>""")
            assertEquals(1, violations(clean.copy(configFiles = listOf(config))).size)

            config.writeText("""<lint><issue id="InlinedApi" severity="ignore" /></lint>""")
            assertEquals(emptyList<String>(), violations(clean.copy(configFiles = listOf(config))))
        } finally {
            tree.deleteRecursively()
        }
    }

    private companion object {
        const val NEW_API = "NewApi"
    }
}
