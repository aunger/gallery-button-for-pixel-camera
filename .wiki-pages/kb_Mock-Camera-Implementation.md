Implementing a mock camera for E2E tests requires choosing between virtualscene rendering and simple in-app color rendering. Each approach has tradeoffs.

## Design Tradeoff: VirtualScene vs In-App Green

### Option 1: VirtualScene Renderer
Use a GPU-accelerated 3D renderer to generate synthetic camera frames.

**Pros:**
- Realistic rendering
- Can test animation, perspective, complex scenes

**Cons:**
- **Incompatible with CI GPU:** VirtualScene requires OpenGL-es support. CI emulators run with `-gpu swiftshader_indirect` (KVM indirect rendering), which doesn't support OpenGL-es well enough for VirtualScene
- Adds significant complexity
- Slower execution

**Status:** Rejected for this project due to CI incompatibility

### Option 2: In-App Green Rendering (Chosen)
App renders a solid green view directly and inserts it as a synthetic camera frame.

**Pros:**
- Simple (no GPU overhead)
- Works with `-gpu swiftshader_indirect`
- Fast
- Deterministic (always the same color)

**Cons:**
- Doesn't test real rendering
- Limited visual variety

**Implementation:**
1. Render solid `#00C853` (Google green) view in the camera module
2. Convert to JPEG (synthetic capture)
3. Insert into MediaStore via `ContentResolver.insert()`
4. Signal readiness via `onWindowFocusChanged(hasFocus=true)`

The `onWindowFocusChanged(true)` signal is important: it's a reliable indicator that the view has been composed and added to the window tree, making the app ready for screenshots.

## Verification: check_green_feed.py

Validates that the camera is producing green:
- **Sample region:** 200×200 pixels center of screen
- **Color target:** `#00C853` (Google green)
- **Tolerance:** ±20 per RGB channel
- **Acceptance:** ≥90% of pixels within tolerance

This check ensures the mock camera is rendering (not just saying it is) before proceeding to screenshot assertions.

---

**Source:** [PR #119 — Mock Camera Implementation & CI Smoke Check](https://github.com/aunger/gallery-button-for-pixel-camera/pull/119)
