# Claude Code for Web — Environment Setup

This document describes every manual step required to go from a fresh
**Claude Code for Web** container to a fully working GB4PC build environment.
The session-start hook (`scripts/session-start.sh`) automates steps 0–3
idempotently, so a human only needs to do them once manually on a brand-new
container.

---

## Prerequisites

- The container already provides: Java 21 (OpenJDK), Gradle 8.14.3
  (`/opt/gradle/`), `wget`, `unzip`, `python3`.
- Internet access is available **only through a proxy** at `21.0.0.19:15004`.
  Credentials are injected as JWT tokens in `JAVA_TOOL_OPTIONS`,
  `HTTP_PROXY`, and `HTTPS_PROXY` environment variables.

---

## Step 0 — Clone the repository

```bash
git clone <repo-url> gallery-button-for-pixel-camera
cd gallery-button-for-pixel-camera
```

---

## Step 1 — Fix the Java proxy / DNS issue

### The problem

The container's `JAVA_TOOL_OPTIONS` (injected at container start) contains:

```
-Dhttp.nonProxyHosts=...|*.googleapis.com|*.google.com
```

This tells Java to bypass the proxy for `*.google.com` and reach those hosts
directly. However, `*.google.com` has **no direct DNS resolution** in this
container network. Any Java tool (`gradle`, `sdkmanager`, etc.) that tries to
reach `maven.google.com`, `dl.google.com`, or `services.gradle.org`
(via non-proxy) gets `UnknownHostException`.

Shell tools (`wget`, `curl`) do work for `*.google.com` because they appear
to ignore the `no_proxy` wildcard and route those requests through the proxy.

### The fix

Strip `|*.googleapis.com|*.google.com` from `JAVA_TOOL_OPTIONS` so Java
routes those domains through the proxy like everything else. Add to
`~/.bashrc`:

```bash
if [[ -n "$JAVA_TOOL_OPTIONS" ]]; then
    export JAVA_TOOL_OPTIONS=$(echo "$JAVA_TOOL_OPTIONS" \
        | sed 's/|\*\.googleapis\.com|\*\.google\.com//')
fi
```

### Why a Gradle `init.d` script is also needed

Java 9+ removed the automatic registration of `-Dhttp.proxyUser` /
`-Dhttp.proxyPassword` as a system `Authenticator`. Without an explicit
`Authenticator`, every proxied HTTPS request (CONNECT tunnel) gets
`HTTP 407 Proxy Authentication Required`.

`~/.gradle/init.d/proxy-auth.gradle` registers an `Authenticator` at Gradle
startup that reads the same system properties:

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

---

## Step 2 — Install the Android SDK

The Android SDK is **not** pre-installed. Install it under
`/home/user/android-sdk`.

### 2a. Download command-line tools

```bash
mkdir -p /home/user/android-sdk/cmdline-tools
cd /home/user/android-sdk/cmdline-tools
wget "https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip" \
     -O cmdline-tools.zip
unzip -q cmdline-tools.zip
mv cmdline-tools latest
```

### 2b. Accept SDK licenses

```bash
mkdir -p /home/user/android-sdk/licenses
printf '\n8933bad161af4178b1185d1a37fbf41ea5269c55\nd56f5187479451eabf01fb78af6dfcb131a6481e\n24333f8a63b6825ea9c5514f83c2829b004d1fee' \
    > /home/user/android-sdk/licenses/android-sdk-license
printf '\n84831b9409646a918e30573bab4c9c91346d8abd' \
    > /home/user/android-sdk/licenses/android-sdk-preview-license
```

### 2c. Install SDK packages via `sdkmanager`

With steps 0 and 1 applied, `sdkmanager` can now reach `dl.google.com`
through the proxy:

```bash
export ANDROID_HOME=/home/user/android-sdk
export PATH=$PATH:$ANDROID_HOME/cmdline-tools/latest/bin

sdkmanager "platforms;android-35" "build-tools;35.0.0" "build-tools;34.0.0" "platform-tools"
```

> **Note:** `build-tools;34.0.0` is required by Android Gradle Plugin 8.7.3
> even though the project targets build-tools 35.

---

## Step 3 — Build the project

With the above steps done, both `./gradlew` and the installed Gradle work:

```bash
# Using the Gradle wrapper (downloads Gradle 8.9 on first run):
./gradlew assembleDebug

# Or using the system-installed Gradle 8.14.3 directly:
/opt/gradle/bin/gradle assembleDebug
```

The debug APK is written to `app/build/outputs/apk/debug/app-debug.apk`.

Run unit tests:

```bash
./gradlew testDebugUnitTest
```

---

## Environment variables to set for this working directory

Add to the working-directory environment (or a future container's startup
environment — but **not** as permanent system env vars, since the JWT tokens
rotate):

| Variable | Value | Why |
|---|---|---|
| `ANDROID_HOME` | `/home/user/android-sdk` | Required by AGP to locate the SDK |
| `JAVA_TOOL_OPTIONS` | *(existing value with `\|*.googleapis.com\|*.google.com` removed)* | Enables Java to reach Google Maven/SDK hosts via the proxy |

`ANDROID_HOME` and `PATH` additions are already written to `~/.bashrc` by the
session-start hook (run once). `JAVA_TOOL_OPTIONS` is patched from the
existing container value (which rotates each session), so it must be fixed at
shell start via the `~/.bashrc` snippet above — **do not hardcode the JWT
token**.

---

## Files created outside the repo

| Path | Purpose |
|---|---|
| `~/.bashrc` (appended) | Sets `ANDROID_HOME`, `PATH`, and fixes `JAVA_TOOL_OPTIONS` |
| `~/.gradle/init.d/proxy-auth.gradle` | Registers Java `Authenticator` for proxy HTTPS |
| `/home/user/android-sdk/` | Android SDK installation |
| `~/.gradle/wrapper/dists/gradle-8.9-bin/` | Gradle 8.9 distribution (downloaded by `./gradlew` on first run) |
