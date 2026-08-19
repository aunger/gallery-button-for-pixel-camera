import org.gradle.process.ExecOperations
import java.io.ByteArrayOutputStream
import java.io.File
import java.time.LocalDate
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.concurrent.TimeUnit
import javax.inject.Inject

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

// Versioning: git tag is the single source of truth for versionName.
// To release: run scripts/tag-release.sh <version> (e.g. 1.2.3).
// CI extracts the version from the tag and injects it via -PversionName=X.Y.Z.
// Dev builds (no property) show "dev". versionCode uses yyyyMMdd date locally or
// github.run_number (via BUILD_NUMBER env var) in CI — monotonically increasing, no collisions.
// BUILD_NUMBER must be a valid integer; a malformed value fails the build loudly.
val envBuildNumber: String? = System.getenv("BUILD_NUMBER")
val buildNumber: Int =
    when {
        envBuildNumber == null -> {
            LocalDate.now(ZoneOffset.UTC).format(DateTimeFormatter.BASIC_ISO_DATE).toInt()
        }

        envBuildNumber.toIntOrNull() != null -> {
            envBuildNumber.toInt()
        }

        else -> {
            error("BUILD_NUMBER env var is set but not a valid integer: '$envBuildNumber'")
        }
    }

// ── Test-result marker helpers ────────────────────────────────────────────────
// Shared by the unit-test listener (tasks.withType<Test>) and the E2E parser.
// Produces the stable ##GB4PC_TEST## line format consumed by the CI Monitor.

/** JSON-escapes a string value (no surrounding quotes). */
fun jsonEscape(s: String): String =
    s
        .replace("\\", "\\\\")
        .replace("\"", "\\\"")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")

/** Returns the first ≤10 stack frames of [ex] as a single newline-separated string. */
fun buildTrace(ex: Throwable): String {
    val frames = ex.stackTrace.take(10).joinToString("\n") { "\tat $it" }
    val header = ex.toString()
    return if (frames.isEmpty()) header else "$header\n$frames"
}

