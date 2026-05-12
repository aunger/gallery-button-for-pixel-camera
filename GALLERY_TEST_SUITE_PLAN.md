# Gallery Button visual E2E test suite — implementation plan

## Goal

Add a visually-evaluated emulator test suite that verifies GB4PC's overlay button:

1. Renders the configured gallery app's adaptive icon at the configured screen position (Tests 1a/1b/1c).
2. Opens the configured gallery app on tap, surfacing the most recent photo (Tests 2a/3a).
3. Behaves correctly when launched into Android's secure-camera mode from a locked screen (Tests 4a/5a).

Tests 4a and 5a are deliberate **red-light tests for a known regression** — they will fail until the secure-camera overlay path is restored.

## Color palette

Locked in up front so visual assertions can be calibrated against a known palette:

| Token  | RGB       | Used for |
|--------|-----------|----------|
| BLUE   | `#1565C0` | Test gallery icon foreground (small square) |
| YELLOW | `#FFD600` | Test gallery icon background |
| GREEN  | `#00C853` | Camera viewfinder + captured photo content |

Choices: mutually distinct in all three channels (nearest separation ~25 in one channel), far from Pixel Camera UI chrome (white/grey/black with brand-red accents), comfortable margin against a per-channel tolerance of 20.

## GREEN feed approach

**Primary**: mock camera renders GREEN itself; never use the emulator's camera hardware.

- `MockCameraActivity` displays a solid-GREEN full-bleed `View` as its "viewfinder" — not wired to camera frames.
- On shutter, the mock camera writes a synthetic GREEN JPEG (`Bitmap.createBitmap` filled, `compress(JPEG, 100)`, `MediaStore.Images.Media.insert`) and notifies MediaStore.
- The existing `CameraDevice.open / close` lifecycle stays intact, so `CameraManager.AvailabilityCallback` still fires and GB4PC's overlay still activates.

Trade-off: less faithful to a "real camera hardware → camera app" path. Acceptable because Pixel Camera is already unavailable on non-Pixel emulators — the mock camera is the camera app under test.

**Alternative (not implemented now, documented for future)**: emulator `-virtualscene-poster wall=<path>/green.png` for all four walls + a real `TextureView` preview in the mock camera. Use this only if a future feature needs to exercise the camera-hardware path. Requires a CI pre-flight check (see Appendix A).

## Decisions settled

| Question | Decision |
|----------|----------|
| Camera app under test | Extend `:e2e-mock-camera` (real Pixel Camera refuses non-Pixel emulators). |
| Setup 3 photo source | Explicit `captureOnePhoto()` per-test in setup; no order dependence between tests. |
| Lockscreen flow | Option 2 — true secure camera via `STILL_IMAGE_CAMERA_SECURE`. Tests 4a/5a are expected to fail at baseline. |
| Color tokens | Locked: BLUE `#1565C0`, YELLOW `#FFD600`, GREEN `#00C853`. |
| Shape classification | Template-matching by IoU; brute-force scaling + position sweep against ground-truth `square`/`circle`/`squircle` masks. No pixel-counting heuristics. |
| GREEN feed | In-app render in mock camera; no emulator hardware config. |

---

## Phase 1 — `:testgallery` module

New Gradle module `:testgallery` mirroring `:e2e-mock-camera`'s structure.

