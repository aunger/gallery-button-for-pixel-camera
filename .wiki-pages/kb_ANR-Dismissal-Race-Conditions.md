ANR dialogs can be difficult to dismiss reliably in CI due to race conditions in the dismiss script. Two key issues occur:

1. **Early exit from `dismiss_anr.sh`:** The script can exit prematurely while the ANR dialog is still on screen. This happens because of a race window where the logcat-based detection misses the ANR event entirely. The event scrolls past logcat's buffer before the monitor checks for it.

2. **Input service unavailability:** Even after successfully locating the ANR dialog, the input service can become unavailable, causing cascading failures in subsequent tap/swipe operations.

## Root Cause & Fix

**Race condition fix:** Add a safety check using `dumpsys window` to verify the ANR dialog is still present before the script exits, even if logcat detection succeeded. This catches cases where the dialog appears but the event was missed.

**Input service resilience:** Implement retry logic with backoff delays when input service operations fail. Not all input failures are permanent — some resolve with a brief wait.

## Defensive Programming Pattern

This illustrates a key CI reliability pattern: **verify state independently** rather than trusting a single signal path. When dealing with asynchronous events (like ANR dialogs), use multiple detection methods (logcat + dumpsys) and always confirm the final desired state before proceeding.

---

**Source:** [PR #204 — ANR Dismissal Race Condition & Input Service Failure](https://github.com/aunger/gallery-button-for-pixel-camera/pull/204)
