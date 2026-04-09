package com.gb4pc.e2e

/**
 * Marks a test class or method as an end-to-end test requiring a real device or emulator
 * with Pixel Camera installed.
 *
 * E2E tests are excluded from the standard [connectedDebugAndroidTest] Gradle task (which
 * uses `notPackage=com.gb4pc.e2e`) and are run separately via [connectedE2EAndroidTest].
 */
@Retention(AnnotationRetention.RUNTIME)
@Target(AnnotationTarget.CLASS, AnnotationTarget.FUNCTION)
annotation class E2ETest
