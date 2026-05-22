Android's `AdaptiveIconDrawable` applies the device launcher's icon mask **before** drawing to the application canvas. This creates a non-obvious graphics pipeline order that can defeat naive clipping approaches.

## The Problem

When using `SquircleDrawable` (a superellipse shape) with adaptive icons, you might assume that calling `setClipToOutline(true)` would clip the drawable to a superellipse. In most cases this works. However:

- `AdaptiveIconDrawable.draw()` internally applies the **device launcher's icon mask** before returning to the caller
- On `google_apis` emulator images, the launcher mask is circular
- By the time the canvas clipping layer gets control, the drawable is **already circular**
- `setClipToOutline` then clips the already-circular drawable to a superellipse — but the damage is done; the adaptive icon's monochrome layer has already been masked

Result: The adaptive icon appears circular instead of superellipse-shaped.

## The Solution

**Don't try to clip at the canvas level. Intervene at the drawable level.**

The fix:
1. Don't use `AdaptiveIconDrawable` directly as the icon drawable
2. Instead, extract the adaptive icon's background and monochrome layers
3. Draw them **manually** to a canvas that's clipped to the superellipse path
4. Bypass the launcher mask entirely

This way, the layers are drawn into your clipped canvas, bypassing the `AdaptiveIconDrawable`'s internal masking.

## Lesson: Platform Behavior Ordering

This illustrates a general principle: **understand the order of operations in platform graphics pipelines**. Clipping, masking, and compositing are applied in a specific order, and later operations can't undo earlier ones. When naive approaches fail, trace back through the drawing order to find where the unwanted transformation is happening.

---

**Source:** [PR #193 — SquircleDrawable Implementation](https://github.com/aunger/gallery-button-for-pixel-camera/pull/193)
