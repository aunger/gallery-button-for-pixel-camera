package com.gb4pc.e2e.visual

import kotlin.math.max
import kotlin.math.min

/**
 * Shape types that the classifier recognises.
 */
sealed class Shape {
    object SQUARE   : Shape()
    object CIRCLE   : Shape()
    object SQUIRCLE : Shape()
}

/**
 * Result of classifying a [MaskData] against the three known shape templates.
 *
 * @param winner       The shape with the highest IoU against the candidate.
 * @param winnerIoU    Best IoU achieved by [winner].
 * @param runnerUpIoU  Best IoU achieved by the second-best shape.
 */
data class ClassifyResult(val winner: Shape, val winnerIoU: Float, val runnerUpIoU: Float)

/**
 * Classifies a [MaskData] as one of SQUARE / CIRCLE / SQUIRCLE by comparing it against
 * ground-truth templates from [ShapeTemplates] using Intersection-over-Union (IoU).
 *
 * All code is pure JVM — no android.* API dependencies — so it runs in both JVM unit
 * tests (app/src/test) and Android instrumented tests (app/src/androidTest).
 */
object ShapeMatcher {

    /**
     * Classifies [candidate] as the shape with the highest IoU after a position and scale sweep.
     *
     * Algorithm:
     * 1. Tight-crop [candidate] to its bounding box.
     * 2. For each template in {SQUARE, CIRCLE, SQUIRCLE}:
     *    - Scale sweep: template rendered at (bboxW ± 3, bboxH ± 3).
     *    - Position sweep: template offset by (dx, dy) ∈ [−8, +8]².
     *    - Take the maximum IoU over the full sweep.
     * 3. Winner = argmax IoU.
     */
    fun classify(candidate: MaskData): ClassifyResult {
        val cropped = cropToBox(candidate)
        if (cropped.pixelCount == 0) {
            return ClassifyResult(Shape.SQUARE, 0f, 0f)
        }

        val bw = cropped.width
        val bh = cropped.height

        var squareIoU   = 0f
        var circleIoU   = 0f
        var squircleIoU = 0f

        for (dw in -3..3) {
            for (dh in -3..3) {
                val tw = max(1, bw + dw)
                val th = max(1, bh + dh)
                val squareTpl   = ShapeTemplates.square(tw, th)
                val circleTpl   = ShapeTemplates.circle(tw, th)
                val squircleTpl = ShapeTemplates.squircle(tw, th)

                for (dx in -8..8) {
                    for (dy in -8..8) {
                        val iSquare   = sweepIoU(cropped, squareTpl,   dx, dy)
                        val iCircle   = sweepIoU(cropped, circleTpl,   dx, dy)
                        val iSquircle = sweepIoU(cropped, squircleTpl, dx, dy)
                        if (iSquare   > squareIoU)   squareIoU   = iSquare
                        if (iCircle   > circleIoU)   circleIoU   = iCircle
                        if (iSquircle > squircleIoU) squircleIoU = iSquircle
                    }
                }
            }
        }

        // Determine winner and runner-up.
        val scores = listOf(
            Shape.SQUARE   to squareIoU,
            Shape.CIRCLE   to circleIoU,
            Shape.SQUIRCLE to squircleIoU
        ).sortedByDescending { it.second }

        val winner     = scores[0].first
        val winnerIoU  = scores[0].second
        val runnerUpIoU = scores[1].second

        return ClassifyResult(winner, winnerIoU, runnerUpIoU)
    }

    /**
     * Asserts that [candidate] classifies as [expected] with sufficient confidence.
     *
     * @param minWinnerIoU  Minimum IoU the winning template must achieve (default 0.92).
     * @param minMargin     Minimum gap between winner IoU and runner-up IoU (default 0.05).
     *
     * @throws AssertionError if any gate fails.
     */
    fun requireShape(
        candidate: MaskData,
        expected: Shape,
        minWinnerIoU: Float = 0.92f,
        minMargin: Float = 0.05f
    ) {
        val result = classify(candidate)
        val margin = result.winnerIoU - result.runnerUpIoU

        if (result.winnerIoU < minWinnerIoU) {
            throw AssertionError(
                "Shape sanity check failed: best IoU ${result.winnerIoU} < $minWinnerIoU " +
                "(winner=${result.winner}, expected=$expected)"
            )
        }
        if (margin < minMargin) {
            throw AssertionError(
                "Shape margin check failed: margin $margin < $minMargin " +
                "(winner=${result.winner} IoU=${result.winnerIoU}, " +
                "runnerUp IoU=${result.runnerUpIoU}, expected=$expected)"
            )
        }
        if (result.winner != expected) {
            throw AssertionError(
                "Shape identity check failed: winner=${result.winner} != expected=$expected " +
                "(winnerIoU=${result.winnerIoU}, runnerUpIoU=${result.runnerUpIoU})"
            )
        }
    }

    // ── internal helpers ─────────────────────────────────────────────

    /**
     * Computes IoU between [candidate] and [template] when the template is offset by
     * ([dx], [dy]) relative to the candidate's origin.
     *
     * Both masks are treated as positioned in an infinite plane; pixels outside either
     * mask boundary count as false. The union is the total number of pixels that are
     * true in either mask at their respective absolute coordinates.
     */
    private fun sweepIoU(candidate: MaskData, template: MaskData, dx: Int, dy: Int): Float {
        val cw = candidate.width
        val ch = candidate.height
        val tw = template.width
        val th = template.height

        // Overlap region in candidate coordinates.
        val overlapLeft   = max(0, dx)
        val overlapTop    = max(0, dy)
        val overlapRight  = min(cw, dx + tw)
        val overlapBottom = min(ch, dy + th)

        if (overlapRight <= overlapLeft || overlapBottom <= overlapTop) {
            // No overlap: IoU = 0 only if both masks are non-empty.
            val union = candidate.pixelCount + template.pixelCount
            return if (union == 0) 1f else 0f
        }

        var intersection = 0
        for (cy in overlapTop until overlapBottom) {
            val ty = cy - dy
            for (cx in overlapLeft until overlapRight) {
                val tx = cx - dx
                if (candidate.bits[cy * cw + cx] && template.bits[ty * tw + tx]) {
                    intersection++
                }
            }
        }

        val trueUnion = candidate.pixelCount + template.pixelCount - intersection
        return if (trueUnion == 0) 1f else intersection.toFloat() / trueUnion
    }

    /**
     * Tight-crops [mask] to its bounding box, returning a new [MaskData].
     * If the mask is empty, returns an empty 0×0 [MaskData].
     */
    private fun cropToBox(mask: MaskData): MaskData {
        if (mask.pixelCount == 0) return MaskData.empty()
        val bw = mask.bboxWidth
        val bh = mask.bboxHeight
        val newBits = BooleanArray(bw * bh)
        for (y in 0 until bh) {
            for (x in 0 until bw) {
                newBits[y * bw + x] = mask.bits[(mask.bboxTop + y) * mask.width + (mask.bboxLeft + x)]
            }
        }
        val newCx = mask.centroidX - mask.bboxLeft
        val newCy = mask.centroidY - mask.bboxTop
        return MaskData(
            bits = newBits,
            width = bw, height = bh,
            bboxLeft = 0, bboxTop = 0, bboxRight = bw, bboxBottom = bh,
            centroidX = newCx, centroidY = newCy,
            pixelCount = mask.pixelCount
        )
    }
}
