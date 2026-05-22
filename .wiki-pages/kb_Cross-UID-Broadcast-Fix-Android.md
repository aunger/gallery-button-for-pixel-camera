Fixing cross-UID broadcast issues (Android 12+) requires understanding which receivers are exported and removing overly-restrictive target packages.

## Root Cause Review

On Android 12+, `RECEIVER_NOT_EXPORTED` prevents cross-UID broadcasts. This creates a two-sided barrier:
1. Sender can't send to unexported receiver (silently dropped)
2. Even if sender marks receiver as exported, a `setPackage()` restriction may prevent the response

## Applied Fixes

### Fix 1: Export the Receiver
```xml
<receiver android:name=".ShutterReceiver" android:exported="true">
  <intent-filter>
    <action android:name="com.example.test.ACTION_SHUTTER" />
  </intent-filter>
</receiver>
```

By declaring `android:exported="true"`, the receiver can receive broadcasts from other processes.

**Risk level:** Low for test-only receivers with custom action strings
- Custom action strings (not standard Android broadcasts) limit who can target the receiver
- If a receiver listens for `android.intent.action.BOOT_COMPLETED`, exporting it is risky
- If it listens for `com.example.test.ACTION_SHUTTER_DONE`, it's safe; only your test code knows the string

### Fix 2: Remove Package Restriction
```java
// Before (too restrictive):
intent.setPackage(packageName);
context.sendBroadcast(intent);

// After (allows responses):
context.sendBroadcast(intent);
```

The `setPackage()` call restricts the broadcast to a single receiver package. This is safe for public system broadcasts, but when combined with `android:exported="false"`, it creates a deadlock.

**Rationale for removal:** In a test context with unique action strings, the broadcast will only be received by the intended app anyway (no other app listens for your custom actions).

## Security Model Preservation

This fix maintains Android's security model:
- **Custom action strings** act as a permission boundary
- Only apps that know the action string can receive it
- No production risk if the action is not documented or used externally

For **production apps** using standard system actions, this approach would be inappropriate. Use signature-based permissions or Binder/ContentProvider IPC instead.

---

**Source:** [PR #180 — Cross-UID Broadcast Fix](https://github.com/aunger/gallery-button-for-pixel-camera/pull/180)
