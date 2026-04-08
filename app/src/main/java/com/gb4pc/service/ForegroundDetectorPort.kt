package com.gb4pc.service

/**
 * Abstraction over foreground-app detection, allowing [ForegroundDetector] to be
 * substituted with a test stub without opening the production class for inheritance.
 */
fun interface ForegroundDetectorPort {
    fun getForegroundPackage(): String?
}
