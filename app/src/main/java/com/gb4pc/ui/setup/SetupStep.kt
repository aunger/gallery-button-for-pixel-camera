package com.gb4pc.ui.setup

import android.os.Build

/**
 * The ordered steps shown in the guided setup flow (PM-01).
 *
 * NOTIFICATION is included only on API 33+ where POST_NOTIFICATIONS is a runtime
 * permission (PM-05); below that, the foreground-service notification is granted
 * implicitly and the step is skipped.
 *
 * MEDIA requests read access to the shared image collection (READ_MEDIA_IMAGES on
 * API 33+, READ_EXTERNAL_STORAGE below), which is a dangerous runtime permission on
 * every supported API level (26+), so the step always applies (PM-06, issue #509).
 * It is requested here during setup, not from the running service, because once the
 * camera is on screen it is too late to prompt (see the permission-timing principle
 * in SPEC §2).
 */
enum class SetupStep {
    NOTIFICATION,
    MEDIA,
    USAGE_ACCESS,
    OVERLAY,
    BATTERY,
}

fun getSetupSteps(apiLevel: Int = Build.VERSION.SDK_INT): List<SetupStep> =
    buildList {
        if (apiLevel >= 33) add(SetupStep.NOTIFICATION) // PM-05
        add(SetupStep.MEDIA) // PM-06
        add(SetupStep.USAGE_ACCESS)
        add(SetupStep.OVERLAY)
        add(SetupStep.BATTERY)
    }
