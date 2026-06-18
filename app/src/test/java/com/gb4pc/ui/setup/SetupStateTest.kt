package com.gb4pc.ui.setup

import org.junit.Assert.assertEquals
import org.junit.Test

class SetupStateTest {
    @Test
    fun `step order is correct for API 33+`() {
        assertEquals(
            listOf(SetupStep.NOTIFICATION, SetupStep.USAGE_ACCESS, SetupStep.OVERLAY, SetupStep.BATTERY),
            getSetupSteps(apiLevel = 33),
        )
    }

    @Test
    fun `step order is correct for API below 33`() {
        assertEquals(
            listOf(SetupStep.USAGE_ACCESS, SetupStep.OVERLAY, SetupStep.BATTERY),
            getSetupSteps(apiLevel = 32),
        )
    }

    @Test
    fun `first step on API 33+ is NOTIFICATION`() {
        assertEquals(SetupStep.NOTIFICATION, getSetupSteps(apiLevel = 33).first())
    }

    @Test
    fun `first step on API below 33 is USAGE_ACCESS`() {
        assertEquals(SetupStep.USAGE_ACCESS, getSetupSteps(apiLevel = 32).first())
    }
}
