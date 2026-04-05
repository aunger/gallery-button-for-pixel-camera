package com.gb4pc.data

import org.junit.Assert.*
import org.junit.Test

class AspectRatioUtilTest {

    @Test
    fun `quantize returns two decimal places`() {
        // 1080x2400 portrait -> 1080/2400 = 0.45
        assertEquals("0.45", AspectRatioUtil.quantize(1080, 2400))
    }

    @Test
    fun `quantize uses smaller divided by larger`() {
        // Landscape: 2400x1080 should give same result as portrait
        assertEquals("0.45", AspectRatioUtil.quantize(2400, 1080))
    }

    @Test
    fun `quantize for 16-9 display`() {
        // 1080x1920 -> 1080/1920 = 0.5625 -> "0.56"
        assertEquals("0.56", AspectRatioUtil.quantize(1080, 1920))
    }

    @Test
    fun `quantize for square display`() {
        assertEquals("1.00", AspectRatioUtil.quantize(1000, 1000))
    }

    @Test
    fun `findClosestRatio returns exact match when available`() {
        val ratios = setOf("0.45", "0.56", "1.00")
        assertEquals("0.45", AspectRatioUtil.findClosestRatio("0.45", ratios))
    }

    @Test
    fun `findClosestRatio returns numerically closest`() {
        val ratios = setOf("0.45", "0.56", "1.00")
        // 0.50 is closer to 0.45 (diff=0.05) than 0.56 (diff=0.06)
        assertEquals("0.45", AspectRatioUtil.findClosestRatio("0.50", ratios))
    }

    @Test
    fun `findClosestRatio returns null for empty set`() {
        assertNull(AspectRatioUtil.findClosestRatio("0.50", emptySet()))
    }

    @Test
    fun `findClosestRatio picks closest when equidistant`() {
        val ratios = setOf("0.40", "0.60")
        // 0.50 is equidistant; either is acceptable but should return one
        val result = AspectRatioUtil.findClosestRatio("0.50", ratios)
        assertTrue(result == "0.40" || result == "0.60")
    }
}
