Setting up the Android emulator keyguard (lock screen) for E2E tests requires idempotent commands that work across multiple test runs without conflicts.

## Single-Run Setup

For ephemeral CI emulators destroyed after each test run:

```bash
adb shell locksettings set-pin 1234
```

This sets the PIN once and is sufficient for one test execution.

## Idempotent Multi-Run Setup

For persistent local AVDs that are reused between multiple test runs:

```bash
adb shell locksettings set-pin --old 1234 1234
```

The `--old 1234` flag checks if the PIN is currently 1234. If it is, no-op (exit 0). If it's different, update it. This allows:
- First test run: Sets PIN to 1234
- Second test run: Already 1234, no-op
- No conflicts or "PIN already set" errors

## Idempotent Cleanup (Optional)

If you want to remove the PIN after tests complete:

```bash
adb shell locksettings clear --old 1234
```

Again, `--old 1234` makes this safe — only clears if the PIN is currently 1234.

## Key Detail: Security ≠ Test PIN

Using PIN "1234" is not a security risk for test emulators because:
- It's a **test-only emulator**, not a production device
- The PIN is just what forces **keyguard engagement** (the gate condition)
- Test code doesn't actually unlock the device; it just verifies engagement

The test framework uses keyguard APIs to check if engagement occurred, not PIN-based unlocking. The PIN itself is irrelevant to the test logic.

---

**Source:** [PR #181 — Emulator Keyguard Configuration](https://github.com/aunger/gallery-button-for-pixel-camera/pull/181)
