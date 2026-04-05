# GB4PC — Claude Code for Web: Environment Setup

## Quick start

```bash
git clone https://github.com/aunger/gallery-button-for-pixel-camera.git
cd gallery-button-for-pixel-camera
./gradlew assembleDebug          # debug APK → app/build/outputs/apk/debug/
./gradlew testDebugUnitTest      # unit tests
./gradlew assembleRelease        # unsigned release APK
```

The `SessionStart` hook (`.claude/hooks/session-start.sh`) runs automatically
at session start and sets up the Android SDK and proxy configuration. See the
script for implementation details — each step is commented.

---

## Environment variables

| Variable           | Value                        | Set by            | Notes                                                        |
|--------------------|------------------------------|-------------------|--------------------------------------------------------------|
| `ANDROID_HOME`     | `/home/user/android-sdk`     | hook + `~/.bashrc`| Required by Gradle Android plugin and `adb`                  |
| `JAVA_TOOL_OPTIONS`| *(modified, not replaced)*   | hook + `~/.bashrc`| Strips `*.google.com` from `nonProxyHosts` — see script §0   |
| `PATH`             | `+$ANDROID_HOME/…`           | hook + `~/.bashrc`| Adds `sdkmanager`, `adb` to path                            |

`~/.bashrc` carries the same fixes for interactive terminal sessions.
The proxy credentials in `JAVA_TOOL_OPTIONS` are a session-scoped JWT injected
by the container — never hard-code them.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `UnknownHostException: dl.google.com` | `*.google.com` in `nonProxyHosts`, no direct DNS | Hook §0 fixes this; check `~/.bashrc` for terminal use |
| `407 Proxy Authentication Required` | Java 9+ doesn't auto-register proxy `Authenticator` | Hook §1 writes `~/.gradle/init.d/proxy-auth.gradle` |
| `Failed to find package 'platform-tools'` | sdkmanager can't fetch repo manifest | Same root cause as above |
| `Failed to install … licences have not been accepted` | Missing `$ANDROID_HOME/licenses/` files | Hook §2b writes them; or run `sdkmanager --licenses` |
| Build picks up wrong SDK | `ANDROID_HOME` unset or wrong | Check `local.properties` and `ANDROID_HOME` |
