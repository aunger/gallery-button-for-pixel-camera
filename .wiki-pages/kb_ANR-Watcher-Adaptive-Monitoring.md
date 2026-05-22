The ANR (Application Not Responding) watcher is a critical CI reliability component for projects that run on slow Android emulators. Adaptive health monitoring is superior to fixed sleep delays because it responds to actual system state.

## Design Overview

The `dismiss_anr.sh` script implements adaptive ANR detection and dismissal:

### Polling Strategy
- Check for ANR **every 3 seconds** for a **maximum of 30 seconds**
- This balances: fast detection (when ANRs occur) vs energy efficiency (checking too often)
- If no ANR appears within 30s, assume the build step completed normally

### Dual-Path Detection

**Fast path — logcat monitoring:**
- Scan logcat for ANR-related system messages
- Instant detection when the message appears
- Risk: logcat buffer is limited; messages can scroll past before being seen

**Fallback path — `dumpsys window` query:**
- Parse the window dump to check if any ANR dialog is currently on screen
- Slower than logcat (requires shell parsing) but reliable
- Catches ANRs that logcat missed due to buffer churn

Always use both paths together. Logcat is fast-path optimization; `dumpsys window` is the ground truth.

### CPU Idle Detection

CPU idle detection is used as a **precondition** (not a gate) before attempting the dismiss tap:
- Query `dumpsys cpuinfo` and check if CPU load is below threshold
- **Two-reading guard:** Take two readings 100ms apart. Both must show idle to avoid false positives from brief drops during render/composition.
- This reduces likelihood of taps landing during high I/O or rendering

## Why Adaptive > Fixed Sleep

**Fixed sleep approach:**
- Must be conservatively long (~30s) to handle slowest emulators
- Wasted time on faster builds
- No feedback on actual ANR state

**Adaptive watcher:**
- Responds to actual events (ANR appears → dismiss immediately)
- On fast builds with no ANR, can exit in seconds
- Gives confidence through multi-path detection that no ANR is present

## Operational Guidance

Use this pattern whenever CI needs to **monitor for and react to transient system events** that may or may not occur. Examples: crash dialogs, permission prompts, low-memory warnings.

---

**Source:** [PR #200 — ANR Watcher Script Design](https://github.com/aunger/gallery-button-for-pixel-camera/pull/200)
