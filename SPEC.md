# GB4PC — Gallery Button for Pixel Camera

## Expanded Requirements v1.0

-----

## 1. Overview

GB4PC overlays the Pixel Camera’s hardcoded Google Photos button with the launcher icon of the user’s preferred gallery app. Tapping the overlay launches that gallery app. The overlay appears when Pixel Camera’s viewfinder is in the foreground and disappears when it is not.

- **OV-01** The app manages a single association: one trigger condition (Pixel Camera viewfinder active) mapped to one overlay (the user’s chosen gallery app icon).
- **OV-02** Target: Android 8.0+ (API 26). Kotlin, Jetpack Compose for settings UI.
- **OV-03** Distribution: sideload / F-Droid. Play Store compatible.
- **OV-04** The Pixel Camera package name is `com.google.android.GoogleCamera`. If this package is not installed, the main settings screen displays a notice and the service refuses to start.

-----

## 2. Permissions & Setup

### 2.1 Required Permissions

|Permission                            |Type                      |Purpose                                                                                          |
|--------------------------------------|--------------------------|-------------------------------------------------------------------------------------------------|
|`PACKAGE_USAGE_STATS`                 |Special — Settings toggle |Confirm foreground app identity via `UsageStatsManager` when a camera-availability callback fires|
|`SYSTEM_ALERT_WINDOW`                 |Special — Settings toggle |Draw the overlay icon on top of Pixel Camera                                                     |
|`FOREGROUND_SERVICE`                  |Normal (manifest)         |Keep the process alive so camera-availability callbacks remain registered                        |
|`FOREGROUND_SERVICE_SPECIAL_USE`      |Normal (manifest, API 34+)|Foreground service type declaration                                                              |
|`POST_NOTIFICATIONS`                  |Runtime (API 33+)         |Show required foreground service notification                                                    |
|`RECEIVE_BOOT_COMPLETED`              |Normal (manifest)         |Restart service after device reboot                                                              |
|`CAMERA`                              |Normal (manifest)         |Required to register `CameraManager.AvailabilityCallback`                                        |
|`REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`|Normal (manifest)         |Prompt user to exclude GB4PC from battery optimization                                           |

Note: `QUERY_ALL_PACKAGES` is not required. Instead, the manifest declares a `<queries>` element to satisfy Android’s package visibility filtering (API 30+):

```xml
<queries>
    <package android:name="com.google.android.GoogleCamera" />
    <intent>
        <action android:name="android.intent.action.MAIN" />
        <category android:name="android.intent.category.LAUNCHER" />
    </intent>
</queries>
```

The first entry allows `PackageManager.getPackageInfo()` to see Pixel Camera (required for installation detection in OV-04 and UI-03). The second entry allows the gallery app picker to enumerate all apps with a launcher icon (required for UI-05). This is a compile-time manifest declaration, not a runtime permission — the user is never prompted.

### 2.2 Guided Setup Flow

- **PM-01** On first launch, display a guided setup flow with three steps. Each step explains one permission, why it is needed, and provides a single button to grant it. Steps:
1. **Usage Access** — “GB4PC needs to confirm which app is using the camera. This permission lets GB4PC see which app is in the foreground. It cannot read your personal data.” Button: “Grant Usage Access” → opens the system Usage Access settings screen.
1. **Draw Over Apps** — “Allows GB4PC to show the gallery button on top of Pixel Camera.” Button: “Grant Overlay Permission” → opens the system overlay settings screen.
1. **Battery Optimization** — “GB4PC needs to stay running in the background to detect when you open the camera. Excluding it from battery optimization prevents Android from killing it.” Button: “Exclude from Battery Optimization” → fires `ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` intent.
- **PM-02** Each step shows a checkmark and auto-advances when the permission is detected as granted (on `onResume`). The user can also tap a “Skip” link to defer, but a persistent banner on the main settings screen indicates any missing permission with a tap-to-fix action.
- **PM-03** If Usage Access is revoked while the service is running, the service continues running (to maintain camera callbacks) but cannot confirm foreground app identity. It hides any visible overlay, posts a notification (“GB4PC cannot detect apps — tap to fix”), and the main settings screen shows the missing-permission banner.
- **PM-04** If Overlay permission is revoked while the service is running, the service detects the failure when it next attempts to show the overlay, posts a notification prompting re-grant, and the main settings screen shows the missing-permission banner.
- **PM-05** If the `POST_NOTIFICATIONS` runtime permission (API 33+) has not been granted, request it at the beginning of the setup flow before step 1, since the foreground service notification requires it.

