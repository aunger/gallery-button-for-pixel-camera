plugins {
    id("com.android.application")
}

/*
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

    testOptions {
        unitTests {
            isIncludeAndroidResources = true
            isReturnDefaultValues = true
        }
    }
}

dependencies {
    // Robolectric lets us launch LastPhotoActivity on the JVM and assert it does not
    // crash against an empty (or populated) MediaStore. This guards the regression in
    // issue #230 where an embedded "LIMIT" in the query sort order threw on launch.
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.robolectric:robolectric:4.14.1")
    testImplementation("androidx.test:core:1.6.1")
}
