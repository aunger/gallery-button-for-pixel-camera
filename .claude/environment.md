# GB4PC — Claude Code for Web: Environment Setup

This document describes how to configure a fresh **Claude Code for Web** container
to build this project. Every step is also automated by the session-start hook
(`scripts/setup-env.sh`), which runs automatically on session start and is
idempotent (safe to run repeatedly).

---

## 1. Clone the repository

```bash
git clone https://github.com/aunger/gallery-button-for-pixel-camera.git
cd gallery-button-for-pixel-camera
```

---

## 2. Fix the Java proxy configuration (critical)

Claude Code for Web containers set `JAVA_TOOL_OPTIONS` to route all Java traffic
through an authenticated HTTP proxy. However, they also **exclude `*.google.com`
and `*.googleapis.com` from the proxy** (`nonProxyHosts`), intending to allow
direct access. In practice, those domains have **no direct DNS resolution** in the
container, so any Java tool (`gradle`, `sdkmanager`, etc.) that tries to reach
`maven.google.com`, `dl.google.com`, or `services.gradle.org` via Google's CDN
fails with `UnknownHostException`.

### Fix — append to `~/.bashrc`

```bash
# Strip *.googleapis.com|*.google.com from nonProxyHosts so Java
# routes those domains through the container proxy instead.
if [[ -n "$JAVA_TOOL_OPTIONS" ]]; then
    export JAVA_TOOL_OPTIONS=$(echo "$JAVA_TOOL_OPTIONS" \
        | sed 's/|\*\.googleapis\.com|\*\.google\.com//')
fi
```

### Why `~/.bashrc` and not a project file

`JAVA_TOOL_OPTIONS` is injected by the container at startup; the correct fix
is to override it once in the shell profile rather than duplicating the entire
value (which contains a session-scoped JWT token that changes each session).

### Related — Gradle proxy authenticator

Java 9+ no longer automatically registers `http.proxyUser`/`http.proxyPassword`
as a system `Authenticator`. Without an explicit authenticator, the proxy returns
`407 Proxy Authentication Required` even though the credentials are set.

The fix is a one-line Gradle init script at `~/.gradle/init.d/proxy-auth.gradle`
(created by `scripts/setup-env.sh`):

```groovy
import java.net.Authenticator, java.net.PasswordAuthentication
String u = System.getProperty("http.proxyUser") ?: ""
String p = System.getProperty("http.proxyPassword") ?: ""
if (u) Authenticator.setDefault(new Authenticator() {
    protected PasswordAuthentication getPasswordAuthentication() {
        new PasswordAuthentication(u, p.toCharArray())
    }
})
```

This runs before any dependency resolution and lets Gradle authenticate with the
proxy for Maven Central, Google Maven, Gradle Plugin Portal, and the Gradle
distribution download.

---

## 3. Install the Android SDK

The SDK is installed to `/home/user/android-sdk`. The container has no pre-installed
Android SDK, but `sdkmanager` (from the command-line tools) and the required
packages are downloaded by `scripts/setup-env.sh`.

### Manual steps (what the script does)

```bash
export ANDROID_HOME=/home/user/android-sdk

# 1. Command-line tools
mkdir -p $ANDROID_HOME/cmdline-tools
wget -q https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip \
     -O /tmp/cmdline-tools.zip
unzip -q /tmp/cmdline-tools.zip -d $ANDROID_HOME/cmdline-tools
mv $ANDROID_HOME/cmdline-tools/cmdline-tools $ANDROID_HOME/cmdline-tools/latest

# 2. Accept licenses
mkdir -p $ANDROID_HOME/licenses
printf '\n8933bad161af4178b1185d1a37fbf41ea5269c55\nd56f5187479451eabf01fb78af6dfcb131a6481e\n24333f8a63b6825ea9c5514f83c2829b004d1fee' \
    > $ANDROID_HOME/licenses/android-sdk-license
printf '\n84831b9409646a918e30573bab4c9c91346d8abd' \
    > $ANDROID_HOME/licenses/android-sdk-preview-license

# 3. SDK packages (sdkmanager now works after the nonProxyHosts fix above)
sdkmanager "platforms;android-35" "build-tools;35.0.0" "build-tools;34.0.0" "platform-tools"
```

Note: `sdkmanager --install` is used in preference to manual zip extraction
once the proxy is correctly configured.

### Required packages

| Package                | Why needed                                     |
|------------------------|------------------------------------------------|
| `platforms;android-35` | Compile against Android API 35                 |
| `build-tools;35.0.0`   | `aapt2`, `d8`, `zipalign` for app module       |
| `build-tools;34.0.0`   | AGP 8.7.3 downloads this version internally    |
| `platform-tools`       | `adb` for device deployment and debugging      |

---

## 4. Add persistent environment variables to `~/.bashrc`

```bash
export ANDROID_HOME=/home/user/android-sdk
export PATH=$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools
```

---

## 5. Build the project

```bash
./gradlew assembleDebug
# Output: app/build/outputs/apk/debug/app-debug.apk

./gradlew testDebugUnitTest   # run unit tests
./gradlew assembleRelease     # unsigned release build
```

The `gradlew` wrapper downloads Gradle 8.9 on first use (into
`~/.gradle/wrapper/dists/`). Subsequent runs use the cached copy.

---

## Environment variables reference

| Variable           | Value                        | Where to set  | Notes                                                    |
|--------------------|------------------------------|---------------|----------------------------------------------------------|
| `ANDROID_HOME`     | `/home/user/android-sdk`     | `~/.bashrc`   | Required by Gradle Android plugin and `adb`              |
| `JAVA_TOOL_OPTIONS`| *(modified, not replaced)*   | `~/.bashrc`   | Strip `*.google.com` from `nonProxyHosts` — see §2       |
| `PATH`             | `+$ANDROID_HOME/...`         | `~/.bashrc`   | Adds `sdkmanager`, `adb` to path                        |

> **Do not** hard-code the proxy credentials (`http.proxyUser`, `http.proxyPassword`)
> or the proxy host/port. They are injected by the container and contain a session-scoped
> JWT that rotates each session. The `sed` strip in `~/.bashrc` modifies
> `JAVA_TOOL_OPTIONS` in-place, preserving the live credentials automatically.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `UnknownHostException: dl.google.com` | `*.google.com` in `nonProxyHosts`, no direct DNS | Apply `~/.bashrc` fix (§2) |
| `407 Proxy Authentication Required` | Java 9+ doesn't auto-register proxy Authenticator | Deploy `~/.gradle/init.d/proxy-auth.gradle` (§2) |
| `Failed to find package 'platform-tools'` | sdkmanager can't fetch remote repo manifest | Same root cause as above |
| `Failed to install ... licences have not been accepted` | Missing `$ANDROID_HOME/licenses/` files | Run `sdkmanager --licenses` or use setup script |
| Build picks up wrong SDK | `ANDROID_HOME` unset or pointing to wrong path | Check `local.properties` and `ANDROID_HOME` |
