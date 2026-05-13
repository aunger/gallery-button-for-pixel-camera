# Plan: E2E visual tests

## Goal

Add a visually-evaluated emulator test suite that verifies GB4PC's overlay button:

1. Renders the configured gallery app's adaptive icon at the configured screen position (Tests 1a/1b/1c).
2. Opens the configured gallery app on tap, surfacing the most recent photo (Tests 2a/3a).
3. Behaves correctly when launched into Android's secure-camera mode from a locked screen (Tests 4a/5a).

Test 5a is a deliberate **red-light test for a known regression** — it will fail until the secure-camera overlay path is restored. Test 4a passes at baseline regardless of the regression (see Phase 5 for explanation).

## Color palette

Locked in up front so visual assertions can be calibrated against a known palette:

| Token  | RGB       | Used for |
|--------|-----------|----------|
| BLUE   | `#1565C0` | Test gallery icon foreground (small square) |
| YELLOW | `#FFD600` | Test gallery icon background |
| GREEN  | `#00C853` | Camera viewfinder + captured photo content |

Choices: mutually distinct in all three channels (nearest separation ~25 in one channel), far from Pixel Camera UI chrome (white/grey/black with brand-red accents), comfortable margin against a per-channel tolerance of 20.

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

## Phase 2 — Mock camera and GREEN feed

The emulator's virtual scene renderer is used to produce a solid-GREEN camera feed, giving the tests a real camera-hardware path without requiring a physical device or the real Pixel Camera app.

### Why the mock camera triggers GB4PC's overlay

`MockCameraActivity` has `applicationId = "com.google.android.GoogleCamera"` (see `e2e-mock-camera/build.gradle.kts`). GB4PC's `ForegroundDetector` compares the `UsageStatsManager` foreground package name against `Constants.PIXEL_CAMERA_PACKAGE = "com.google.android.GoogleCamera"`. When `MockCameraActivity` is in the foreground, this check returns `true` and the overlay activates. No changes to `ForegroundDetector` or `Constants` are needed.

### Emulator setup

Check in `.github/emulator/green.png` (solid `#00C853`, 1024×1024). In `.github/workflows/build.yml`, append `-virtualscene-poster` flags to the **existing emulator launch command** in the "Start emulator" step (these are startup parameters, not post-boot configuration):

```
$ANDROID_HOME/emulator/emulator -avd e2e_avd -no-window -no-audio -no-boot-anim \
  -gpu swiftshader_indirect \
  -virtualscene-poster wall0=.github/emulator/green.png \
  -virtualscene-poster wall1=.github/emulator/green.png \
  -virtualscene-poster wall2=.github/emulator/green.png \
  -virtualscene-poster wall3=.github/emulator/green.png
```

**Caveat**: the emulator's virtual-scene camera renderer uses OpenGL internally. Compatibility with `-gpu swiftshader_indirect` (software rendering) is not guaranteed and must be confirmed during implementation. If the virtualscene camera produces no output under swiftshader, fall back to Alternative 1 or 2 (see Appendix A). The two-layer smoke check (below) will surface this failure immediately.

### MockCameraActivity updates

Extend `MockCameraActivity` to render a real camera preview and support photo capture:

- Add a full-bleed `TextureView`. Wire the `CameraDevice` preview session to its surface so the GREEN virtual-scene feed is visible on screen.
- Add an `ImageReader` alongside the `TextureView` surface so a shutter trigger can capture a frame. The captured JPEG will contain GREEN pixels because the camera feed is GREEN.
- Keep the existing `CameraDevice.open / close` lifecycle (`onResume` / `onPause`) intact so `CameraManager.AvailabilityCallback` continues to fire identically.
- Expose a shutter path (broadcast receiver or activity-result handler) that triggers an `ImageReader` capture, writes the result to `MediaStore.Images.Media` with `DATE_TAKEN = System.currentTimeMillis()`, and signals completion once the row is queryable.
- For Tests 4a/5a (secure-camera mode), declare `android:showWhenLocked="true"` and `android:turnScreenOn="true"` on `MockCameraActivity` in the manifest so it can appear over the lock screen when launched via `STILL_IMAGE_CAMERA_SECURE`. Confirm during implementation that `KeyguardManager.isKeyguardLocked()` remains `true` after this launch sequence — `am start -a android.media.action.STILL_IMAGE_CAMERA_SECURE` dispatched via adb while the screen is locked is not identical to the lock-screen camera shortcut and may behave differently on the emulator.

### Smoke check

Two-layer verification that the GREEN feed is reaching the camera before any visual test is trusted:

**CI pre-flight** (in `build.yml`, before running the test suite):
1. Install the mock-camera APK.
2. `adb shell am start com.google.android.GoogleCamera/com.gb4pc.mockcamera.MockCameraActivity`, sleep 2s.
3. `adb exec-out screencap -p > preflight.png`.
4. Sample the central 200×200 px region and assert ≥90% of pixels match `#00C853` within per-channel tolerance 20 (30-line Python script or ImageMagick invocation checked into `scripts/`).
5. On failure: upload `preflight.png` as a CI artifact and fail the build with the actual dominant color so the poster path can be debugged.

**In-suite** (`test0_smokeGreenFeedVisible`, runs first by alphabetic order):
- Launches mock camera, pauses 1s, takes a screenshot.
- Asserts `coverage(GREEN) > 70%` in the central 60% of the screen (excluding status/nav bars).
- If this fails after the CI pre-flight passed, the bug is in the test harness itself.

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
- `squircle(w, h)`: superellipse `|2x/w − 1|^n + |2y/h − 1|^n ≤ 1` with `n = 4`. Approximates Android's adaptive-icon mask; calibrated against the actual rendered mask in unit tests.

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
- `clearCameraRoll()` — deletes all rows via `ContentResolver.delete(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, null, null)`; asserts subsequent query returns 0 rows. Note: on API 33+, deleting rows inserted by other packages throws `RecoverableSecurityException`; since the test suite controls all insertions this should not occur, but if it does the fixture should fail loudly rather than silently skip rows.
- `captureOnePhoto()` — triggers the mock camera's shutter path (Phase 2); polls `MediaStore` until row count increments or 10s timeout. Falls back to `MediaScannerConnection.scanFile` if the row does not appear promptly.
- `tapOverlay()` — reads active `OverlayPosition` from `PrefsManager`, computes pixel coordinates against `WindowMetrics`, calls `UiDevice.click(x, y)`. Note: if the overlay is not rendered (e.g. blocked in secure-camera mode), this call taps empty screen space and has no effect.
- `lockScreen()` — `adb shell input keyevent 26`; polls `KeyguardManager.isKeyguardLocked` until true.
- `launchSecureCamera()` — `am start -a android.media.action.STILL_IMAGE_CAMERA_SECURE`, then immediately asserts `KeyguardManager.isKeyguardLocked() == true`. If the assertion fails (i.e. the adb path silently dismissed the keyguard), the fixture throws before any screenshot is taken — a silent keyguard dismissal would make Tests 4a/5a pass for the wrong reason.
- `pause(ms: Long)` — explicit helper for the spec's "pause 1 second" steps.

`Screenshot.kt` helpers (Phase 3 lib) used here:

- `captureScreen(): Bitmap` via `InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot()`.
- `saveForArtifact(bmp, name)` writes PNGs to `/sdcard/Android/data/com.gb4pc/files/screenshots/` for CI artifact pickup on failure.

## Phase 5 — `GalleryButtonVisualE2ETest`

`app/src/androidTest/java/com/gb4pc/e2e/GalleryButtonVisualE2ETest.kt`.

Each test fully sets its own state — no order dependence between tests.

Test ordering enforced via `@FixMethodOrder(MethodSorters.NAME_ASCENDING)` (JUnit 4). Method names use the prefix scheme `test0_`, `test1a_`, `test1b_`, `test1c_`, `test2a_`, `test3a_`, `test4a_`, `test5a_` — alphabetic comparison of these prefixes produces the intended numeric order (`'0' < '1' < '2' < … < '5'`, `'a' < 'b' < 'c'`). Serial execution within the class (no parallel) so device state is deterministic.

| # | Test | Per-test setup | Screen capture | Assertion |
|---|------|----------------|----------------|-----------|
| 0 | `test0_smokeGreenFeedVisible` | — | launch mock camera, pause 1s, screenshot | `coverage(GREEN) > 70%` in central 60% of screen |
| 1a | `test1a_overlayShowsBlueAtConfiguredPosition` | seed prefs, clear roll | launch mock camera, pause 1s, screenshot | `mask(BLUE).centroid` within icon radius of configured `(xPercent, yPercent)` |
| 1b | `test1b_overlayBlueIsSquare` | as 1a | as 1a | `requireShape(mask(BLUE), SQUARE)` |
| 1c | `test1c_overlayOuterIsSquircle` | as 1a | as 1a | `requireShape(union(mask(BLUE), mask(YELLOW)), SQUIRCLE)` |
| 2a | `test2a_emptyGalleryNoGreenAfterTap` | clear roll | launch, pause 1s, S1, tap overlay, pause 1s, S2 | `coverage(S2, GREEN) < 10%` |
| 3a | `test3a_populatedGalleryShowsGreenAfterTap` | clear roll, then `captureOnePhoto()` | launch, pause 1s, S1, tap, pause 1s, S2 | `coverage(S2, GREEN) > 40%` |
| 4a | `test4a_secureCameraLockedEmptyGalleryNoGreen` | clear roll, then `lockScreen()` | `launchSecureCamera()`, pause 1s, S1, tap, pause 1s, S2 | as 2a |
| 5a | `test5a_secureCameraLockedPopulatedGalleryShowsGreen` ⚠ | `captureOnePhoto()` (before lock), `lockScreen()` | `launchSecureCamera()`, pause 1s, S1, tap, pause 1s, S2 | as 3a |

