package com.gb4pc.e2e.visual

import android.graphics.PointF
import android.graphics.Rect

/**
 * Bridge between [BinaryMask] (androidTest / android.graphics types) and [MaskData]
 * (main / pure-JVM type accepted by [ShapeMatcher]).
 *
 * The conversion is lossless: a round-trip `BinaryMask → MaskData → BinaryMask` produces
 * a value equal to the original.
 */

fun BinaryMask.toMaskData(): MaskData =
    MaskData(
        bits = bits.copyOf(),
        width = width,
        height = height,
        bboxLeft = bbox.left,
        bboxTop = bbox.top,
        bboxRight = bbox.right,
        bboxBottom = bbox.bottom,
        centroidX = centroid.x,
        centroidY = centroid.y,
        pixelCount = pixelCount,
    )

fun MaskData.toBinaryMask(): BinaryMask =
    BinaryMask(
        bits = bits.copyOf(),
        width = width,
        height = height,
        bbox = Rect(bboxLeft, bboxTop, bboxRight, bboxBottom),
        centroid = PointF(centroidX, centroidY),
        pixelCount = pixelCount,
    )
