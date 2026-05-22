Android 12+ (API 31+) introduced stricter broadcast receiver semantics that prevent cross-UID broadcasts by default. This creates challenges when apps communicate via broadcast intents across process boundaries.

## The Security Model Change

**Android 11 and earlier:** Broadcast receivers (especially those declared in `AndroidManifest.xml`) received broadcasts from any process without explicit opt-in.

**Android 12+ (API 31+):** A receiver must declare `android:exported="true"` in the manifest to receive broadcasts from other apps (cross-UID). Receivers with `android:exported="false"` (default) silently drop cross-UID broadcasts.

## The Mirror-Image Problem

In a test setup with mock camera and test app, this creates a two-sided issue:

**Side 1: Test app can't reach mock camera receiver**
- Test app broadcasts `ACTION_CAMERA_READY` to mock camera process
- Mock camera has `<receiver android:exported="false" />`
- Broadcast is silently dropped, test never receives the ready signal

**Side 2: Mock camera can't respond to test app**
- Mock camera wants to broadcast `ACTION_SHUTTER_DONE` back to the test app
- Mock camera calls `setPackage(testAppPackageName)` to target only the test app
- But `setPackage()` is ignored by the system if the receiver is not exported
- Even if mock camera is exported, the target test app may not have an exported receiver

## Three Fix Options (with risk assessment)

### Option 1: Add `android:exported="true"` to receivers
```xml
<receiver android:name="..." android:exported="true">
  <intent-filter>
    <action android:name="com.example.ACTION_CAMERA_READY" />
  </intent-filter>
</receiver>
```

**Risk Level:** Low for test-only packages
- Rationale: Custom action strings (not standard Android intents) mean only apps knowing the string can target the receiver
- Production risk: None if this is in a test-only or mock app
- Recommendation: Safe to use

### Option 2: Drop `setPackage()` restriction
Remove the `setPackage(packageName)` call and broadcast publicly.

**Risk Level:** Medium
- Rationale: Other apps can now intercept the intent
- Recommendation: Only if using a sufficiently unique action string that collisions are unlikely

### Option 3: Use a shared signature or signature-level permission
Both apps sign with the same key and declare a signature-based permission.

**Risk Level:** High (complexity)
- Recommendation: Only for production apps; overkill for tests

## Recommended Approach

For test apps: **Use Option 1 + unique action strings.** Adding `android:exported="true"` to test-only receivers with custom action names (`com.example.test.ACTION_*`) is safe and straightforward.

For production apps: Use a shared signature permission or internal IPC mechanism (Binder, ContentProvider) instead of broadcasts.

---

**Source:** [Issue #177 — RECEIVER_NOT_EXPORTED Cross-UID Broadcast](https://github.com/aunger/gallery-button-for-pixel-camera/issues/177)