-----

## 3. Foreground Detection

### 3.1 Camera Availability Callback (Primary Mechanism)

- **DT-01** On service start, register a `CameraManager.AvailabilityCallback` for all camera IDs reported by `CameraManager.getCameraIdList()`.
- **DT-02** When `onCameraUnavailable(cameraId)` fires (meaning an app has acquired the camera hardware), perform a single `UsageStatsManager.queryEvents()` call covering the last 5 seconds to identify the foreground app’s package name.
- **DT-03** If the foreground package is `com.google.android.GoogleCamera`, activate the overlay. If it is any other package, do nothing.
- **DT-04** When `onCameraAvailable(cameraId)` fires (meaning the camera hardware has been released), deactivate the overlay — but only after a **500ms debounce delay**. If `onCameraUnavailable` fires again for any camera ID within that 500ms window (as happens during front/back camera switching), cancel the pending deactivation and re-evaluate per DT-02/DT-03. This prevents overlay flicker during camera switches.
- **DT-05** If `onCameraAvailable` fires for one camera ID but another camera ID is still unavailable, do not deactivate. Only deactivate when all camera IDs are available (i.e., no camera hardware is held by any app).
- **DT-06** The `UsageStatsManager` query in DT-02 requires the `PACKAGE_USAGE_STATS` permission. If this permission is not granted, the callback still fires but the service cannot confirm the foreground app. In this case, the service does not show the overlay and follows PM-03 behavior.

### 3.2 Process Lifetime

- **DT-07** A foreground service keeps the process alive so that camera-availability callbacks remain registered. Without it, Android may kill the process and callbacks will stop firing.
- **DT-08** The service must survive doze mode. The setup flow prompts battery optimization exclusion (PM-01 step 3). If the user has not excluded GB4PC from battery optimization, the main settings screen shows a warning banner.

-----

## 4. Overlay

### 4.1 Appearance

- **WG-01** The overlay displays the selected gallery app’s adaptive icon, extracted live from `PackageManager.getApplicationIcon()` each time the overlay is shown (not cached as a bitmap), so it stays current if the gallery app updates its icon.
- **WG-02** The icon is rendered with the system’s adaptive icon shape mask (round, squircle, etc., matching the device’s icon shape) to visually blend with Pixel Camera’s UI.
- **WG-03** The overlay is a `WindowManager` view with type `TYPE_APPLICATION_OVERLAY`, using `SYSTEM_ALERT_WINDOW` permission.

### 4.2 Positioning & Sizing

- **PS-01** Overlay position is stored as three normalized values:
  - `x%` (0.00–100.00, left-to-right) — horizontal center of the overlay
  - `y%` (0.00–100.00, top-to-bottom) — vertical center of the overlay
  - `size%` — the percentage of `min(displayWidth, displayHeight)` that the overlay occupies
- **PS-02** The app ships with a hardcoded default position tuned for the Pixel Camera viewfinder’s Google Photos button location. The initial default is `x=13.0, y=91.5, size=11.5`. These values are derived from the Pixel Camera layout on Pixel 6/7/8/9 devices in portrait orientation and may need adjustment during development/testing.
- **PS-03** Position values are stored per display aspect ratio, quantized to two decimal places (e.g., 0.46 for a 1080×2400 portrait display). Each distinct ratio stores its own `x%`, `y%`, and `size%`.
- **PS-04** If the current display ratio has no stored position but other ratios do, the position from the numerically closest ratio is used.
- **PS-05** There is no drag-to-reposition. Users adjust position only through the Advanced Settings screen (see §6.3).

### 4.3 Tap Action

- **AC-01** **Device unlocked:** Tapping the overlay launches the user’s selected gallery app via `PackageManager.getLaunchIntentForPackage()`.
- **AC-02** **Device locked:** Tapping the overlay opens the built-in secure filmstrip viewer (see §5). It does not launch the external gallery app.
- **AC-03** **No gallery app configured:** If no gallery app has been selected (including on very first use), tapping the overlay while the device is unlocked presents the gallery app picker (see §6.2). While locked, it shows a toast: “Unlock to set up your gallery app.”
- **AC-04** **Gallery app uninstalled:** If the selected gallery app is no longer installed, the overlay displays a generic placeholder icon with a small warning badge. Tapping while unlocked presents the gallery app picker. Tapping while locked shows a toast: “Gallery app not found — unlock to choose a new one.”

