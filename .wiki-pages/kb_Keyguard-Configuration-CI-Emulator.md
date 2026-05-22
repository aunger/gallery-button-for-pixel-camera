Android emulators in CI environments require explicit keyguard (lock screen) configuration. By default, emulators without a PIN don't engage the keyguard on sleep, which breaks any tests expecting lock-screen behavior.

## The Problem

On CI emulator AVDs **without a security PIN:**
- `adb shell input keyevent KEYCODE_SLEEP` turns the display off
- But the keyguard **never engages** — the display is just off
- Subsequent unlock attempts fail because there's nothing to unlock
- Tests expecting keyguard state will fail or timeout

## The Solution

### Setup (Run Once)

Set a security PIN during emulator initialization:

```bash
adb shell locksettings set-pin 1234
```

This command:
- Sets a numeric PIN (1234 is conventional for tests)
- Forces keyguard to engage on next SLEEP event
- Is idempotent: running it again with the same PIN is safe

### Teardown (Optional Cleanup)

If you're destroying the emulator after the test run:

```bash
adb shell locksettings clear --old 1234
```

### Idempotent Setup for Persistent AVDs

For local AVDs that are reused between builds, make the setup idempotent:

```bash
adb shell locksettings set-pin --old 1234 1234
```

This sets the PIN to 1234, but if it's already 1234, it's a no-op (no error).

## Key Detail: Engagement is the Gate Condition

The important thing is **keyguard engagement**, not the PIN value. The test framework uses keyguard APIs to check engagement state, not PIN-based unlocking. The PIN is just what forces engagement. You could use any PIN value, but 1234 is conventional.

---

**Source:** [Issue #178 — Keyguard Engagement Requirements](https://github.com/aunger/gallery-button-for-pixel-camera/issues/178)