- `applicationId = "com.gb4pc.testgallery"`, label `"test gallery app"`, `minSdk 26`, `compileSdk 35`, `targetSdk 35`, Kotlin.
- Single activity `LastPhotoActivity`:
    - Queries `MediaStore.Images.Media.EXTERNAL_CONTENT_URI` ordered `DATE_ADDED DESC LIMIT 1`.
    - Full-bleed `ImageView`, `scaleType = "centerCrop"`.
    - Empty state: solid black background (so a "no GREEN" assertion isn't accidentally satisfied by transparent or uninitialized rendering).
- `READ_MEDIA_IMAGES` permission declared in manifest; granted via `appops` in the gradle task.
- Adaptive icon (`res/mipmap-anydpi-v26/ic_launcher.xml`):
    - `ic_launcher_background.xml`: solid `#FFD600`.
    - `ic_launcher_foreground.xml`: centered `#1565C0` square at ~33% of the foreground viewport (well inside the 66dp adaptive safe zone, so the system squircle mask never clips it).

## Phase 2 — Mock camera updates

- Replace headless `MockCameraActivity` with a full-bleed solid-GREEN `View`.
- Keep the existing `CameraDevice.open / close` lifecycle (driven by `onResume` / `onPause`) so `CameraManager.AvailabilityCallback` continues to fire identically.
- Add a broadcast receiver or activity-result intent handler for `captureOnePhoto()`:
    - Generates `Bitmap.createBitmap(w, h, ARGB_8888)`, fills with GREEN, compresses to JPEG.
    - Inserts into `MediaStore.Images.Media` with `DATE_TAKEN = System.currentTimeMillis()`.
    - Returns success once the row is queryable.

## Phase 3 — Visual-assertion library

`app/src/androidTest/java/com/gb4pc/e2e/visual/`:

### `Rgb.kt`

```kotlin
data class Rgb(val r: Int, val g: Int, val b: Int) {
    companion object {
        val BLUE   = Rgb(0x15, 0x65, 0xC0)
        val YELLOW = Rgb(0xFF, 0xD6, 0x00)
        val GREEN  = Rgb(0x00, 0xC8, 0x53)
    }
}
```

### `BinaryMask.kt`

```kotlin
data class BinaryMask(
    val bits: BooleanArray,   // row-major, length = width * height
    val width: Int,
    val height: Int,
    val bbox: Rect,           // tight bbox of true pixels; Rect(0,0,0,0) if empty
    val centroid: PointF,     // mean (x, y) of true pixels in image coords
    val pixelCount: Int
)
```

### `ColorMatch.kt`

```kotlin
object ColorMatch {

    // Per-channel RGB distance. Per-channel (not Euclidean) because anti-aliased
    // edges and PNG round-tripping shift channels near-independently, and
    // per-channel gates are easier to reason about. Default tolerance 20 is
    // calibrated to the BLUE/YELLOW/GREEN palette + takeScreenshot() PNG noise.
    fun matches(pixel: Int, target: Rgb, tolerance: Int = 20): Boolean

    // Single-pass scan: populates bbox, centroid, and pixelCount.
    fun mask(bmp: Bitmap, target: Rgb, tolerance: Int = 20): BinaryMask

    fun coverageFraction(mask: BinaryMask): Float
    fun coverageFraction(mask: BinaryMask, region: Rect): Float

    fun crop(mask: BinaryMask): BinaryMask                // tight-crop to bbox
    fun union(a: BinaryMask, b: BinaryMask): BinaryMask   // for outer-silhouette tests
}
```

Rationale:

- **Per-channel, not Euclidean**: cheaper in the hot loop (3 comparisons vs sqrt) and matches how anti-aliasing perturbs colors. Identical mask output to Euclidean for this palette at tolerance 20.
- **Tolerance default 20**: anti-alias noise ≤15, PNG round-trip ≤3, margin ~2; nearest-channel separation in the palette is ~25.
- **`union`**: needed for Test 1c, where the outer icon silhouette is the union of BLUE and YELLOW pixels (the YELLOW alone is a frame around the BLUE square, not a squircle).
- **`centroid` stored separately from `bbox.center`**: centroid is robust to one-pixel anti-aliased fuzz on bbox edges; bbox center isn't. Both computed in the same single pass.

### `ShapeTemplates.kt`

Generates ground-truth `BinaryMask`s at arbitrary `(w, h)`:

- `square(w, h)`: all pixels true.
- `circle(w, h)`: pixels inside the inscribed ellipse.
- `squircle(w, h)`: superellipse `|2x/w − 1|^n + |2y/h − 1|^n ≤ 1` with `n = 4`. Approximates Android's adaptive-icon mask; will be calibrated against the actual rendered mask in unit tests.

### `ShapeMatcher.kt`

```kotlin
sealed class Shape { object SQUARE : Shape(); object CIRCLE : Shape(); object SQUIRCLE : Shape() }

data class ClassifyResult(val winner: Shape, val winnerIoU: Float, val runnerUpIoU: Float)

object ShapeMatcher {
    fun classify(candidate: BinaryMask): ClassifyResult
    fun requireShape(candidate: BinaryMask, expected: Shape,
                     minWinnerIoU: Float = 0.92f, minMargin: Float = 0.05f)
}
```

Classification:

1. Tight-crop `candidate` to its bbox.
2. For each template ∈ `{SQUARE, CIRCLE, SQUIRCLE}`:
    - Position sweep: template offset by `(dx, dy) ∈ [−8, +8]²`.
    - Scale sweep: template rendered at `(w ± 3, h ± 3)`.
    - Take max IoU over the full sweep.
3. Winner = argmax IoU.
4. `requireShape` gates:
    - **Sanity**: `winnerIoU ≥ 0.92`. Anything lower means rendering broke; fail loudly.
    - **Margin**: `winnerIoU − runnerUpIoU ≥ 0.05`. Shapes must be clearly distinguishable. A true squircle scores ~0.95 vs ~0.91 for the runners-up; a misrendered shape collapses that margin.
    - **Identity**: `winner == expected`.

Thresholds are tunable post-implementation: the unit tests (next) tell us what margins the actual templates produce on synthetic inputs.

### Unit tests for `ShapeMatcher`

`app/src/test/java/com/gb4pc/e2e/visual/ShapeMatcherTest.kt`:

- Synthetic square/circle/squircle bitmaps at 64×64, 128×128, 256×256.
- Asserts each one classifies as itself with `winnerIoU ≥ 0.95` and margin `≥ 0.05`.
- Asserts a squircle does **not** classify as a square or a circle.
- Asserts an anti-aliased input (Gaussian-blurred 1px) still classifies correctly — guards against the screenshot path's edge softening.

## Phase 4 — `E2EFixture` extensions

`app/src/androidTest/java/com/gb4pc/e2e/E2EFixture.kt` gains:

- `seedGalleryPrefs(pkg: String)` — sets `PrefsManager.galleryPackage = pkg`, `isSetupCompleted = true`, `isServiceEnabled = true`. Other prefs untouched.
- `clearCameraRoll()` — deletes all rows in `MediaStore.Images.Media`; belt-and-braces `adb shell rm /sdcard/DCIM/Camera/* /sdcard/Pictures/*`; asserts subsequent query returns 0 rows.
- `captureOnePhoto()` — triggers the mock camera's synthetic-photo path (Phase 2); polls `MediaStore` until row count increments or 10s timeout.
- `tapOverlay()` — reads active `OverlayPosition` from `PrefsManager`, computes pixel coordinates against `WindowMetrics`, calls `UiDevice.click(x, y)`.
- `lockScreen()` — `adb shell input keyevent 26`; polls `KeyguardManager.isKeyguardLocked` until true.
- `launchSecureCamera()` — `am start -a android.media.action.STILL_IMAGE_CAMERA_SECURE`. Does **not** dismiss keyguard — the secure intent is the whole point of Tests 4a/5a.
- `pause(ms: Long)` — explicit helper for the spec's "pause 1 second" steps.

`Screenshot.kt` helpers (Phase 3 lib) used here:

- `captureScreen(): Bitmap` via `InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot()`.
- `saveForArtifact(bmp, name)` writes PNGs to `/sdcard/Android/data/com.gb4pc/files/screenshots/` for CI artifact pickup on failure.

## Phase 5 — `GalleryButtonVisualE2ETest`

`app/src/androidTest/java/com/gb4pc/e2e/GalleryButtonVisualE2ETest.kt`.

Each test fully sets its own state in `@Before` or inline — no order dependence between tests.

| # | Test | Per-test setup | Screen capture | Assertion |
|---|------|----------------|----------------|-----------|
| 1a | `test1a_overlayShowsBlueAtConfiguredPosition` | seed prefs, clear roll | launch mock camera, pause 1s, screenshot | `mask(BLUE).centroid` within `±sizePercent/2` of `(xPercent, yPercent)` |
| 1b | `test1b_overlayBlueIsSquare` | as 1a | as 1a | `requireShape(mask(BLUE), SQUARE)` |
| 1c | `test1c_overlayOuterIsSquircle` | as 1a | as 1a | `requireShape(union(mask(BLUE), mask(YELLOW)), SQUIRCLE)` |
| 2a | `test2a_emptyGalleryNoGreenAfterTap` | clear roll | launch, pause 1s, S1, tap overlay, pause 1s, S2 | `coverage(S2, GREEN) < 10%` |
| 3a | `test3a_populatedGalleryShowsGreenAfterTap` | clear roll, then `captureOnePhoto()` | launch, pause 1s, S1, tap, pause 1s, S2 | `coverage(S2, GREEN) > 40%` |
| 4a | `test4a_secureCameraLockedEmptyGalleryNoGreen` ⚠ | `lockScreen()`, clear roll | `launchSecureCamera()`, pause 1s, S1, tap, pause 1s, S2 | as 2a |
| 5a | `test5a_secureCameraLockedPopulatedGalleryShowsGreen` ⚠ | `captureOnePhoto()` (before lock), `lockScreen()` | `launchSecureCamera()`, pause 1s, S1, tap, pause 1s, S2 | as 3a |

⚠ = expected to fail at baseline (red-light test for the secure-camera-overlay regression).

Smoke test `test0_smokeGreenFeedVisible` runs first (alphabetic ordering):

- Launches mock camera, pause 1s, screenshot.
- Asserts `coverage(GREEN) > 70%` in the central 60% of the screen (excluding status/nav bars).
- If this fails, the entire visual harness is broken — investigate before trusting any test 1-5 result.

All tests `saveForArtifact` every screenshot and every binary mask used in classification, so a CI failure ships reviewable PNGs.

Test ordering enforced via `@FixMethodOrder(MethodSorters.NAME_ASCENDING)`. Serial execution within the class (no parallel) so device state is deterministic.

## Phase 6 — Build & CI wiring

- `settings.gradle.kts`: `include(":testgallery")`.
- `app/build.gradle.kts`'s `connectedE2EAndroidTest` task:
    - Build and install `:testgallery` (in addition to `:app` and `:e2e-mock-camera`).
    - Grant `:testgallery` `READ_MEDIA_IMAGES` via `appops`.
    - Grant `:e2e-mock-camera` `WRITE_EXTERNAL_STORAGE` / `MANAGE_EXTERNAL_STORAGE` as needed for the synthetic-photo write.
    - Test package filter `com.gb4pc.e2e` already covers `GalleryButtonVisualE2ETest`; no change there.
- `.github/workflows/build.yml`: upload `/sdcard/Android/data/com.gb4pc/files/screenshots/` as a CI artifact on failure.

No emulator flag changes required (since GREEN is rendered in-app).

## Phase 7 — Commit organization

Per `agents/code_edit.md`, split by concern, not by file:

1. `:testgallery` module skeleton + adaptive icon assets.
2. `LastPhotoActivity` + MediaStore query.
3. Mock-camera GREEN viewfinder + synthetic-photo capture.
4. Visual library (`Screenshot` / `ColorMatch` / `ShapeTemplates` / `ShapeMatcher`) + unit tests for the matcher.
5. `E2EFixture` extensions (clear roll, capture, tap, lock, secure-camera launch).
6. `GalleryButtonVisualE2ETest` + gradle task wiring + CI artifact upload.

Each commit builds and runs its own tests locally before being committed.

## Risks and known red lights

- **Tests 4a / 5a fail at baseline**. They are intentional regression markers. Do **not** quarantine, ignore, or skip them — they must remain visible reds until the secure-camera overlay path is fixed in a separate PR.
- **Anti-aliasing on small icons**. The icon thumbnail is ~170px wide at default size on a 1080p emulator. Edge anti-aliasing eats 1-3px on each side. Mitigations: (a) BLUE square is centered in the icon foreground viewport, so edges are well clear of the squircle mask; (b) `ShapeMatcher`'s scale sweep `±3 px` absorbs this; (c) unit tests use Gaussian-blurred inputs to confirm robustness.
- **System UI chrome leaking into screenshots**. Status bar / nav bar can include colors close to YELLOW/GREEN on some themes. Mitigation: visual tests inspect only the central 60% of the screen unless otherwise noted; Test 1a uses the configured overlay region.
- **MediaStore latency**. `captureOnePhoto()` writes to MediaStore but the row may not be queryable instantly. Mitigation: poll up to 10s with 100ms intervals. If this remains flaky, fall back to `MediaScannerConnection.scanFile` to force-publish.

---

## Appendix A — Alternative GREEN feed (virtualscene posters)

Not implemented in this plan. Documented in case a future test needs to exercise the real camera-hardware path.

1. Check in `.github/emulator/green.png` (solid `#00C853`, 1024×1024).
2. In CI workflow, after `emulator -avd …` boots, pass `-virtualscene-poster wall=…/green.png` for all four wall slots.
3. Extend `MockCameraActivity` to attach the `CameraDevice` preview session to a real `TextureView` (not a static GREEN view).
4. Add two-layer smoke check:
    - **CI pre-flight**: `adb shell am start com.gb4pc.mockcamera/.MockCameraActivity`, `adb exec-out screencap -p > preflight.png`, sample central 200×200 px and assert ≥90% match `#00C853` within tolerance 20.
    - **In-suite**: existing `test0_smokeGreenFeedVisible` works as-is.
5. Fallback if poster replacement is flaky: `v4l2loopback` device + `ffmpeg` piping `green.png`, `emulator -camera-back webcam0`.

## Appendix B — Per-test pseudocode for the trickiest cases

### Test 1a — BLUE at configured screen position

```kotlin
seedGalleryPrefs("com.gb4pc.testgallery")
clearCameraRoll()
launchMockCamera()
pause(1000)

val screen = captureScreen()
val blue = ColorMatch.mask(screen, Rgb.BLUE)
saveForArtifact(screen, "1a-screen.png")

val pos = PrefsManager(context).getOverlayPosition(currentAspectRatio())
val expectedX = pos.xPercent / 100f * screen.width
val expectedY = pos.yPercent / 100f * screen.height
val tolerancePx = (pos.sizePercent / 200f) * minOf(screen.width, screen.height)

assertThat(distance(blue.centroid, PointF(expectedX, expectedY))).isLessThan(tolerancePx)
```

### Test 1c — outer silhouette is a squircle

```kotlin
// ... same setup as 1a ...
val screen = captureScreen()
val outer = ColorMatch.union(
    ColorMatch.mask(screen, Rgb.BLUE),
    ColorMatch.mask(screen, Rgb.YELLOW)
)
saveForArtifact(maskToBitmap(outer), "1c-outer-mask.png")
ShapeMatcher.requireShape(ColorMatch.crop(outer), Shape.SQUIRCLE)
```

### Test 5a — secure-camera with populated gallery (RED at baseline)

```kotlin
seedGalleryPrefs("com.gb4pc.testgallery")
clearCameraRoll()
captureOnePhoto()        // green JPEG now in MediaStore
lockScreen()
launchSecureCamera()
pause(1000)
val s1 = captureScreen(); saveForArtifact(s1, "5a-s1.png")

tapOverlay()             // expected to be ineffective at baseline
pause(1000)
val s2 = captureScreen(); saveForArtifact(s2, "5a-s2.png")

val coverage = ColorMatch.coverageFraction(ColorMatch.mask(s2, Rgb.GREEN))
assertThat(coverage).isGreaterThan(0.40f)
```
