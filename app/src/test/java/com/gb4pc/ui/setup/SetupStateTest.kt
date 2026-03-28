package com.gb4pc.ui.setup

import org.junit.Assert.*
import org.junit.Test

class SetupStateTest {

    @Test
    fun `initial step is NOTIFICATION on API 33+`() {
        val state = SetupState(apiLevel = 33)
        assertEquals(SetupStep.NOTIFICATION, state.currentStep)
    }

    @Test
    fun `initial step is USAGE_ACCESS on API below 33`() {
        val state = SetupState(apiLevel = 32)
        assertEquals(SetupStep.USAGE_ACCESS, state.currentStep)
    }

    @Test
    fun `step order is correct for API 33+`() {
        val steps = SetupState.getSteps(apiLevel = 33)
        assertEquals(
            listOf(SetupStep.NOTIFICATION, SetupStep.USAGE_ACCESS, SetupStep.OVERLAY, SetupStep.BATTERY),
            steps
        )
    }

    @Test
    fun `step order is correct for API below 33`() {
        val steps = SetupState.getSteps(apiLevel = 32)
        assertEquals(
            listOf(SetupStep.USAGE_ACCESS, SetupStep.OVERLAY, SetupStep.BATTERY),
            steps
        )
    }

    @Test
    fun `advance moves to next step`() {
        val state = SetupState(apiLevel = 32)
        assertEquals(SetupStep.USAGE_ACCESS, state.currentStep)
        state.advance()
        assertEquals(SetupStep.OVERLAY, state.currentStep)
        state.advance()
        assertEquals(SetupStep.BATTERY, state.currentStep)
    }

    @Test
    fun `advance past last step marks completed`() {
        val state = SetupState(apiLevel = 32)
        state.advance() // -> OVERLAY
        state.advance() // -> BATTERY
        state.advance() // -> completed
        assertTrue(state.isCompleted)
    }

    @Test
    fun `isCompleted initially false`() {
        val state = SetupState(apiLevel = 32)
        assertFalse(state.isCompleted)
    }
}

enum class SetupStep {
    NOTIFICATION,
    USAGE_ACCESS,
    OVERLAY,
    BATTERY
}

class SetupState(apiLevel: Int) {
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
                if (apiLevel >= 33) add(SetupStep.NOTIFICATION)
                add(SetupStep.USAGE_ACCESS)
                add(SetupStep.OVERLAY)
                add(SetupStep.BATTERY)
            }
        }
    }
}
