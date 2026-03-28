package com.gb4pc.ui.setup

import android.os.Build

enum class SetupStep {
    NOTIFICATION,
    USAGE_ACCESS,
    OVERLAY,
    BATTERY
}

/**
 * Tracks the current step in the guided setup flow (PM-01).
 */
class SetupState(apiLevel: Int = Build.VERSION.SDK_INT) {
    private val steps = getSteps(apiLevel)
    private var currentIndex = 0

    val currentStep: SetupStep get() = steps[currentIndex]
    var isCompleted: Boolean = false
        private set

    fun advance() {
        if (currentIndex < steps.size - 1) {
            currentIndex++
        } else {
            isCompleted = true
        }
    }

    companion object {
        fun getSteps(apiLevel: Int): List<SetupStep> {
            return buildList {
                if (apiLevel >= 33) add(SetupStep.NOTIFICATION) // PM-05
                add(SetupStep.USAGE_ACCESS)
                add(SetupStep.OVERLAY)
                add(SetupStep.BATTERY)
            }
        }
    }
}