### 4.4 Lock State Detection

- **AC-05** Lock state is determined via `KeyguardManager.isKeyguardLocked()`. This returns `true` when the device is locked (even if the screen is on and showing the lock screen or a secure camera session), and `false` when the device is fully unlocked.

-----

## 5. Secure Filmstrip Viewer

When the device is locked and the user taps the overlay, GB4PC displays a built-in photo viewer that shows only photos captured during the current camera session. This mirrors the behavior of AOSP’s secure camera implementation.

### 5.1 Session Tracking

- **SF-01** A “camera session” begins when the overlay is activated (Pixel Camera acquires the camera while in the foreground) and the device is locked. It ends when either: (a) the overlay is deactivated (Pixel Camera releases the camera or leaves the foreground), or (b) the device is unlocked.
- **SF-02** On session start, record the session start timestamp.
- **SF-03** Register a `ContentObserver` on `MediaStore.Images.Media.EXTERNAL_CONTENT_URI` and `MediaStore.Video.Media.EXTERNAL_CONTENT_URI` to detect new media captured during the session.
- **SF-04** A media item belongs to the current session if all of the following are true:
  - Its `DATE_ADDED` (or `DATE_TAKEN` if available) is ≥ the session start timestamp minus a 2-second tolerance (to account for filesystem/MediaStore timing differences).
  - Its `RELATIVE_PATH` starts with `DCIM/Camera/` (the standard path for camera-captured media on Pixel devices).
- **SF-05** The session media list is maintained in memory. It is never persisted to disk. When the session ends, the list is cleared.

### 5.2 Viewer UI

- **SF-06** The viewer is an `Activity` with `setShowWhenLocked(true)` and `setTurnScreenOn(true)`, allowing it to display on top of the lock screen without requiring unlock.
- **SF-07** The viewer displays session photos in a horizontal `ViewPager2`, most recent first (swipe left for older).
- **SF-08** **Pinch-to-zoom** is supported on individual photos using a gesture-enabled `ImageView` (e.g., a `SubsamplingScaleImageView` or equivalent library that supports large images without OOM).
- **SF-09** **Videos** are displayed as a still frame (first frame or a representative thumbnail via `MediaMetadataRetriever.getFrameAtTime()`). A play button icon is overlaid on the thumbnail. Tapping a video item shows a toast: “Unlock to play video” (since launching a full video player from the lock screen would bypass security).
- **SF-10** **Swipe-to-delete:** Swiping a photo vertically (up or down) initiates a delete gesture. The photo animates away and a confirmation snackbar appears with an “Undo” action (5-second timeout). If not undone, the photo is deleted from `MediaStore`. Deleted items are removed from the session list.
- **SF-11** **Share button:** A share icon is displayed in the viewer toolbar. Tapping it triggers `KeyguardManager.requestDismissKeyguard()`, which prompts the user to authenticate (PIN/pattern/fingerprint). On successful unlock, a standard `ACTION_SEND` share sheet is launched with the current photo’s URI. On authentication failure or cancellation, nothing happens.
- **SF-12** If the session has no photos yet, the viewer shows a centered message: “No photos yet — take a picture!”
- **SF-13** Pressing the back button or swiping the viewer away returns to Pixel Camera (the viewer activity finishes). The overlay remains visible.

### 5.3 Edge Cases

- **SF-14** If a photo is deleted externally (e.g., by Pixel Camera’s own review UI) while the viewer is open, the `ContentObserver` detects the removal and the photo is removed from the session list. The `ViewPager2` updates to reflect the change.
- **SF-15** If the device is unlocked while the secure viewer is open, the viewer finishes itself. Subsequent taps on the overlay will launch the external gallery app per AC-01.
- **SF-16** The viewer does not provide any way to access photos outside the current session. It has no navigation to albums, folders, or the broader photo library.

-----

## 6. Settings UI

### 6.1 Main Screen

- **UI-01** The main screen contains:
1. A master on/off toggle for the service, with a status line beneath it that displays error descriptions when applicable (otherwise no status text).
1. A gallery app row showing the currently selected app’s icon and name (or “Not set — tap to choose” if none is selected). Tapping this row opens the gallery app picker (§6.2).
1. A link/button to Advanced Settings (§6.3).
- **UI-02** If any required permission is missing, a banner appears at the top of the screen indicating which permission is missing, with a tap action that navigates to the appropriate system settings screen.
- **UI-03** If Pixel Camera (`com.google.android.GoogleCamera`) is not installed, a notice replaces the service toggle: “Pixel Camera is not installed. GB4PC requires Pixel Camera to function.”
- **UI-04** If battery optimization exclusion has not been granted, a warning banner appears: “Not excluded from battery optimization — GB4PC may be killed in the background. Tap to fix.”

