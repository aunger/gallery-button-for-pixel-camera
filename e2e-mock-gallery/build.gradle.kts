plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

/**
 * Minimal test-only APK used by the E2E visual test suite.
 *
 * applicationId = "com.gb4pc.mockgallery" is the gallery package seeded into
 * PrefsManager by E2EFixture.seedGalleryPrefs() so that GB4PC's overlay points
 * to this app during visual tests.
 *
 * LastPhotoActivity displays the most-recently-captured photo from MediaStore,
 * giving the tests a real gallery-app target without requiring a shipping gallery.
 */
android {
    namespace = "com.gb4pc.mockgallery"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.gb4pc.mockgallery"
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

// No external dependencies needed — MediaStore is part of the Android framework.
