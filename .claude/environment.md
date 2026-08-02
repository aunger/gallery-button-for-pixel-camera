# GB4PC: Claude Code for Web: Environment Setup

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
script for implementation details; each step is commented.

______________________________________________________________________

## Environment variables

| Variable            | Value                      | Set by             | Notes                                                      |
| ------------------- | -------------------------- | ------------------ | ---------------------------------------------------------- |
| `ANDROID_HOME`      | `/home/user/android-sdk`   | hook + `~/.bashrc` | Required by Gradle Android plugin and `adb`                |
| `JAVA_TOOL_OPTIONS` | *(modified, not replaced)* | hook + `~/.bashrc` | Strips `*.google.com` from `nonProxyHosts` (see script §0) |
| `PATH`              | `+$ANDROID_HOME/...`       | hook + `~/.bashrc` | Adds `sdkmanager`, `adb` to path                           |
| `GITHUB_TOKEN`      | *(fine-grained PAT)*       | container          | Use with `curl` to query the GitHub REST API               |

`~/.bashrc` carries the same fixes for interactive terminal sessions.
The proxy credentials in `JAVA_TOOL_OPTIONS` are a session-scoped JWT injected
by the container; never hard-code them.

______________________________________________________________________

## Troubleshooting

| Symptom                                                 | Cause                                               | Fix                                                    |
| ------------------------------------------------------- | --------------------------------------------------- | ------------------------------------------------------ |
| `UnknownHostException: dl.google.com`                   | `*.google.com` in `nonProxyHosts`, no direct DNS    | Hook §0 fixes this; check `~/.bashrc` for terminal use |
| `407 Proxy Authentication Required`                     | Java 9+ doesn't auto-register proxy `Authenticator` | Hook §1 writes `~/.gradle/init.d/proxy-auth.gradle`    |
| `Failed to find package 'platform-tools'`               | sdkmanager can't fetch repo manifest                | Same root cause as above                               |
| `Failed to install ... licences have not been accepted` | Missing `$ANDROID_HOME/licenses/` files             | Hook §2b writes them; or run `sdkmanager --licenses`   |
| Build picks up wrong SDK                                | `ANDROID_HOME` unset or wrong                       | Check `local.properties` and `ANDROID_HOME`            |

______________________________________________________________________

## Linting and formatting

