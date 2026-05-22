CI emulators frequently hit ANR (Application Not Responding) timeouts during install operations. The root cause is often package manager broadcasts to third-party launchers.

## Root Cause: Icon Grid Reconciliation

When you run `adb install -r` (reinstall), the package manager broadcasts `ACTION_PACKAGE_REPLACED` to all receivers, including the Pixel Launcher.

Pixel Launcher's response:
1. Receives `ACTION_PACKAGE_REPLACED` broadcast
2. Begins icon-grid reconciliation (checking if icon exists, refreshing thumbnails, updating shortcut cache)
3. On slow emulators, this work saturates the **main thread**
4. Main thread can't process input, timers, or other events
5. After ~10 seconds, Android system triggers a broadcast ANR timeout

The broadcast ANR timer is separate from and usually hits **before** the app ANR timer.

## Two Solution Strategies

### Plan A: Adaptive Watcher (Recommended)
Run a background script that:
- Polls for ANR dialogs every 3 seconds
- Detects and dismisses ANRs as they occur
- Uses multiple detection methods (logcat + dumpsys window) for reliability
- Allows the build to continue with minimal delay

**Pros:** Handles any transient ANR, future-proof
**Cons:** Adds script complexity, requires careful race-condition handling

### Plan B: Fixed Sleep (Simple but Inefficient)
Before running the actual test, do a `sleep 30` (or longer) to let icon reconciliation finish.

**Pros:** Simple, no additional scripts
**Cons:** Wastes time on faster builds, doesn't actually detect ANR (just hopes 30s is enough)

## Design Tradeoff

Plan A is better for CI reliability, but Plan B may be acceptable if:
- Your emulator is consistently slow (so 30s sleep is predictable)
- You have only a few test runs (so the time waste is acceptable)
- Your team prefers simplicity over optimization

The ANR watcher (Plan A) is the production-quality approach used in projects running many builds per day.

---

**Source:** [Issue #194 — Pixel Launcher ANR Root Cause](https://github.com/aunger/gallery-button-for-pixel-camera/issues/194)
