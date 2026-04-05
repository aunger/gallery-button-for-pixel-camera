package com.gb4pc.data

import kotlin.math.abs
import kotlin.math.min
import kotlin.math.max

/**
 * Utilities for quantizing and comparing display aspect ratios (PS-03, PS-04).
 */
object AspectRatioUtil {

    /**
     * Returns the aspect ratio quantized to two decimal places.
     * Always divides the smaller dimension by the larger, so orientation doesn't matter.
     */
    fun quantize(width: Int, height: Int): String {
        val smaller = min(width, height).toFloat()
        val larger = max(width, height).toFloat()
        val ratio = smaller / larger
        return "%.2f".format(ratio)
    }

    /**
     * Finds the numerically closest ratio string in [availableRatios] to [targetRatio].
     * Returns null if [availableRatios] is empty.
     */
    fun findClosestRatio(targetRatio: String, availableRatios: Set<String>): String? {
        if (availableRatios.isEmpty()) return null
        val target = targetRatio.toFloatOrNull() ?: return null
        return availableRatios.minByOrNull { ratio ->
            val value = ratio.toFloatOrNull() ?: Float.MAX_VALUE
            abs(value - target)
        }
    }
}
