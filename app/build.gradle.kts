import java.io.ByteArrayOutputStream
import java.time.LocalDate
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

// Versioning: semver versionName + yyyyMMdd time-based versionCode.
// GitHub releases must be tagged v{versionName} (e.g. v0.0.1).
// CI may override the build number via the BUILD_NUMBER env var.
// BUILD_NUMBER must be a valid integer; a malformed value fails the build loudly.
val envBuildNumber: String? = System.getenv("BUILD_NUMBER")
val buildNumber: Int = when {
    envBuildNumber == null ->
        LocalDate.now(ZoneOffset.UTC).format(DateTimeFormatter.BASIC_ISO_DATE).toInt()
    envBuildNumber.toIntOrNull() != null -> envBuildNumber.toInt()
    else -> error("BUILD_NUMBER env var is set but not a valid integer: '$envBuildNumber'")
}

android {
    namespace = "com.gb4pc"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.gb4pc"
        minSdk = 26
        targetSdk = 35
        versionCode = buildNumber
        versionName = "0.0.1"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        // Exclude E2E tests from the standard instrumented-test run.
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
                "proguard-rules.pro"
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

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    testOptions {
        unitTests {
            isIncludeAndroidResources = true
            isReturnDefaultValues = true
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
    implementation(platform("androidx.compose:compose-bom:2024.12.01"))
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
    testImplementation("org.mockito:mockito-core:5.14.2")
    testImplementation("org.mockito.kotlin:mockito-kotlin:5.4.0")
    testImplementation("org.robolectric:robolectric:4.14.1")
    testImplementation("androidx.test:core:1.6.1")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.9.0")
    testImplementation("org.json:json:20231013")

    // Android instrumented testing
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1")
    androidTestImplementation("androidx.test.espresso:espresso-intents:3.6.1")
    androidTestImplementation(platform("androidx.compose:compose-bom:2024.12.01"))
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
// Note: captures SDK dir and APK paths at configuration time so they are available
// inside the doLast execution closure where the project extension is out of scope.
val e2eAdb = "${android.sdkDirectory.absolutePath}/platform-tools/adb"
val e2eAppApk = layout.buildDirectory
    .file("outputs/apk/debug/app-debug.apk")
val e2eTestApk = layout.buildDirectory
    .file("outputs/apk/androidTest/debug/app-debug-androidTest.apk")

tasks.register("connectedE2EAndroidTest") {
    group = "verification"
    description = "Runs E2E instrumented tests (requires device/emulator with Pixel Camera installed)."
    dependsOn("assembleDebug", "assembleDebugAndroidTest")
    doLast {
        // Install app first so SYSTEM_ALERT_WINDOW can be granted by package name.
        exec { commandLine(e2eAdb, "install", "-r", e2eAppApk.get().asFile.absolutePath) }
        // Grant SYSTEM_ALERT_WINDOW now that the app UID exists on the device.
        exec { commandLine(e2eAdb, "shell", "appops", "set", "com.gb4pc", "SYSTEM_ALERT_WINDOW", "allow") }
        exec { commandLine(e2eAdb, "install", "-r", e2eTestApk.get().asFile.absolutePath) }
        // Run E2E tests. am instrument exits non-zero on test failure but returns 0
        // on process crash; capture stdout and fail loudly if "Process crashed" appears.
        val instrumentOut = ByteArrayOutputStream()
        exec {
            commandLine(
                e2eAdb, "shell", "am", "instrument", "-w",
                "-e", "package", "com.gb4pc.e2e",
                "com.gb4pc.test/androidx.test.runner.AndroidJUnitRunner"
            )
            standardOutput = instrumentOut
        }
        val output = instrumentOut.toString()
        print(output)
        if (output.contains("Process crashed") || output.contains("INSTRUMENTATION_ABORTED")) {
            throw GradleException("E2E instrumentation process crashed — check device logs")
        }
    }
}
