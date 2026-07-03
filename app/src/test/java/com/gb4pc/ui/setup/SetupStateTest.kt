package com.gb4pc.ui.setup

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SetupStateTest {
    @Test
    fun `step order is correct for API 33+`() {
        assertEquals(
            listOf(SetupStep.NOTIFICATION, SetupStep.MEDIA, SetupStep.USAGE_ACCESS, SetupStep.OVERLAY, SetupStep.BATTERY),
            getSetupSteps(apiLevel = 33),
        )
    }

    @Test
    fun `step order is correct for API below 33`() {
        assertEquals(
            listOf(SetupStep.MEDIA, SetupStep.USAGE_ACCESS, SetupStep.OVERLAY, SetupStep.BATTERY),
            getSetupSteps(apiLevel = 32),
        )
    }

    @Test
    fun `first step on API 33+ is NOTIFICATION`() {
        assertEquals(SetupStep.NOTIFICATION, getSetupSteps(apiLevel = 33).first())
    }

    @Test
    fun `first step on API below 33 is MEDIA`() {
        assertEquals(SetupStep.MEDIA, getSetupSteps(apiLevel = 32).first())
    }

    @Test
    fun `MEDIA step is always present`() {
        assertTrue(getSetupSteps(apiLevel = 33).contains(SetupStep.MEDIA))
        assertTrue(getSetupSteps(apiLevel = 32).contains(SetupStep.MEDIA))
        assertTrue(getSetupSteps(apiLevel = 26).contains(SetupStep.MEDIA))
    }
}