The `SessionStart` hook installs and wires up the linting stack automatically (steps 3a-3c).
No manual setup is needed.
There is no `pre-commit` framework: every tool is installed from a trusted package registry, pinned to an exact version, and run from a checked-in git hook (`scripts/git-hooks/pre-commit`) via `scripts/lint/lint.sh`.
Nothing is git-cloned or fetched from GitHub Releases at commit time (issue #667).

### Tools installed at session start

| Tool              | Step | Source                                             | Purpose                                                                     |
| ----------------- | ---- | -------------------------------------------------- | --------------------------------------------------------------------------- |
| `ktlint`          | 3a   | Maven Central (`ktlint-cli` JAR, SHA-256 verified) | Kotlin formatting; wrapper at `~/.local/bin/ktlint` runs the JAR via `java` |
| Python lint tools | 3b   | PyPI (`scripts/lint/requirements-lint.txt`)        | `ruff`, `pre-commit-hooks` checks, `mdformat` + plugins; in `~/.local/bin`  |
| git hook          | 3c   | `core.hooksPath` = `scripts/git-hooks`             | runs `scripts/lint/lint.sh` on staged files on every `git commit`           |

The `ktlint` JAR is stored under `~/.local/lib/ktlint/`.
`ruff` version tracks the ruff-pre-commit pin (issue #673); `pre-commit-hooks` is the same upstream project as before, now installed from PyPI instead of cloned, and exposes each generic check as a console script.

`scripts/lint/requirements-lint.txt` is a fully resolved lock: every package, top-level and transitive, is pinned to an exact version and a SHA-256 hash, and pip installs it with `--require-hashes` so a substituted or tampered wheel is rejected (issue #699).
Edit the top-level pins in `scripts/lint/requirements-lint.in` and regenerate the lock with the `uv pip compile` command recorded in that file's header.
`uv` is not installed by the session-start hook; install it first with `pip install uv`.

### Checks run by `scripts/lint/lint.sh`

`scripts/lint/lint.sh` is the single source of truth for "run the linters".
It takes an explicit file list (the git hook passes the staged set) or `--all` to lint the whole tree.

| Check                       | Files             | Behavior                                      |
| --------------------------- | ----------------- | --------------------------------------------- |
| `trailing-whitespace-fixer` | text              | removes trailing spaces                       |
| `end-of-file-fixer`         | text              | ensures files end with a newline              |
| `check-yaml`                | `*.yaml`, `*.yml` | validates YAML syntax                         |
| `check-toml`                | `*.toml`          | validates TOML syntax                         |
| `check-merge-conflict`      | all               | blocks accidental conflict markers            |
| `check-added-large-files`   | staged            | blocks large file commits                     |
| `ruff`                      | `*.py`            | lint + auto-fix (E, F rules), then format     |
| `mdformat`                  | `*.md`            | format; `--wrap keep --number`; auto-corrects |
| `ktlint`                    | `*.kt`, `*.kts`   | format; auto-corrects in place                |

When a hook modifies a file, the agent did not cause that change--
stage the modified files and commit again.
The hook lints the working-tree copy of each staged file rather than stashing unstaged changes first, so a partially staged file is linted as it sits in the working tree.

### Semgrep (CI only)

Semgrep runs in CI (`.github/workflows/semgrep.yml`) on PRs and weekly.
Rulesets: `p/python`, `p/kotlin`, `p/security-audit`.
Results appear in the GitHub Security tab (SARIF upload).
Findings block the PR.
The engine is installed from `scripts/ci/requirements-semgrep.txt`, a hash-pinned lock (top-level pin in `scripts/ci/requirements-semgrep.in`), with `--require-hashes` (issue #723); the rulesets are still fetched from the Semgrep registry at scan time, so the weekly run keeps picking up new rules.
Regenerate the lock with the `uv pip compile` command recorded in that `.in` file's header.

### CI helper-script dependencies

The Python helper scripts' runtime deps (`defusedxml`, `requests`, `PyYAML`) install in CI (`.github/workflows/build.yml`) from `scripts/requirements.txt`, a hash-pinned lock (top-level pins in `scripts/requirements.in`), with `--require-hashes` (issue #723).
Regenerate the lock with the `uv pip compile` command recorded in that `.in` file's header.

______________________________________________________________________

## GitHub MCP tool quirks

### `issue_write labels: []` is a silent no-op

Calling `mcp__github__issue_write` with `labels: []` to clear all labels returns a
success-looking response (`{"id":"...","url":"..."}`) but **does not change any labels**.
The MCP tool appears to filter out empty arrays before building the API request body,
so the `labels` field is never sent and all existing labels remain.

**Evidence (empirically verified 2026-05):**

- `labels: ["planning needed"]` → correctly replaces ALL labels with just that one
  (REPLACE semantics; "orchestrate" was removed in the same call). Non-empty arrays work.
- `labels: []` → no change; all labels remain. Confirmed by a follow-up `get_labels` read.

**Implication:** There is no way to remove *all* labels from an issue using
`mcp__github__issue_write` alone. To drop N−1 labels, set `labels` to the one label
you want to keep. The last remaining label cannot be removed via this tool, but
`GITHUB_TOKEN` can remove it directly against the labels sub-resource endpoint; see
"`GITHUB_TOKEN` can write labels" below, which supersedes the read-only finding this
section used to state here.

### `GITHUB_TOKEN` can write labels (supersedes the 2026-05 finding above)

The 2026-05 empirical test above found that `GITHUB_TOKEN` returned 403 on `DELETE`
and `PATCH` against the Issues API, and concluded the token was read-only for
issue/PR writes. That conclusion does not hold for the labels sub-resource
endpoints.

**Re-verified 2026-07 (issue #710 / PR #717):** `POST /issues/{n}/labels` (add) and
`DELETE /issues/{n}/labels/{name}` (remove) both succeed with `GITHUB_TOKEN`. A
round trip on issue #710's own `P3` label (`scripts/agents/update_gh_labels.sh ... --remove P3`, then `--add P3`) was confirmed by a follow-up `get_labels` read after each
call. `scripts/agents/update_gh_labels.sh` (issue #710) relies on this, and is the
supported way to add or remove specific labels without the replace-all behavior of
`mcp__github__issue_write` (see "Applying label transitions" in
`dev_orchestration.md`).

Why the 2026-05 finding differed is unconfirmed: either the fine-grained PAT's
permissions changed since then, or the original 403 was specific to `PATCH /issues/{n}` (updating the issue resource itself) rather than the labels
sub-resource endpoints this script uses. Treat the write-permission boundary as the
labels sub-resource only; do not assume other Issues/PR API writes succeed with
`GITHUB_TOKEN` without testing them individually.

### Always verify writes with a follow-up read

`mcp__github__issue_write` (and similar write tools) return `{"id":"...","url":"..."}`
regardless of whether the underlying change was applied. The stripped response gives
no signal about what actually changed. **Always follow a write with a confirming
`issue_read`** (e.g. `method: "get_labels"`) before reporting success.

### GitHub MCP "requires re-authorization (token expired)" on write

Write operations (`add_issue_comment`, `issue_write`, etc.) occasionally fail with the following error while reads on the same resource succeed:

```text
MCP server "github" requires re-authorization (token expired)
```

#### Possible workaround (not guaranteed, appears to work)

If writes fail with this error, try performing any `issue_read` first, then immediately retry the write.
Do not interpret this as "no blind writes" enforcement.

#### Observations

`$GITHUB_TOKEN` is a fine-grained PAT (`github_pat_...` format), not a JWT.
It is stable across sessions and does not change mid-session or after MCP reads/writes.
The "token expired" error therefore **does not refer to `GITHUB_TOKEN` expiring**.
The MCP server manages its own internal credentials separately from this environment variable.
The mechanism is unknown, but reads appear to succeed via a cached path while writes require a live MCP session token.
A successful read call appears to unblock subsequent writes.

______________________________________________________________________

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
