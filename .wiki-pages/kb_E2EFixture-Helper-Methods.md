E2E test fixtures provide helper methods that abstract away platform-specific details and make tests more readable.

## Core Helpers

### `seedGalleryPrefs()`
Initialize gallery app preferences to a known state before tests run.

**Purpose:** Ensure consistent starting state (no previous test artifacts affecting new tests)

### `clearCameraRoll()`
Delete all photos from the device's camera roll / photo library.

**Purpose:** Provide blank slate for photo tests

### `captureOnePhoto()`
Trigger a single photo capture and wait for completion.

**Implementation details:**
- Send capture intent or button tap
- **Timeout:** 15 seconds (handles slow emulators)
- **Fallback:** Use `MediaScannerConnection.scanFile()` to ensure MediaStore index is updated
- Return: URI of captured photo

**Why the timeout & fallback:** Photo capture is asynchronous. The file may be written to disk, but MediaStore indexing happens separately. Without the MediaScannerConnection fallback, the photo might not appear in queries.

### `tapOverlay(x, y)` — Screen Coordinate Translation
Tap at absolute screen coordinates, handling API level differences.

**Challenge:** Screen dimensions and DPI differ between API 26-29 and API 30+.

**Solution:**
- **API 30+:** Use `WindowMetrics` (system window service API)
- **API 26-29:** Use `DisplayMetrics` (older API)

Code handles both gracefully, reading the actual screen dimensions at runtime.

### `lockScreen()`
Engage the lock screen (keyguard).

**Implementation:**
- Send `KEYCODE_SLEEP` to turn display off
- Assert keyguard state before returning
- Fail fast if keyguard doesn't engage (better than hanging)

### `launchSecureCamera()`
Launch the secure camera app and wait for it to be ready.

**Pattern:**
- Send intent
- Check for keyguard (secure camera often inherits lock screen)
- Verify app is visible

### `pause(millis)`
Sleep for a given duration.

**Use case:** Wait for animation, compose, or async operation to complete

## Cross-Module Action Strings

When using `Intent.setAction()` with custom action strings, be careful about **module boundaries**:

The string `ACTION_SHUTTER` might be defined in the camera module, but used from the test module. Both modules must see the same constant string value. If you hardcode the string in both places, mismatches become subtle bugs.

**Solution:** Define action constants in a shared location (e.g., `test-shared/AndroidManifest.xml` or a shared resources module) and reference them by constant.

---

**Source:** [PR #166 — E2EFixture Extensions Implementation](https://github.com/aunger/gallery-button-for-pixel-camera/pull/166)