⚠ = expected to fail at baseline (red-light test for the secure-camera-overlay regression). Test 4a does **not** carry this marker: whether the regression is present or not, an empty gallery produces no GREEN after tap, so 4a passes in both states. Test 5a is the meaningful regression signal — it requires the overlay to render and be tappable in secure-camera mode, then verifies the gallery opens and shows the captured GREEN photo.

All tests `saveForArtifact` every screenshot and every binary mask used in classification, so a CI failure ships reviewable PNGs.

## Phase 6 — Build & CI wiring

- `settings.gradle.kts`: `include(":testgallery")`.
- `app/build.gradle.kts`'s `connectedE2EAndroidTest` task:
    - Build and install `:testgallery` (in addition to `:app` and `:e2e-mock-camera`).
    - Grant `:testgallery` `READ_MEDIA_IMAGES` via `appops`.
    - No additional runtime permission is needed for `:e2e-mock-camera` to insert into `MediaStore.Images.Media` — on API 29+, any app may insert its own media via `ContentResolver` without a runtime permission grant.
    - Test package filter `com.gb4pc.e2e` already covers `GalleryButtonVisualE2ETest`; no change there.
- `.github/workflows/build.yml`:
    - Append `-virtualscene-poster` flags to the existing emulator launch command (see Phase 2).
    - Run the CI pre-flight smoke check (see Phase 2) before starting the instrumented test run.
    - Upload `/sdcard/Android/data/com.gb4pc/files/screenshots/` as a CI artifact on failure.

## Phase 7 — Commit organization

Per `agents/code_edit.md`, split by concern, not by file:

1. `:testgallery` module skeleton + adaptive icon assets.
2. `LastPhotoActivity` + MediaStore query.
3. Mock-camera `TextureView` preview, `ImageReader` capture, shutter path, and `showWhenLocked`/`turnScreenOn` manifest flags; `.github/emulator/green.png`.
4. Visual library (`Screenshot` / `ColorMatch` / `ShapeTemplates` / `ShapeMatcher`) + unit tests for the matcher.
5. `E2EFixture` extensions (clear roll, capture, tap, lock, secure-camera launch).
6. `GalleryButtonVisualE2ETest` + gradle task wiring + CI pre-flight script + artifact upload.

Each commit builds and runs its own tests locally before being committed.

## Risks and known red lights

- **Test 5a fails at baseline**. It is an intentional regression marker. Do **not** quarantine, ignore, or skip it — it must remain a visible red until the secure-camera overlay path is fixed in a separate PR. Link the tracking issue in the test's `@Test` comment when implementing.
- **Virtualscene + swiftshader compatibility**. The emulator's virtualscene camera renderer may not produce output when `-gpu swiftshader_indirect` is active. The two-layer smoke check (CI pre-flight + `test0`) will surface this immediately; if it fires, adopt Alternative 1 or 2 from Appendix A.
- **Secure-camera launch on the emulator**. `am start -a android.media.action.STILL_IMAGE_CAMERA_SECURE` via adb is not the same as triggering the lock-screen camera shortcut. Confirm during implementation that the keyguard stays locked and the activity appears above it. If the emulator does not honor this flow, Tests 4a/5a cannot be run until a workaround is found (e.g. UI Automator gestures to trigger the shortcut).
- **Anti-aliasing on small icons**. The icon thumbnail is ~170px wide at default size on a 1080p emulator. Edge anti-aliasing eats 1-3px on each side. Mitigations: (a) BLUE square is centered in the icon foreground viewport, so edges are well clear of the squircle mask; (b) `ShapeMatcher`'s scale sweep `±3 px` absorbs this; (c) unit tests use Gaussian-blurred inputs to confirm robustness.
- **System UI chrome leaking into screenshots**. Status bar / nav bar can include colors close to YELLOW/GREEN on some themes. Mitigation: visual tests inspect only the central 60% of the screen unless otherwise noted; Test 1a uses the configured overlay region.
- **MediaStore latency**. `captureOnePhoto()` writes to MediaStore but the row may not be queryable instantly. Mitigation: poll up to 10s with 100ms intervals; fall back to `MediaScannerConnection.scanFile` to force-publish.