### 6.2 Gallery App Picker

- **UI-05** The gallery app picker displays a scrollable list of installed apps that have a launch intent, sorted alphabetically. Each row shows the app’s icon and name.
- **UI-06** A search/filter bar at the top allows the user to type to narrow the list.
- **UI-07** The picker is presented as a bottom sheet or full-screen dialog. Selecting an app immediately saves the choice and dismisses the picker.
- **UI-08** The picker is also shown just-in-time (JIT) when the user taps the overlay for the first time and no gallery app has been configured (see AC-03). In this case, the picker is launched as an activity that displays over Pixel Camera. After selection, the chosen gallery app is launched immediately (so the tap feels like it worked).
- **UI-09** The picker excludes `com.google.android.GoogleCamera` and GB4PC’s own package from the list.

### 6.3 Advanced Settings

- **UI-10** The Advanced Settings screen contains:
1. **Overlay Position** — Three labeled sliders or number inputs:
  - `X position` (0.00–100.00%) — horizontal center
  - `Y position` (0.00–100.00%) — vertical center
  - `Size` (1.00–30.00%) — percentage of the shorter screen dimension
1. **Reset to Defaults** button — restores the shipped default position values for the current display aspect ratio. Shows a confirmation dialog before resetting.
1. **Debug Log** — A scrollable log viewer showing the most recent foreground detection events (camera callbacks, USM queries, overlay show/hide events), timestamped. Useful for troubleshooting. The log is held in a circular buffer of the last 200 entries, in memory only (not persisted).
- **UI-11** Changes to position/size values are applied immediately (live preview if the overlay is currently visible). Values are saved on change.

-----

## 7. Foreground Service & Notification

- **FS-01** The service runs as an Android Foreground Service with type `specialUse` (API 34+) or untyped (API 26–33).
- **FS-02** The persistent notification shows: “GB4PC is running” with a “Stop” action that terminates the service and turns off the master toggle.
- **FS-03** The notification uses a low-priority channel (`IMPORTANCE_LOW`) so it appears silently and sits at the bottom of the notification shade.
- **FS-04** Tapping the notification body opens the GB4PC main settings screen.
- **FS-05** The service is started on boot via `RECEIVE_BOOT_COMPLETED` broadcast if the master toggle was on when the device last shut down. The toggle state is persisted in `SharedPreferences`.

-----

## 8. Data Persistence

- **DA-01** All settings (gallery app package name, overlay positions per aspect ratio, master toggle state) are stored in `SharedPreferences`. A Room database is not required for v1 given the minimal data model.
- **DA-02** Overlay positions are stored as a JSON-serialized map keyed by quantized aspect ratio (two decimal places). Each entry contains `x%`, `y%`, and `size%`.
- **DA-03** The debug log is in-memory only, in a circular buffer. It is lost when the process is killed.

-----

## 9. Edge Cases & Error Handling

- **EC-01** **Gallery app uninstalled while overlay is visible:** The overlay switches to a placeholder icon with a warning badge. Next tap triggers the gallery app picker (if unlocked) or a toast (if locked). See AC-04.
- **EC-02** **Pixel Camera uninstalled:** The service detects this when the camera callback fires and USM returns a different foreground package, or on next service start. The main settings screen shows the “not installed” notice per UI-03. The service stops.
- **EC-03** **Overlay permission revoked while overlay is visible:** The overlay fails silently. The service detects the failure on the next show attempt, hides the overlay, and posts a notification per PM-04.
- **EC-04** **Usage Stats permission revoked:** Per PM-03, the service continues running but cannot confirm foreground app identity. Overlay is hidden; notification is posted.
- **EC-05** **Split-screen / multi-window:** If Pixel Camera is one of the visible apps and holds camera hardware, the overlay is shown. It draws above both apps (standard `TYPE_APPLICATION_OVERLAY` behavior).
- **EC-06** **Lock screen:** `TYPE_APPLICATION_OVERLAY` windows can draw over apps that are displayed while the device is locked, including Pixel Camera launched from the lock screen. The overlay remains visible and functional over the Pixel Camera viewfinder regardless of lock state. The overlay disappears only through the normal deactivation path (camera hardware released or Pixel Camera leaves the foreground). The overlay cannot draw over the keyguard surface itself (PIN/pattern/fingerprint screen), but this is a non-issue since the camera is not visible on that surface.
- **EC-07** **Camera acquired by non-Pixel-Camera app:** `onCameraUnavailable` fires, USM query reveals the foreground app is not Pixel Camera. Overlay is not shown. No action is taken.
- **EC-08** **Multiple rapid camera switches (e.g., toggling front/back repeatedly):** The 500ms debounce (DT-04) prevents overlay flicker. At most one USM query is issued per debounce window.
- **EC-09** **USM query returns stale data:** `UsageStatsManager` event data can lag by a few hundred milliseconds. The 5-second query window in DT-02 mitigates this. If no `MOVE_TO_FOREGROUND` event is found in the window, the service assumes the foreground app is unknown and does not show the overlay.

