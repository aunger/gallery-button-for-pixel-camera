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
| `GITHUB_TOKEN`     | *(session-scoped JWT)*       | container         | Use with `curl` to query the GitHub REST API                 |

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

---

## GitHub MCP tool quirks

### `issue_write labels: []` is a silent no-op

Calling `mcp__github__issue_write` with `labels: []` to clear all labels returns a
success-looking response (`{"id":"…","url":"…"}`) but **does not change any labels**.
The MCP tool appears to filter out empty arrays before building the API request body,
so the `labels` field is never sent and all existing labels remain.

**Evidence (empirically verified):**
- `labels: ["planning needed"]` → correctly replaces ALL labels with just that one
  (REPLACE semantics — "for ai to do" was removed in the same call). Non-empty arrays work.
- `labels: []` → no change; all labels remain. Confirmed by a follow-up `get_labels` read.

**Implication:** There is no way to remove *all* labels from an issue using
`mcp__github__issue_write` alone. To drop N−1 labels, set `labels` to the one label
you want to keep. The last remaining label cannot be removed via this tool, and the
`GITHUB_TOKEN` environment variable also cannot help — it is read-only for the Issues
API (write attempts return 403).

### Always verify writes with a follow-up read

`mcp__github__issue_write` (and similar write tools) return `{"id":"…","url":"…"}`
regardless of whether the underlying change was applied. The stripped response gives
no signal about what actually changed. **Always follow a write with a confirming
`issue_read`** (e.g. `method: "get_labels"`) before reporting success.

### "Token expired" errors on write operations

Write operations (`add_issue_comment`, `issue_write`, etc.) occasionally fail with:

```
MCP server "github" requires re-authorization (token expired)
```

…while reads on the same resource succeed. The `GITHUB_TOKEN` is a session-scoped JWT
(short-lived). The JWT can expire mid-session; reads appear to use a cached or
lower-privilege path that still succeeds, while writes require a fresh token. A
successful read call appears to trigger a background JWT refresh that unblocks
subsequent writes.

**Workaround (not guaranteed — appears to work):** if writes fail with this error,
try performing any `issue_read` first, then immediately retry the write. Do not
interpret the error as a "no blind writes" enforcement — it is genuine token expiry.

---

## Read GitHub Actions job logs

```bash
# List jobs for a workflow run (to get job IDs):
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/aunger/gallery-button-for-pixel-camera/actions/runs/{run_id}/jobs"

# Fetch the log for a specific job:
curl -s -L -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/aunger/gallery-button-for-pixel-camera/actions/jobs/{job_id}/logs"
```
