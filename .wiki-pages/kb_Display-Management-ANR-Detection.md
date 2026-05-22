Display management and dialog timing on Android emulators involve subtle race conditions that can cause flaky CI failures.

## The CPU Idle Race

When `dismiss_anr.sh` uses CPU idle detection as a precondition for the dismiss tap, a timing issue occurs:

- The ANR dialog render causes CPU to spike
- Once rendering completes, CPU drops to idle **immediately** — before the dismiss tap has landed
- The script detects idle and exits, but the tap hasn't reached the input queue yet
- Result: ANR persists or dismissal appears to fail

**Fix:** Re-check for the ANR dialog after the idle-exit condition is detected. If the dialog is still there, the idle was a false positive (rendering completed but tap not processed yet). Continue waiting and retry.

## Keyguard Unlock: `wm dismiss-keyguard` vs `input swipe`

Early implementations used `adb shell wm dismiss-keyguard`, which is unreliable on CI emulators:
- Sometimes fails silently
- Can leave the display in an inconsistent state
- Blocks subsequent input commands

**Solution:** Replace with `input swipe` from edge to center (e.g., swipe left across the bottom). This is more reliable because it:
- Simulates actual user gesture (swipe)
- Doesn't rely on the Window Manager accepting the dismiss request
- Works consistently across different emulator configurations

## Lesson

Timing races are common in CI emulator automation. When a precondition is based on observable state (CPU idle, display state), **always reverify the actual goal state** rather than assuming the precondition was sufficient.

---

**Source:** [PR #202 — ANR Watcher Improvements & Keyguard Hardening](https://github.com/aunger/gallery-button-for-pixel-camera/pull/202)