-----

## 10. Package & Build

- **PK-01** Application ID: `com.gb4pc` (or a suitable alternative).
- **PK-02** Minimum SDK: 26 (Android 8.0).
- **PK-03** Target SDK: 35 (or current latest stable).
- **PK-04** Language: Kotlin.
- **PK-05** UI framework: Jetpack Compose for all settings screens. The overlay and secure viewer use traditional `View`/`WindowManager` APIs (Compose does not support `WindowManager` overlay views).
- **PK-06** Dependencies should be minimal. Suggested:
  - AndroidX Core, Lifecycle, Activity, Compose (BOM)
  - A gesture-capable ImageView library for the secure viewer (e.g., `com.davemorrissey.labs:subsampling-scale-image-view` or equivalent)
  - No network libraries, no analytics, no crash reporting.

-----

## 11. Known Risks & Platform Limitations

- **KR-01** **`setHideOverlayWindows` (Android 12+):** Android 12 introduced `Window.setHideOverlayWindows(true)`, which allows any app to block all `TYPE_APPLICATION_OVERLAY` windows from appearing over it. This is an OS-enforced restriction with no workaround. If Google adds this call to Pixel Camera in a future update, GB4PC’s overlay will be silently hidden. As of this writing, Pixel Camera does not use this API (it is intended for sensitive screens like banking and authentication flows), but it represents a platform-level risk that could render GB4PC non-functional without any available mitigation.
- **KR-02** **Untrusted touch blocking (Android 12+):** Android 12 blocks touch pass-through for untrusted overlay windows by default. GB4PC’s overlay *receives* taps directly (it does not pass them through to the underlying app), so this restriction should not affect normal operation. However, it must be verified during testing that taps on the overlay are received correctly and that taps outside the overlay reach Pixel Camera without interference.
- **KR-03** **Pixel Camera UI changes:** The hardcoded default overlay position (PS-02) is based on the current Pixel Camera layout across Pixel 6/7/8/9 devices. Google may redesign the Pixel Camera UI at any time, moving or removing the Google Photos button. Users can correct the position via Advanced Settings, but a significant redesign could require updated defaults in a new GB4PC release.
- **KR-04** **Incorrect overlay positioning:** If the overlay does not accurately cover the Google Photos button, the user experience degrades in two ways: the native button may be partially visible or tappable alongside the overlay, and the overlay may obscure other Pixel Camera controls. This risk is compounded in split-screen mode, where Pixel Camera’s window size is highly variable and the layout may reflow in ways that the normalized position values do not account for. The current per-aspect-ratio position storage (PS-03) helps for rotation changes, but split-screen produces a continuum of window sizes at the same aspect ratio. Incorrect positioning may be a significant usability problem in v1 and is the primary motivation for exploring the detection approaches in Appendix A.

-----

## Appendix A: Overlay Position Detection — Future Approaches

The current spec (v1) uses hardcoded default positions with manual adjustment via Advanced Settings. This appendix documents alternative approaches for automatically detecting the Google Photos button’s position, evaluated for potential inclusion in future versions.

### A1. Guided Calibration Tap

**Concept:** On first run or via Advanced Settings, show a full-screen semi-transparent overlay instructing the user: “Open Pixel Camera, then tap the Google Photos button through this overlay.” Record the tap coordinates as the overlay position.

**Pros:** Precise, device-agnostic, no ongoing cost, no special permissions. One-time action taking about five seconds.

**Cons:** Requires a manual user action. Slightly tricky UX: the semi-transparent overlay must pass the tap through to Pixel Camera (to confirm it’s the right button) while simultaneously recording coordinates.

