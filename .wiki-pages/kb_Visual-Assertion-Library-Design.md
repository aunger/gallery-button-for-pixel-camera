Visual assertions in E2E tests require comparing rendered shapes to ground-truth templates using computer vision techniques. A robust library balances accuracy, performance, and resilience to minor rendering variations.

## Library Components

### ColorMatch: RGB Pixel Comparison
Simple per-pixel color matching with tolerance:
- Match criterion: each channel must be within tolerance (e.g., ±20 per RGB value)
- Use for: uniform regions (solid backgrounds, fills)
- Fast: O(n) pixels, no geometric reasoning

### ShapeTemplates: Ground-Truth Masks
Pre-generated reference images for expected shapes:
- **Square:** 64×64, 128×128, 256×256 pixels
- **Circle:** Same scales
- **Squircle:** Superellipse (n=4, CSS-like 4.2 curvature), same scales

Templates are binary masks (white=inside, black=outside).

### ShapeMatcher: IoU-Based Classification
Compares rendered shape to all templates using Intersection-over-Union (IoU):

1. **Render screenshot** from test
2. **Extract shape region** (binarize rendered pixels)
3. **Position sweep:** Try the shape at ±8 pixel offsets (handles slight render jitter)
4. **Scale sweep:** Try the shape at ±3 pixel scale variations (handles font/DPI differences)
5. **Compute IoU** for each template × position × scale combination
6. **Classify:** Highest IoU wins

Formula: IoU = (intersection area) / (union area)
- IoU = 1.0: Perfect match
- IoU > 0.9: Acceptable match (>90% overlap)
- IoU < 0.8: Likely wrong shape

### Edge-Pixel Dropout Handling

Anti-aliasing and rendering differences can cause edge pixels to be partially transparent or slightly miscolored. The matcher handles this by:
- Using a **binarization threshold** (e.g., alpha > 0.5) before computing IoU
- Allowing **low-intensity edge pixels** to be treated as "don't care" in the comparison

This prevents 1-2 pixel rendering differences from causing false negatives.

## Validation & Confidence

Test coverage includes:
- **Correctness:** Squircle correctly classified vs square/circle at all scales
- **Resilience:** 1-2 pixel shifts and anti-aliasing don't break detection
- **Performance:** Classification completes in <100ms per screenshot

Typical confidence: >95% correct classification on real screenshots.

---

**Source:** [PR #120 — Visual-Assertion Library Design](https://github.com/aunger/gallery-button-for-pixel-camera/pull/120)