android {
    namespace = "com.gb4pc"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.gb4pc"
        minSdk = 26
        targetSdk = 35
        versionCode = buildNumber
        versionName = findProperty("versionName") as String? ?: "dev"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        // Exclude E2E tests from the instrumented-test run.
        // E2E tests live in com.gb4pc.e2e and require a device with Pixel Camera installed.
        // Run them separately with: ./gradlew connectedE2EAndroidTest
        testInstrumentationRunnerArguments["notPackage"] = "com.gb4pc.e2e"
    }

    // M6: Conditionally configure release signing from environment variables.
    // CI sets KEYSTORE_PATH/KEYSTORE_PASSWORD/KEY_ALIAS/KEY_PASSWORD to sign with a real keystore.
    // Locally (no env vars) the release APK will be unsigned — never uses the debug keystore.
    val keystorePath = System.getenv("KEYSTORE_PATH")
    if (keystorePath != null) {
        signingConfigs {
            create("release") {
                storeFile = file(keystorePath)
                storePassword = System.getenv("KEYSTORE_PASSWORD")
                keyAlias = System.getenv("KEY_ALIAS")
                keyPassword = System.getenv("KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            // M7: Enable minification and resource shrinking for release builds.
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            // M6: Only apply release signing config when keystore env vars are present.
            if (keystorePath != null) {
                signingConfig = signingConfigs.getByName("release")
            }
            // else signingConfig stays null → unsigned release build locally
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    // Shared pure-JVM test helpers compiled into both test and androidTest source sets.
    // AGP testFixtures does not support Kotlin on application modules (b/139438662).
    // `directories` arrives prepopulated with the source set's defaults (src/<name>/java
    // and src/<name>/kotlin) and is mutated in place, so `+=` adds to those defaults
    // rather than replacing them.
    sourceSets {
        getByName("test") {
            kotlin.directories += "src/sharedTest/java"
        }
        getByName("androidTest") {
            kotlin.directories += "src/sharedTest/java"
        }
    }

    testOptions {
        unitTests {
            isIncludeAndroidResources = true
            isReturnDefaultValues = true
            all { testTask ->
                testTask.addTestListener(
                    object : TestListener {
                        override fun beforeSuite(suite: TestDescriptor) {}

                        override fun afterSuite(
                            suite: TestDescriptor,
                            result: TestResult,
                        ) {}

                        override fun beforeTest(test: TestDescriptor) {}

                        override fun afterTest(
                            test: TestDescriptor,
                            result: TestResult,
                        ) {
                            val outcome =
                                when (result.resultType) {
                                    TestResult.ResultType.SUCCESS -> "PASS"
                                    TestResult.ResultType.FAILURE -> "FAIL"
                                    TestResult.ResultType.SKIPPED -> "SKIP"
                                }
                            val suite = test.className ?: ""
                            val name = test.name
                            val ms = result.endTime - result.startTime
                            val ex = result.exception
                            val msg = if (ex != null) jsonEscape(ex.message ?: ex.javaClass.name) else ""
                            val trace = if (ex != null) jsonEscape(buildTrace(ex)) else ""
                            println(
                                """##GB4PC_TEST## {"suite":"${jsonEscape(
                                    suite,
                                )}","name":"${jsonEscape(name)}","outcome":"$outcome","ms":$ms,"msg":"$msg","trace":"$trace"}""",
                            )
                        }
                    },
                )
            }
        }
    }
}

dependencies {
    // AndroidX Core
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.lifecycle:lifecycle-service:2.8.7")
    implementation("androidx.activity:activity-compose:1.9.3")

    // Compose
    implementation(platform("androidx.compose:compose-bom:2026.08.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")

    // Material Components (Snackbar for secure viewer)
    implementation("com.google.android.material:material:1.12.0")

    // ViewPager2 for secure filmstrip viewer
    implementation("androidx.viewpager2:viewpager2:1.1.0")

    // Subsampling Scale Image View for pinch-to-zoom
    implementation("com.davemorrissey.labs:subsampling-scale-image-view-androidx:3.10.0")

    // Unit testing
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.mockito:mockito-core:5.23.0")
    testImplementation("org.mockito.kotlin:mockito-kotlin:6.3.0")
    testImplementation("org.robolectric:robolectric:4.16.1")
    testImplementation("androidx.test:core:1.7.0")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.11.0")
    testImplementation("org.json:json:20260814")

    // Android instrumented testing
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.7.0")
    androidTestImplementation("androidx.test.espresso:espresso-intents:3.7.0")
    androidTestImplementation("androidx.test.uiautomator:uiautomator:2.4.0")
    androidTestImplementation(platform("androidx.compose:compose-bom:2026.08.00"))
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")

    // Debug
    debugImplementation("androidx.compose.ui:ui-tooling")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}

// ── E2E test task ────────────────────────────────────────────────────────────
// Builds the APKs, installs them on the connected device/emulator, and runs only
// the com.gb4pc.e2e package (the standard connectedDebugAndroidTest excludes it).
// Usage: ./gradlew connectedE2EAndroidTest
//
// Note: captures the SDK-dir provider, APK paths and -P properties at configuration time so they
// are available inside the doLast execution closure where the project extension is out of scope.
// The adb path is a Provider rather than a plain String: reading android.sdkDirectory eagerly at
// configuration time is gone in AGP 9, so the location is resolved lazily and dereferenced with
// .get() at each use inside doLast.
val e2eAdb =
    androidComponents.sdkComponents.sdkDirectory
        .map { it.file("platform-tools/adb").asFile.absolutePath }
val e2eAppApk =
    layout.buildDirectory
        .file("outputs/apk/debug/app-debug.apk")
val e2eTestApk =
    layout.buildDirectory
        .file("outputs/apk/androidTest/debug/app-debug-androidTest.apk")
val e2eMockCameraApk =
    project(":e2e-mock-camera")
        .layout.buildDirectory
        .file("outputs/apk/debug/e2e-mock-camera-debug.apk")
val e2eMockGalleryApk =
    project(":e2e-mock-gallery")
        .layout.buildDirectory
        .file("outputs/apk/debug/e2e-mock-gallery-debug.apk")
val e2eXmlDir =
    layout.buildDirectory
        .dir("outputs/androidTest-results/connected/debug")
val e2eGrantMediaPermission = (findProperty("mediaPermissionGranted") as String? ?: "true").toBoolean()
val e2eClass = findProperty("e2eClass") as String?

/**
 * Carrier for an injected [ExecOperations], which is how the task below runs adb: the service has
 * no public constructor and no accessor of its own, so the only way to obtain one is an `@Inject`
 * getter that `ObjectFactory.newInstance` implements.
 */
interface E2EExecOps {
    @get:Inject
    val execOps: ExecOperations
}

val e2eExecOps = objects.newInstance<E2EExecOps>().execOps

/**
 * Runs [command] via a plain ProcessBuilder, bounded by [timeoutSeconds]: `Process.waitFor(timeout,
 * unit)` + `destroyForcibly()` on expiry, the same pattern `e2eInstrumentTimeoutMinutes` uses below
 * for `am instrument` -- not the `timeout` coreutil `build.yml` uses for its own adb waits, to keep
 * this task portable to local dev on any OS (see that call site's own rationale).
 *
 * Returns combined stdout/stderr, or "" on any failure (non-zero exit, thrown exception, or the
 * timeout itself, which is logged as a warning rather than thrown). Callers use this only for
 * best-effort diagnostic capture (issue #629): a command that hangs or errors here must never
 * block the build or mask the failure that triggered the capture in the first place -- exactly the
 * class of hang this whole task's timeout guard exists to eliminate.
 */
fun runBestEffort(
    command: List<String>,
    timeoutSeconds: Long,
): String =
    try {
        val process = ProcessBuilder(command).redirectErrorStream(true).start()
        val out = ByteArrayOutputStream()
        val drainThread =
            Thread {
                process.inputStream.copyTo(out)
            }.apply {
                isDaemon = true
                start()
            }
        if (!process.waitFor(timeoutSeconds, TimeUnit.SECONDS)) {
            process.destroyForcibly()
            println("WARNING: '${command.joinToString(" ")}' did not finish within ${timeoutSeconds}s; killed")
        }
        drainThread.join(TimeUnit.SECONDS.toMillis(5))
        out.toString()
    } catch (e: Exception) {
        println("WARNING: '${command.joinToString(" ")}' failed: ${e.message}")
        ""
    }

tasks.register("connectedE2EAndroidTest") {
    group = "verification"
    description = "Runs E2E instrumented tests (requires device/emulator with Pixel Camera installed)."
    dependsOn("assembleDebug", "assembleDebugAndroidTest", ":e2e-mock-camera:assembleDebug", ":e2e-mock-gallery:assembleDebug")
    doLast {
        // Install app first so permissions can be granted by package name.
        e2eExecOps.exec { commandLine(e2eAdb.get(), "install", "-r", e2eAppApk.get().asFile.absolutePath) }
        // Grant permissions now that the app UID exists on the device.
        e2eExecOps.exec { commandLine(e2eAdb.get(), "shell", "appops", "set", "com.gb4pc", "SYSTEM_ALERT_WINDOW", "allow") }
        // GET_USAGE_STATS (= PACKAGE_USAGE_STATS on API 29+) lets ForegroundDetector see
        // which app is in the foreground — without this the overlay never appears.
        e2eExecOps.exec { commandLine(e2eAdb.get(), "shell", "appops", "set", "com.gb4pc", "GET_USAGE_STATS", "allow") }
        // POST_NOTIFICATIONS (API 33+ runtime permission, declared in the main manifest) lets
        // OverlayService's postPermissionNotification() actually post anything. Without this
        // grant, NotificationManager.notify() is a silent no-op on API 33+ (no exception, no
        // crash, the call simply does nothing), so PermissionsDeniedE2ETest's assertion that
        // NOTIFICATION_MEDIA_PERMISSION_ID becomes active would otherwise time out even though
        // the production code posting it is correct (issue #509 follow-up, PR #564 review).
        // `pm grant` (not `appops set`) for the same reason as READ_MEDIA_IMAGES below: this is a
        // dangerous/runtime permission, not an appops-gated special permission.
        e2eExecOps.exec { commandLine(e2eAdb.get(), "shell", "pm", "grant", "com.gb4pc", "android.permission.POST_NOTIFICATIONS") }
        // READ_MEDIA_IMAGES lets E2EFixture see MediaStore rows inserted by other packages
        // (e2e-mock-camera). `am instrument` runs the instrumented test code inside this
        // app's process and UID (com.gb4pc), not com.gb4pc.test's, so the grant must target
        // com.gb4pc; the permission is declared in app/src/main/AndroidManifest.xml (it also
        // backs the runtime grant flow added for issue #509, so debug builds no longer need
        // their own copy of the declaration).
        // Without this, E2EFixture.captureOnePhoto()'s countMediaStoreImages() query only
        // returns rows owned by com.gb4pc itself (scoped storage, API 29+), so it never
        // observes the photo the mock camera wrote and times out (issues #231/#232).
        //
        // READ_MEDIA_IMAGES is a normal dangerous/runtime permission, not an appops-gated
        // special permission like SYSTEM_ALERT_WINDOW or GET_USAGE_STATS above. `appops set
        // ... allow` only adjusts the AppOps mode; it does not flip the PackageManager-level
        // grant state that ContentResolver's permission check (PermissionChecker) consults,
        // so the prior `appops set com.gb4pc READ_MEDIA_IMAGES allow` was a no-op and CI still
        // timed out. `pm grant` is the mechanism already used for CAMERA below and in
        // build.yml; it grants the manifest-declared permission outright.
        //
        // -PmediaPermissionGranted=false flips this to `pm revoke` instead, for
        // PermissionsDeniedE2ETest (issue #509 follow-up, PR #564 review). This MUST happen here,
        // before `am instrument` starts the com.gb4pc process below, never from within a running
        // test: changing a storage-group runtime permission on an already-running process makes
        // Android kill that process to re-establish its scoped-storage FUSE mount, which took down
        // an earlier version of this suite that called `pm grant`/`pm revoke` mid-test (see
        // PermissionsDeniedE2ETest's class doc for the full incident).
        e2eExecOps.exec {
            commandLine(
                e2eAdb.get(),
                "shell",
                "pm",
                if (e2eGrantMediaPermission) "grant" else "revoke",
                "com.gb4pc",
                "android.permission.READ_MEDIA_IMAGES",
            )
        }
        // Install mock Pixel Camera so CameraManager callbacks and UsageStats detection are exercised.
        // CI also installs this APK explicitly before invoking the task (see build.yml) because
        // relying solely on this doLast install caused test failures in CI; kept here for local runs.
        e2eExecOps.exec { commandLine(e2eAdb.get(), "install", "-r", e2eMockCameraApk.get().asFile.absolutePath) }
        e2eExecOps.exec {
            commandLine(
                e2eAdb.get(),
                "shell",
                "pm",
                "grant",
                "com.google.android.GoogleCamera",
                "android.permission.CAMERA",
            )
        }
        // Install mock gallery so tapOverlay() can navigate to it in visual E2E tests.
        e2eExecOps.exec { commandLine(e2eAdb.get(), "install", "-r", e2eMockGalleryApk.get().asFile.absolutePath) }
        // READ_MEDIA_IMAGES lets mock gallery query MediaStore for the last captured photo.
        // Use `pm grant` (not `appops set`) for the same reason as the com.gb4pc grant above:
        // READ_MEDIA_IMAGES is a dangerous runtime permission, and `appops set ... allow` does
        // not flip its PackageManager-level grant state.
        e2eExecOps.exec {
            commandLine(
                e2eAdb.get(),
                "shell",
                "pm",
                "grant",
                "com.gb4pc.mockgallery",
                "android.permission.READ_MEDIA_IMAGES",
            )
        }
        e2eExecOps.exec { commandLine(e2eAdb.get(), "install", "-r", e2eTestApk.get().asFile.absolutePath) }
        // Run E2E tests with -r for machine-parseable per-test status lines.
        // am instrument exits non-zero on test failure but returns 0 on process crash;
        // capture stdout, write JUnit XML, then fail loudly on crash or test failure.
        val classArgs =
            if (e2eClass != null) {
                listOf("-e", "class", e2eClass)
            } else {
                listOf("-e", "package", "com.gb4pc.e2e")
            }
        val xmlSuiteName = e2eClass ?: "com.gb4pc.e2e"

        val instrumentOut = ByteArrayOutputStream()
        // Issue #611: `am instrument -w` blocks until the on-device instrumentation reports
        // completion, and has no timeout of its own. A prior CI run's "Run SetupActivityDeniedE2ETest"
        // step went completely silent right after this exec started (no INSTRUMENTATION_STATUS
        // output at all) for over three hours before a human had to cancel it; a separate run on
        // main hit GitHub's own 360-minute (6-hour) job default and was cancelled automatically.
        // Both burned CI compute and blocked the pipeline for hours with no automatic recovery.
        // Run `am instrument` via a plain ProcessBuilder (rather than Gradle's `exec {}`, which has
        // no built-in timeout either) and bound it ourselves with Process.waitFor(timeout, unit) +
        // destroyForcibly(), so a hung instrumentation run fails this task quickly instead of
        // hanging indefinitely. A JVM-level timeout (vs. shelling out to the `timeout` coreutil, as
        // build.yml already does for the emulator-readiness wait) keeps this task portable to
        // developers running `./gradlew connectedE2EAndroidTest` locally on any OS.
        val e2eInstrumentTimeoutMinutes = 10L
        val instrumentProcess =
            ProcessBuilder(
                e2eAdb.get(),
                "shell",
                "am",
                "instrument",
                "-r",
                "-w",
                *classArgs.toTypedArray(),
                "com.gb4pc.test/androidx.test.runner.AndroidJUnitRunner",
            ).redirectErrorStream(true)
                .start()
        // Drain stdout on a separate thread while we wait: the pipe's OS buffer is bounded, and a
        // verbose test run could otherwise deadlock the child process (blocked writing) against
        // this task (not yet reading) before the timeout below is ever reached.
        val drainThread =
            Thread {
                instrumentProcess.inputStream.copyTo(instrumentOut)
            }.apply {
                isDaemon = true
                start()
            }
        val finishedInTime = instrumentProcess.waitFor(e2eInstrumentTimeoutMinutes, TimeUnit.MINUTES)
        val timedOut = !finishedInTime
        if (timedOut) {
            // Issue #629: this class's own hang history has already shown two different
            // symptoms (a keyguard-driven RESUMED/PAUSED flicker, then later a launch that
            // never produced any further system activity at all), and the second recurrence
            // left no real clue to work from: scripts/ci/test-support/filter_logcat.sh's tag allowlist may
            // have dropped lines relevant to a stuck launch, and nothing captured what was
            // actually on screen. Grab both BEFORE the force-stop below tears the activity
            // down, so the device state reflects the hang itself rather than a freshly
            // cleaned slate. Best-effort: a device unresponsive enough to hang a whole suite
            // might not answer these either, so each capture is bounded via runBestEffort()
            // (PR #651 review): an unbounded `exec {}` here could itself hang against the exact
            // unresponsive-device state this issue documents, reintroducing the multi-hour block
            // issue #611/#628 already eliminated for `am instrument`, just three calls later.
            val diagnosticsDir =
                layout.buildDirectory
                    .dir("outputs/e2e-diagnostics")
                    .get()
                    .asFile
                    .also { it.mkdirs() }
            val diagnosticsTimeoutSeconds = 30L
            val onDeviceDumpPath = "/sdcard/gb4pc-e2e-timeout-uidump.xml"
            runBestEffort(
                listOf(e2eAdb.get(), "shell", "uiautomator", "dump", onDeviceDumpPath),
                diagnosticsTimeoutSeconds,
            )
            runBestEffort(
                listOf(
                    e2eAdb.get(),
                    "pull",
                    onDeviceDumpPath,
                    File(diagnosticsDir, "$xmlSuiteName-timeout-uidump.xml").absolutePath,
                ),
                diagnosticsTimeoutSeconds,
            )
            // Unfiltered: unlike every other CI failure branch (which pipes through
            // scripts/ci/test-support/filter_logcat.sh), this dumps the raw buffer so a symptom outside that
            // script's tag allowlist can't be silently dropped again.
            val rawLogcat = runBestEffort(listOf(e2eAdb.get(), "logcat", "-d"), diagnosticsTimeoutSeconds)
            File(diagnosticsDir, "$xmlSuiteName-timeout-logcat.txt").writeText(rawLogcat)
            println(
                "E2E timeout diagnostics captured for $xmlSuiteName in " +
                    "${diagnosticsDir.absolutePath} (UI Automator dump + full logcat); " +
                    "see the e2e-timeout-diagnostics CI artifact.",
            )

            instrumentProcess.destroyForcibly()
            // PR #628 review: destroyForcibly() above only kills the local `adb shell am
            // instrument` client process; it does not by itself confirm the on-device
            // instrumentation (com.gb4pc, am instrument's targetPackage) actually stopped. If it
            // lingered, every later connectedE2EAndroidTest step in the same CI job (overlay,
            // gallery, permissions granted/denied, setup-activity granted/denied/permission-dialog,
            // partial-access-photo-picker) targets that same process and could each need their own
            // e2eInstrumentTimeoutMinutes wait, eroding most of this fix's benefit. Force-stop both
            // the target and test packages explicitly (`am force-stop` acts at the ActivityManager
            // level and does not itself wait on app cooperation, so this is not a source of a
            // second hang) so the device is guaranteed clean for whichever step runs next,
            // regardless of whether the local client's death alone would have propagated.
            e2eExecOps.exec {
                commandLine(e2eAdb.get(), "shell", "am", "force-stop", "com.gb4pc")
                isIgnoreExitValue = true
            }
            e2eExecOps.exec {
                commandLine(e2eAdb.get(), "shell", "am", "force-stop", "com.gb4pc.test")
                isIgnoreExitValue = true
            }
        }
        drainThread.join(TimeUnit.MINUTES.toMillis(1))
        val output = instrumentOut.toString()
        print(output)

        // Parse INSTRUMENTATION_STATUS blocks into JUnit XML.
        // Each test emits STATUS_CODE 1 (started) then 0 (pass), -2 (failure), or -1 (error).
        // Stack-trace values continue on lines starting with \t until the next STATUS: key.
        data class TestCase(
            val cls: String,
            val name: String,
            val code: Int,
            val stack: String,
        )
        val cases = mutableListOf<TestCase>()
        var curName = ""
        var curClass = ""
        var curStack = StringBuilder()
        var inStack = false
        for (line in output.lines()) {
            when {
                line.startsWith("INSTRUMENTATION_STATUS: test=") -> {
                    curName = line.removePrefix("INSTRUMENTATION_STATUS: test=")
                    inStack = false
                }

                line.startsWith("INSTRUMENTATION_STATUS: class=") -> {
                    curClass = line.removePrefix("INSTRUMENTATION_STATUS: class=")
                    inStack = false
                }

                line.startsWith("INSTRUMENTATION_STATUS: stack=") -> {
                    curStack = StringBuilder(line.removePrefix("INSTRUMENTATION_STATUS: stack="))
                    inStack = true
                }

                // Any other INSTRUMENTATION_STATUS: key ends a stack-trace continuation.
                line.startsWith("INSTRUMENTATION_STATUS:") -> {
                    inStack = false
                }

                inStack && line.startsWith("\t") -> {
                    curStack.append('\n').append(line)
                }

                line.startsWith("INSTRUMENTATION_STATUS_CODE:") -> {
                    inStack = false
                    val code = line.removePrefix("INSTRUMENTATION_STATUS_CODE:").trim().toIntOrNull() ?: 0
                    if (curName.isNotEmpty()) {
                        if (code != 1) {
                            cases += TestCase(curClass, curName, code, curStack.toString())
                            // Emit per-test marker immediately so the CI log is the source of truth.
                            val outcome =
                                when (code) {
                                    0 -> "PASS"
                                    -3 -> "SKIP"
                                    else -> "FAIL"
                                }
                            val stackStr = curStack.toString()
                            val msg =
                                if (code != 0 && code != -3) {
                                    jsonEscape(
                                        stackStr
                                            .lines()
                                            .firstOrNull()
                                            .orEmpty()
                                            .take(200),
                                    )
                                } else {
                                    ""
                                }
                            val trace =
                                if (code != 0 && code != -3) {
                                    val frames = stackStr.lines().take(10).joinToString("\n")
                                    jsonEscape(frames)
                                } else {
                                    ""
                                }
                            println(
                                """##GB4PC_TEST## {"suite":"${jsonEscape(
                                    curClass,
                                )}","name":"${jsonEscape(curName)}","outcome":"$outcome","ms":0,"msg":"$msg","trace":"$trace"}""",
                            )
                        }
                        curStack = StringBuilder() // always reset so stale stack can't leak into the next test
                    }
                }

                inStack -> {
                    curStack.append('\n').append(line)
                }
            }
        }
        val xmlOutDir = e2eXmlDir.get().asFile.also { it.mkdirs() }
        val failCount = cases.count { it.code != 0 }

        fun String.esc() = replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;")
        File(xmlOutDir, "TEST-$xmlSuiteName.xml").writeText(
            buildString {
                appendLine("""<?xml version="1.0" encoding="UTF-8"?>""")
                appendLine("""<testsuite name="$xmlSuiteName" tests="${cases.size}" failures="$failCount" errors="0">""")
                for (c in cases) {
                    append("""  <testcase name="${c.name.esc()}" classname="${c.cls.esc()}"""")
                    if (c.code == 0) {
                        appendLine("/>")
                    } else {
                        appendLine(">")
                        val msg =
                            c.stack
                                .lines()
                                .firstOrNull()
                                .orEmpty()
                                .take(200)
                                .esc()
                        appendLine("""    <failure message="$msg">""")
                        appendLine(c.stack.esc())
                        appendLine("    </failure>")
                        appendLine("  </testcase>")
                    }
                }
                appendLine("</testsuite>")
            },
        )

        // Timeout guard (issue #611): checked first and unconditionally, since a hung run that
        // happened to complete a few tests before being killed would otherwise slip past the
        // empty-results guard (cases non-empty) and the failure guard (those completed cases
        // may all have passed), reporting a false-green partial run instead of the hang it was.
        if (timedOut) {
            throw GradleException(
                "E2E: 'am instrument' for $xmlSuiteName did not finish within " +
                    "${e2eInstrumentTimeoutMinutes}m and was killed (device or test likely hung; " +
                    "check logcat); ${cases.size} test(s) completed before the timeout",
            )
        }
        // Crash guard: these strings appear in -r output on hard abort/crash.
        if (output.contains("Process crashed") || output.contains("INSTRUMENTATION_ABORTED")) {
            throw GradleException("E2E instrumentation process crashed — check device logs")
        }
        // Empty-results guard: if no test cases were parsed the runner never started
        // (missing APK, ADB disconnect, wrong runner class, etc.) — catch it before
        // failCount produces a false-green 0/0 result.
        if (cases.isEmpty()) {
            throw GradleException("E2E: no test results parsed — runner or device failure (check logcat)")
        }
        // Failure guard: use the parsed result rather than the human-readable summary
        // (which may be absent in -r mode).
        if (failCount > 0) {
            throw GradleException("E2E tests FAILED ($failCount failure(s)) — see instrument output above")
        }
    }
}
