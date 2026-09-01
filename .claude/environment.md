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

## The environment Setup script (optional)

`.claude/setup-environment.sh` is the checked-in copy of the block that goes in the **Setup script** field of the Claude Code for Web environment configuration (issue #792).
That field runs as root once per environment build, before any session starts, and its filesystem output is cached across sessions.

It is optional.
Without it, sessions still work: the `SessionStart` hook provisions everything a build needs on its own.
With it, two things stop being luck:

- **The Gradle distribution.** The hook cannot cache it across sessions, so every session re-downloads it from `services.gradle.org`, which redirects to a `github.com` release asset. Whether the session proxy allows that has been observed to vary (hard-blocked in 2026-07 sessions, open in 2026-08 ones; see the comments on #774). The Setup script seeds the wrapper's own cache, so `./gradlew` starts offline.
- **The JDK.** The base image ships JDK 21; CI and the generator workflow build on Temurin 17. The Setup script installs Temurin 17 to `/opt/java/temurin-17`, and the hook (STEP 0b) points `JAVA_HOME` there when it finds it. This matches CI at the major version, which is what makes a local run reproduce CI's Java behaviour; the workflows ask for `java-version: '17'` and float to the newest 17.x, while the script pins an exact build, so patch levels will diverge and nothing detects that.

It also provisions the Android SDK once into the cached image instead of once per session, using the same pins as the hook, whose skip guards then turn those steps into fast no-ops.

The Setup script and the session must agree on one home directory, because the wrapper looks for its cached distribution under the session's home.
Here they do: sessions run as root with `HOME=/root`, which the script's default follows.

The script cannot confirm that agreement, and does not try to.
It fails only when the resolved home does not exist, which is not the case that matters: if sessions move to another user while the Setup script still runs as root, `/root` is a perfectly good directory, so the script would seed `/root/.gradle`, report success, and provision a home no session ever reads.
What it does instead is state its answer, as the first line of its output:

```text
[setup-environment] Provisioning for session home /root (owner root)
```

So if sessions ever run as a different user, set `SESSION_HOME` in the pasted block.
Nothing will stop you if you forget; that log line is the only place it shows, which is why it is worth reading after a rebuild.

### Installing it

Paste the entire contents of `.claude/setup-environment.sh` into the environment's Setup script field, then rebuild the environment.
The script is idempotent, so a rebuild over an already provisioned image is fast.
Each step skips only when the work is already done *at the pinned version*, so a rebuild after a version bump reinstalls rather than reporting a stale install as present.

### Keeping it in sync

The pasted copy lives in a web form that CI cannot read, so **re-paste it whenever `.claude/setup-environment.sh` changes**, most importantly on a Gradle version bump.
A stale copy fails quietly: it seeds the previous distribution, the wrapper ignores it, and sessions silently go back to downloading.

`scripts/test_setup_environment.sh` guards the half CI can see.
It fails the build when the committed copy drifts from `gradle/wrapper/gradle-wrapper.properties` (version, distribution URL, checksum) or from `.claude/hooks/session-start.sh` (command-line tools URL, `ANDROID_HOME`, SDK package list, license hashes, Temurin path), when the checksum verification stops being fail-closed, when the wrapper cache path stops matching the one the wrapper actually reads, or when a step would skip its work on an image already holding a different version.

### What it deliberately does not do

- It never regenerates `gradle/verification-metadata.xml`. The generator workflow is that file's only provenance (#774).
- It provisions no emulator. The E2E suites need KVM, which web sessions do not have, so they stay CI-only.
- It installs no pip packages (#806). Both hash-pinned locks (`scripts/lint/requirements-lint.txt` and `scripts/requirements.txt`) are installed per session by the hook, into the session user's `~/.local`. Seeding them here would inherit the `SESSION_HOME` guess above, and a wrong guess would be invisible: the session would simply install them itself and look no different, where a mis-seeded Gradle at least shows up as a download. It would also put a second copy of the marker logic in a script CI can only partly verify, and to be coherent it would have to seed both locks, including the much larger lint one (about 31 MB installed, nearly all of it `ruff`). What that buys back is one `pip install` per session, about 5 MB installed for the helper deps and about 31 MB for the lint tools, from PyPI, which is not the path that has actually proved flaky here. That is the same cost this script exists to eliminate for the ~130 MB Gradle distribution and ~190 MB JDK, at a scale where it does not pay.
- It never trusts a hash published by whatever served the bytes. `GRADLE_DIST_DOWNLOAD_URL` and `TEMURIN_DOWNLOAD_URL` can redirect those *downloads* to a mirror if a host is blocked; the pinned SHA-256 still gates what that mirror serves.

### What is and is not checksummed

The script makes three downloads, and two of them are checksum-pinned:

| Download                   | Step | Verification                                 |
| -------------------------- | ---- | -------------------------------------------- |
| Gradle distribution        | 2    | SHA-256 pinned in the script, fail-closed    |
| Temurin 17                 | 1    | SHA-256 pinned in the script, fail-closed    |
| Android command-line tools | 3a   | **Not checksummed**; version-pinned URL only |

The command-line tools are the exception because Google publishes no stable checksum for that archive, and the SDK packages `sdkmanager` subsequently fetches are verified by `sdkmanager` against its own repository manifest.
This is the same trust boundary the `SessionStart` hook and the generator workflow already operate on (#774).
It is stated explicitly because "everything here is checksummed" would be false, and this is the wrong place to be loose about which bytes are verified.

The Gradle pin in particular carries more weight than it looks.
The wrapper verifies a distribution it downloads against `distributionSha256Sum`, but a distribution it finds already installed is taken as given, and the zip is deleted once unpacked, so nothing is left to re-check.
Seeding the cache moves that verification from the wrapper into the Setup script, which is why the script's check is fail-closed and why its pin is guarded by `scripts/test_setup_environment.sh`.

______________________________________________________________________

## Environment variables

| Variable            | Value                      | Set by             | Notes                                                         |
| ------------------- | -------------------------- | ------------------ | ------------------------------------------------------------- |
| `ANDROID_HOME`      | `/home/user/android-sdk`   | hook + `~/.bashrc` | Required by Gradle Android plugin and `adb`                   |
| `JAVA_HOME`         | `/opt/java/temurin-17`     | hook (§0b)         | Only when the Setup script provisioned it; else image default |
| `JAVA_TOOL_OPTIONS` | *(modified, not replaced)* | hook + `~/.bashrc` | Strips `*.google.com` from `nonProxyHosts` (see script §0)    |
| `PATH`              | `+$ANDROID_HOME/...`       | hook + `~/.bashrc` | Adds `sdkmanager`, `adb` to path                              |
| `GITHUB_TOKEN`      | *(fine-grained PAT)*       | container          | Use with `curl` to query the GitHub REST API                  |

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

That table is the lint stack only, not everything the hook installs: step 4 also installs the Python helper scripts' runtime dependencies, covered in its own section below.

The `ktlint` JAR is stored under `~/.local/lib/ktlint/`.
`ruff` version tracks the ruff-pre-commit pin (issue #673); `pre-commit-hooks` is the same upstream project as before, now installed from PyPI instead of cloned, and exposes each generic check as a console script.

`scripts/lint/requirements-lint.txt` is a fully resolved lock: every package, top-level and transitive, is pinned to an exact version and a SHA-256 hash, and pip installs it with `--require-hashes` so a substituted or tampered wheel is rejected (issue #699).
Edit the top-level pins in `scripts/lint/requirements-lint.in` and regenerate the lock with the `uv pip compile` command recorded in that file's header.
Regeneration is also how a transitive security fix reaches a lock, and the plain command will not pick one up; the `--upgrade` note in that header explains why (issue #788), and the same note appears in the other two `.in` files.
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

### Requirements audit (CI only)

`.github/workflows/dependency-audit.yml` runs `scripts/ci/audit_requirements.py` on PRs, on pushes to `main`, and weekly.
It discovers every `scripts/**/requirements*.txt` and runs `pip-audit` over the pins in each, so a lock added later is audited without being registered anywhere (issue #804).
That covers all four locks: the two the session-start hook installs, the semgrep engine's, and the auditor's own.

`pip-audit` installs from `scripts/ci/requirements-audit.txt`, a hash-pinned lock of its own (top-level pin in `scripts/ci/requirements-audit.in`).
It is deliberately CI-only, like the semgrep engine above and unlike the two locks in the section below, so it is not provisioned for sessions and `scripts/install-pinned-requirements.sh` does not install it.
Keeping it out of `scripts/lint/requirements-lint.in` keeps its 29-package closure out of every session and out of the four CI steps that install the lint lock.

The check fails on any finding without an entry in `scripts/ci/requirements-audit-ignore.toml`, and equally on any entry in that file that is *no longer* reported.
The second half is the point: a stale entry usually means an upstream cap lifted, which is when the reasoning it records needs re-reading.
Entries are per-lock and must state both why the finding is tolerated and what would make the entry unnecessary; see the file's header.
This list is not the Dependabot dismissal list and is not kept in sync with it--the two advisory databases do not carry the same set.

Take a fix by regenerating the affected lock with `--upgrade` (see the note in each `.in` file header), not by adding an ignore entry.

______________________________________________________________________

## Python helper-script dependencies

The Python helper scripts' runtime deps (`defusedxml`, `requests`, `PyYAML`) come from `scripts/requirements.txt`, a hash-pinned lock (top-level pins in `scripts/requirements.in`), installed with `--require-hashes` (issue #723).
Regenerate the lock with the `uv pip compile` command recorded in that `.in` file's header.

Both sides install it: CI in `.github/workflows/build.yml`, and the `SessionStart` hook in step 4 (issue #806).
The session install is what makes the whole test suite runnable in a session; without it, four of the Python test modules and `scripts/ci/test-support/test_summarize_preflight_integration.sh` fail with `No module named 'defusedxml'` whatever the change under test is.
The hook returns immediately unless `CLAUDE_CODE_REMOTE=true`, so a checkout on your own machine is not covered by it: install the lock there yourself with `pip install --force-reinstall --require-hashes -r scripts/requirements.txt`, the command CI runs.
The install also makes the versions this repository declares the ones a session runs: `requests` and `PyYAML` happen to resolve from the base image, so a change there would extend the same failure to them with no other signal.
`--force-reinstall` is what guarantees that: without it, pip leaves a package alone when the runner image already ships the pinned version, and CI would silently run the image's copy instead of the artifact the lock names (issue #810).
CI and the hook now match on both `--require-hashes` and `--force-reinstall`; only `--user` differs, because CI has no per-session home to isolate into and installs straight into the runner's own site (`build.yml`'s `shell-tests` job installs the lint lock into a dedicated venv instead, where `--user` is not even valid; `lint.yml` relies on `sysconfig`'s own scripts path, which a `--user` install would not populate).
`scripts/test_install_pinned_requirements.sh` case (i) checks every CI install line still carries `--force-reinstall`.

Steps 3b and 4 both install through `scripts/install-pinned-requirements.sh`, which records the SHA-256 of the lock it installed under `~/.local/share/gb4pc/`.
A re-run against an unchanged lock is therefore a no-op, an edited lock reinstalls, and a failed install writes no marker and is retried.
That marker sits in the session's own home rather than in the cached image, so it makes the hook cheap to re-run within a container; it does not carry across sessions, and a later session installs again.
`scripts/test_install_pinned_requirements.sh` covers that behavior, and fails the build if `build.yml` installs a lock the hook does not.

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

(See "`GITHUB_TOKEN` is inert in a session" below, which reframes this section: in
a session the token's value is not what authenticates, and the unexplained
disagreement above is most likely a change in the credential the proxy injects.)

### `GITHUB_TOKEN` can write issue dependencies and sub-issues

**Verified 2026-09-01 (issue links / `scripts/agents/link_gh_issues.py`):** both
per-issue *relationship* families are writable with `GITHUB_TOKEN`, extending the
labels finding above.

- **Dependencies.** `POST /issues/{n}/dependencies/blocked_by` and
  `DELETE /issues/{n}/dependencies/blocked_by/{issue_id}` both succeed. Confirmed
  by creating a real link with
  `scripts/agents/link_gh_issues.py add aunger gallery-button-for-pixel-camera 986 --blocked-by 985`,
  then reading it back from both sides: `blocked_by` on #986, `blocking` on #985,
  and the `issue_dependencies_summary` counters on each. #986 already stated that
  dependency in prose under a "Blocked by #985" heading, so the link records
  something the issue text had no way to make queryable. It was left in place.
- **Sub-issues.** `POST /issues/{n}/sub_issues` and `DELETE /issues/{n}/sub_issue`
  both succeed. Confirmed on two throwaway issues (#998, #999) through a full
  add / read-back / remove / re-add cycle in both directions, checked against
  `sub_issues_summary` on the parent and `GET /issues/{n}/parent` on the child.

**The 403 trap, which cost this investigation a wrong conclusion.** These endpoints
answer `403 Resource not accessible by integration` when the *relationship the
request names does not exist* -- for example removing a sub-issue from an issue
that is not its parent, or a dependency that was never added. That is a statement
about the operand, not about the token. Back to back, with one token:

```text
DELETE /issues/998/sub_issue  {"sub_issue_id": <#986, not a sub-issue>}  -> 403
DELETE /issues/998/sub_issue  {"sub_issue_id": <#999, really a sub-issue>} -> 200
```

An earlier draft of this section read the 403 as proof that sub-issue writes were
unpermitted and told readers to go find a PAT. Both halves were wrong. Do not treat
this wording as a permission verdict without a control like the pair above. Note
also that GitHub uses the word "integration" here even for a fine-grained PAT, so
it is not evidence about what kind of credential is in play either.

**These endpoints take a database id, never an issue number.** A number silently
addresses the wrong issue, or none. `link_gh_issues.py` resolves references
(`123`, `#123`, `owner/repo#123`, a URL) through a `GET` before writing, and reads
the current link set first, so it never issues a removal for a link that is absent
-- which is what keeps callers clear of the 403 above.

### `GITHUB_TOKEN` is inert in a session: the proxy supplies the credential

**Verified 2026-09-01.** This reframes every "`GITHUB_TOKEN` can/cannot do X"
claim above. In a session, `api.github.com` is not reached directly: `HTTPS_PROXY`
points at a local proxy (`127.0.0.1:45501`) that terminates the connection
(`curl` reports `remote_ip 127.0.0.1`) and supplies its own credential.

The value of `GITHUB_TOKEN` does not participate. Real token, a garbage token, and
no `Authorization` header at all are indistinguishable, on reads and on writes:

```text
GET  /user                                    real -> aunger   garbage -> aunger   none -> aunger
POST /issues/998/dependencies/blocked_by      real -> 404      garbage -> 404      none -> 404
```

A missing or invalid credential would be `401` on both. It never is.

Three consequences, in descending order of how much time they will save:

- **`GET /user` proves nothing about the credential here.** It answers `aunger`
  with no credential presented. Do not use it to identify what is authenticating.
- **Write identity depends on the path taken, and is not `GITHUB_TOKEN` either
  way.** Writes through `curl`/`urllib` land as `claude[bot]`: issues #998 and
  #999 were created that way and carry that author. Writes through the
  `mcp__github__*` tools land as `aunger`: PR #1000 was created that way and
  carries that author. So `scripts/agents/*` (all of which use `curl` or `urllib`)
  post as the bot, while the MCP tools post as the repository owner.
- **The proxy also blocks whole API paths on its own**, answering with an
  Anthropic-branded message and a `docs.anthropic.com` link rather than a GitHub
  one. `/installation/repositories`, `/user/repos`, `/notifications` and
  `/repos/{o}/{r}/collaborators` are all refused this way. A 403 whose
  `documentation_url` points at Anthropic is the proxy; one pointing at
  `docs.github.com` is GitHub.

**This likely settles the open question in the labels section above.** That section
records that the 2026-05 finding (403 on `PATCH`/`DELETE`) and the 2026-07 finding
(labels writable) disagree, and calls the reason unconfirmed, guessing at a change
in "the fine-grained PAT's permissions". The likelier explanation is that no PAT
was ever involved: both tests measured whatever credential the proxy injected on
the day they ran, and that changed between them. It can change again without
anyone touching a token or a repository setting, which is the real reason those
sections keep needing re-verification.

**Scope, and what is not established.** All of the above is about a *session*.
`scripts/agents/*` are session-only -- no workflow invokes them -- so this is the
environment that governs them, but the same reasoning does not carry to
`.github/workflows/`, where `GITHUB_TOKEN` is a genuine Actions token. What
`GITHUB_TOKEN` actually is remains unidentified: it has the 93-character
`github_...` shape of a fine-grained PAT, but its shape is not evidence about what
authenticates, and settling it would mean bypassing the proxy, which this
environment says not to do. Also untested: `PATCH /issues/{n}` on an issue authored
by someone other than the write identity. `PATCH` itself works (#998 and #999 were
closed with it), and relationship writes on another author's issues work (the
#986 dependency link), but those are different endpoints and the gap is real.

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
