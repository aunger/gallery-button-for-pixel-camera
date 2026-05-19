package com.gb4pc.overlay

import android.graphics.Canvas
import android.graphics.ColorFilter
import android.graphics.Path
import android.graphics.PixelFormat
import android.graphics.Rect
import android.graphics.drawable.AdaptiveIconDrawable
import android.graphics.drawable.Drawable
import android.os.Build
import kotlin.math.abs
import kotlin.math.pow

/**
 * A [Drawable] wrapper that clips its content to a superellipse ("squircle") shape,
 * independent of the device launcher's adaptive-icon mask.
 *
 * On the [google_apis] API-35 emulator the launcher clips adaptive icons to a circle.
 * [AdaptiveIconDrawable.draw] internally applies that mask before we can do anything — so
 * [android.view.View.clipToOutline] with a rounded-rect outline has no visible effect (the
 * content is already a circle that fits inside the rounded-rect, leaving the outer boundary
 * circular).
 *
 * This drawable fixes the problem at the drawable level:
 *  - If the wrapped drawable is an [AdaptiveIconDrawable] (API 26+), the background and
 *    foreground layers are drawn **directly** (bypassing the adaptive-icon mask) with only
 *    the squircle clip applied to the canvas.
 *  - Otherwise, the wrapped drawable is drawn with the squircle clip applied.
 *
 * The squircle uses the superellipse formula  |2x/w − 1|^n + |2y/h − 1|^n ≤ 1  with n = 4,
 * matching the [com.gb4pc.e2e.visual.ShapeTemplates.squircle] template used by the E2E tests.
 *
 * Issue #188: make squircular overlays independent of launcher icon-mask shape.
 */
class SquircleDrawable(val inner: Drawable) : Drawable() {

    private val clipPath = Path()
    private var pathWidth = -1
    private var pathHeight = -1

    override fun draw(canvas: Canvas) {
        val b = bounds
        if (b.isEmpty) return

        rebuildPathIfNeeded(b.width(), b.height())

        val save = canvas.save()
        try {
            canvas.translate(b.left.toFloat(), b.top.toFloat())
            canvas.clipPath(clipPath)

            // Draw into the local (0,0) origin after translate.
            val localBounds = Rect(0, 0, b.width(), b.height())

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && inner is AdaptiveIconDrawable) {
                // Draw background and foreground layers directly, bypassing
                // AdaptiveIconDrawable's internal launcher mask.
                inner.background?.let { bg ->
                    bg.bounds = localBounds
                    bg.draw(canvas)
                }
                inner.foreground?.let { fg ->
                    fg.bounds = localBounds
                    fg.draw(canvas)
                }
            } else {
                inner.bounds = localBounds
                inner.draw(canvas)
            }
        } finally {
            canvas.restoreToCount(save)
        }
    }

    override fun onBoundsChange(bounds: Rect) {
        super.onBoundsChange(bounds)
        // Invalidate cached path whenever bounds change.
        pathWidth = -1
        pathHeight = -1
    }

    override fun setAlpha(alpha: Int) {
        inner.alpha = alpha
        invalidateSelf()
    }

    override fun setColorFilter(cf: ColorFilter?) {
        inner.colorFilter = cf
        invalidateSelf()
    }

    @Deprecated("Deprecated in Java")
    override fun getOpacity(): Int = PixelFormat.TRANSLUCENT

    override fun getIntrinsicWidth(): Int = inner.intrinsicWidth
    override fun getIntrinsicHeight(): Int = inner.intrinsicHeight

    // ── path helpers ──────────────────────────────────────────────────────────

    /**
     * Rebuilds [clipPath] for the given dimensions only when the dimensions have changed.
     * The path traces the superellipse boundary  |2x/w − 1|^4 + |2y/h − 1|^4 = 1.
     */
    private fun rebuildPathIfNeeded(w: Int, h: Int) {
        if (w == pathWidth && h == pathHeight) return
        pathWidth = w
        pathHeight = h
        buildSuperellipsePath(clipPath, w, h)
    }

    companion object {
        /**
         * Fills [path] with a superellipse (n = 4) approximated by a dense polygon.
         *
         * The superellipse is parameterised as:
         *   x(θ) = cx + rx * sign(cos θ) * |cos θ|^(2/n)
         *   y(θ) = cy + ry * sign(sin θ) * |sin θ|^(2/n)
         * with n = 4, so the exponent is 2/4 = 0.5.
         *
         * This formula produces the same shape as [com.gb4pc.e2e.visual.ShapeTemplates.squircle],
         * which uses the implicit form |2x/w − 1|^4 + |2y/h − 1|^4 ≤ 1.
         */
        private const val STEPS = 256
        private const val N = 4.0
        internal const val EXP = 2.0 / N  // = 0.5

        fun buildSuperellipsePath(path: Path, w: Int, h: Int) {
            path.reset()
            val cx = w / 2.0
            val cy = h / 2.0
            val rx = cx
            val ry = cy

            for (i in 0 until STEPS) {
                val theta = 2.0 * Math.PI * i / STEPS
                val cosT = Math.cos(theta)
                val sinT = Math.sin(theta)
                // Parametric superellipse point.
                val px = (cx + rx * Math.signum(cosT) * abs(cosT).pow(EXP)).toFloat()
                val py = (cy + ry * Math.signum(sinT) * abs(sinT).pow(EXP)).toFloat()
                if (i == 0) {
                    path.moveTo(px, py)
                } else {
                    path.lineTo(px, py)
                }
            }
            path.close()
        }
    }
}