---

## Appendix A — Alternative GREEN feed approaches

The primary approach (virtualscene posters, Phase 2) uses the emulator's real camera-hardware path, which is the most faithful test of the full stack. Two alternatives are documented here if the poster approach proves unworkable (e.g. swiftshader incompatibility, see Risks above).

### Alternative 1 — In-app mock (no emulator config)

`MockCameraActivity` renders a solid-GREEN full-bleed `View` instead of a real camera preview. On shutter, it writes a synthetic GREEN JPEG (`Bitmap.createBitmap` filled with `#00C853`, `compress(JPEG, 100)`, `MediaStore.Images.Media.insert`) rather than capturing from the camera session.

**Pros:**
- Zero emulator configuration — no CI flag changes, no pre-flight check, no swiftshader risk.
- Fully deterministic; no dependency on emulator SDK version or virtual-scene rendering.
- Simpler `MockCameraActivity` (no `TextureView`, no `ImageReader`).

**Cons:**
- The camera-hardware → camera-preview → capture path is bypassed entirely. Tests verify GB4PC's overlay and gallery integration but not the camera plumbing.
- A regression in the emulator's camera support would go undetected.

To adopt: replace Phase 2's emulator setup and `TextureView`/`ImageReader` work with "render a solid-GREEN `View`, write a synthetic JPEG on shutter"; remove the emulator startup flags from Phase 6; delete the CI pre-flight script.

### Alternative 2 — v4l2loopback virtual webcam

Use a kernel loopback video device to pipe a solid-GREEN image into the emulator as a real webcam feed:

1. `apt-get install v4l2loopback-dkms ffmpeg` on the CI runner.
2. `modprobe v4l2loopback devices=1`.
3. `ffmpeg -loop 1 -i .github/emulator/green.png -f v4l2 /dev/video0 &` to continuously feed frames.
4. Boot the AVD with `-camera-back webcam0` instead of `-virtualscene-poster`.

**Pros:**
- Exercises the real camera-hardware path, same as the primary approach.
- Does not depend on the virtual-scene renderer; sidesteps the swiftshader compatibility question.

**Cons:**
- Requires kernel module (`v4l2loopback`) and `ffmpeg` on the CI runner — additional dependencies, potential breakage on runner OS upgrades.
- `modprobe` requires elevated privileges; may not be available on all CI environments.
- More moving parts than the virtualscene-poster approach.

To adopt: replace the `-virtualscene-poster` flags in Phase 6 with the `v4l2loopback` setup above; keep `MockCameraActivity`'s `TextureView` preview and `ImageReader` unchanged.

---

## Appendix B — Per-test pseudocode for the trickiest cases

### Test 1a — BLUE at configured screen position

`OverlayPosition.sizePercent` is a percentage of `min(displayWidth, displayHeight)` (see `OverlayManager.calculateOverlaySizePx`). The tolerance formula below therefore yields half the icon's pixel diameter — i.e., the icon radius — which is the right slack for a centroid-based position check: the centroid of the BLUE region should land within one radius of wherever the overlay is configured to sit.

Confirm during implementation whether `(xPercent, yPercent)` refers to the icon's center or its top-left corner. If it is the top-left corner, shift the expected coordinates by half the icon size: `expectedX += iconRadiusPx`, `expectedY += iconRadiusPx`. Do not widen the tolerance to compensate — that would mask a mis-positioned overlay.

```kotlin
seedGalleryPrefs("com.gb4pc.testgallery")
clearCameraRoll()
launchMockCamera()
pause(1000)

val screen = captureScreen()
val blue = ColorMatch.mask(screen, Rgb.BLUE)
saveForArtifact(screen, "1a-screen.png")

val pos = PrefsManager(context).getOverlayPosition(currentAspectRatio())
val minDim = minOf(screen.width, screen.height)
val iconRadiusPx = (pos.sizePercent / 200f) * minDim  // half of icon size in px
val expectedX = pos.xPercent / 100f * screen.width
val expectedY = pos.yPercent / 100f * screen.height

assertThat(distance(blue.centroid, PointF(expectedX, expectedY))).isLessThan(iconRadiusPx)
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

tapOverlay()             // taps overlay position; no-op at baseline (overlay blocked)
pause(1000)
val s2 = captureScreen(); saveForArtifact(s2, "5a-s2.png")

val coverage = ColorMatch.coverageFraction(ColorMatch.mask(s2, Rgb.GREEN))
assertThat(coverage).isGreaterThan(0.40f)
```