**Feasibility:** High. This is the most practical improvement over hardcoded defaults.

### A2. Device-Model Position Database

**Concept:** Bundle a JSON map of `(device model, display resolution) → (x%, y%, size%)`. The Pixel Camera UI is consistent across a given hardware model. Ship with data for Pixel 6/7/8/9 (all variants). Allow community contributions.

**Pros:** Zero-configuration for supported devices.

**Cons:** Requires updating whenever Pixel Camera redesigns its UI or new Pixel devices launch. Does not cover non-Pixel devices running Pixel Camera ports. Maintenance burden grows with device diversity.

**Feasibility:** Medium. Useful as a complement to calibration, not a standalone solution.

### A3. AccessibilityService

**Concept:** Register an `AccessibilityService` that calls `getRootInActiveWindow()` on Pixel Camera’s window, traverses the accessibility node tree, locates the Google Photos button by its content description or view ID, and calls `getBoundsInScreen()` to get exact pixel coordinates. Jetpack Compose UIs expose semantics nodes to the accessibility framework, so this works even for Compose-based layouts.

**Pros:** Most precise and reliable method. Adapts automatically to UI changes and different screen sizes. No user interaction required.

**Cons:** Google restricts `AccessibilityService` to genuine accessibility purposes. Play Store review may reject it. Users must manually enable it in system Accessibility settings — a heavyweight permission that is difficult to explain and may alarm privacy-conscious users. F-Droid distribution mitigates the Play Store concern, but the UX remains heavy.

**Feasibility:** High technically, low socially. Best suited for a power-user toggle in Advanced Settings, clearly labeled as optional, with the caveat that it requires Accessibility permission.

### A4. MediaProjection Screenshot + Image Analysis

**Concept:** Use `MediaProjection` to capture a screenshot of Pixel Camera, then locate the thumbnail button using image heuristics (it is always a rounded square with a photo thumbnail, near the bottom of the screen).

**Pros:** Does not depend on Pixel Camera’s internal structure.

**Cons:** Requires a user-facing consent dialog every process lifetime (on Android 10+, consent cannot be persisted across process restarts). Adds battery cost. The thumbnail content changes with every photo taken, making template matching unreliable. Significant implementation complexity for marginal benefit over simpler approaches.

**Feasibility:** Low. Over-engineered for this problem.

### A5. APK Resource Extraction

**Concept:** Decompile Pixel Camera’s APK at install time and extract layout XML to determine button positions.

**Pros:** No runtime cost.

**Cons:** Pixel Camera increasingly uses Jetpack Compose, meaning UI layout is compiled into bytecode rather than XML resources. Even remaining XML layouts use `ConstraintLayout` with runtime-computed dimensions. Fragile across Pixel Camera updates. May raise legal concerns around reverse engineering.

**Feasibility:** Very low. Not viable for Compose-based UIs.

### A6. Emulator-Based Position Database (Build-Time)

**Concept:** Use Android emulator images for each target Pixel device to determine the Google Photos button’s position ahead of time, outside the app itself. The process: for each supported device model and Pixel Camera version, launch Pixel Camera in an emulator configured with that device’s exact display specs, then extract the button position using `uiautomator dump` (which outputs the full view/accessibility hierarchy with bounds) or an AccessibilityService running inside the emulator. Record the normalized positions. This can be repeated at various window sizes to capture split-screen positions. The resulting position map is bundled into GB4PC as a static JSON asset, keyed by `(device model, Pixel Camera version, window size class)`.

**Pros:** Zero runtime cost, no special permissions needed on the user’s device, and no user interaction. Positions are exact rather than estimated, since they come from running the real Pixel Camera UI at the real display dimensions. The emulator approach sidesteps AccessibilityService permission concerns entirely — the service runs only in the emulator during development, not on the user’s device. Split-screen positions can be systematically mapped by emulating different window sizes. The process can be automated as a CI/CD pipeline step that runs whenever a new Pixel Camera APK is released.

**Cons:** Requires maintaining emulator configurations for each supported device. Must be re-run when Pixel Camera updates change the UI layout (though this can be automated and diffed). Google does not publish Pixel Camera on the emulator by default — the APK would need to be sideloaded into the emulator image. Coverage is limited to devices and window sizes that have been explicitly tested. Non-Pixel devices running Pixel Camera ports would not be covered.

**Feasibility:** High. This approach combines the precision of AccessibilityService detection with the zero-permission simplicity of a static database, at the cost of a build-time maintenance process that can be largely automated.