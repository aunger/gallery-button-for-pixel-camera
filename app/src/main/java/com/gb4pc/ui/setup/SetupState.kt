package com.gb4pc.ui.setup

import android.os.Build

/**
 * The ordered steps shown in the guided setup flow (PM-01).
 *
 * NOTIFICATION is included only on API 33+ where POST_NOTIFICATIONS is a runtime
 * permission (PM-05); below that, the foreground-service notification is granted
 * implicitly and the step is skipped.
 */
enum class SetupStep {
    NOTIFICATION,
    USAGE_ACCESS,
    OVERLAY,
    BATTERY,
}

fun getSetupSteps(apiLevel: Int = Build.VERSION.SDK_INT): List<SetupStep> = buildList {
    if (apiLevel >= 33) add(SetupStep.NOTIFICATION) // PM-05
    add(SetupStep.USAGE_ACCESS)
    add(SetupStep.OVERLAY)
    add(SetupStep.BATTERY)
}
