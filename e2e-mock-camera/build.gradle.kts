plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

/**
 * Minimal stub APK used exclusively for E2E testing on the emulator.
 *
 * applicationId = "com.google.android.GoogleCamera" makes GB4PC's ForegroundDetector
 * treat this stub as Pixel Camera, so the overlay lifecycle can be tested end-to-end
 * without requiring a real Pixel Camera APK to run on the emulator.
 *
 * The stub opens the front camera on Activity resume and releases it on pause,
 * which is sufficient to fire CameraManager.AvailabilityCallback in OverlayService.
 */
android {
    namespace = "com.gb4pc.mockcamera"
    compileSdk = 35

    defaultConfig {
        // Must match Constants.PIXEL_CAMERA_PACKAGE so ForegroundDetector recognises it.
        applicationId = "com.google.android.GoogleCamera"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.test"
    }

    buildTypes {
        debug {
            // Debug signing is fine — installed only on test emulators.
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

// No external dependencies needed — Camera2 API is part of the Android framework.
