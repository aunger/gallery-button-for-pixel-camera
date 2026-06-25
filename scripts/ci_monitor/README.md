# CI Monitor

`ci_monitor.py` polls a pull request's CI and streams a terminal outcome plus per-test signals.
Each stdout line is the interface: terminal lines (`Clear`/`Blocked`/`Infra`) end the loop, while informational lines (`in_progress` heartbeat, per-step deltas, per-test `FAIL`/`SKIP`/`PASS`) keep it alive.

For how the Orchestrator uses this script as part of the development cycle (the Monitor loop, routing decisions, and the Verification Planner dispatch), see [`agents/dev_orchestration.md`](../../agents/dev_orchestration.md).

## Running the monitor

Run it from the repo root, passing the PR number via `--pr`:

```bash
python3 scripts/ci_monitor/ci_monitor.py --pr <PR_NUMBER> [filter flags]
```

`OWNER`/`REPO` default to this repo at the top of the script, and it reads `$GITHUB_TOKEN` from the environment (required).
The script catches transient REST/parse failures per call so they cannot kill the resilient poll loop.

The Monitor discovers which workflow run(s) and job(s) to track from the `/commits/{sha}/check-runs` payload, by parsing each GitHub Actions check run's `details_url` for its `(run_id, job_id)` (gated on `app.slug == "github-actions"`, with a `/actions/runs/` URL-pattern fallback when the `app` block is absent).
It does not name a workflow or job; the run/job to follow is derived from the same check-runs data that produces the verdict.

## Per-test outcome filters

By default the monitor reports **all FAIL markers**, **all SKIP markers**, and **no PASS markers**.
Independent filter flags narrow or expand which per-test outcomes are streamed:

| Flag | Effect |
|---|---|
| `--include-fail [PATTERN]` | Report FAIL markers (default); optionally restrict to those whose `name` matches PATTERN. |
| `--no-include-fail` | Suppress all FAIL markers. |
| `--include-skip [PATTERN]` | Report SKIP markers (default); optionally restrict to those whose `name` matches PATTERN. |
| `--no-include-skip` | Suppress all SKIP markers. |
| `--include-pass [PATTERN]` | Report PASS markers (not the default); optionally restrict to those whose `name` matches PATTERN. |
| `--no-include-pass` | Suppress all PASS markers (explicit form of the default). |

Each `--include-*` flag takes an **optional regex** matched against the marker's `name` field.
Supplied without a pattern it includes *all* markers of that outcome.
The three outcomes keep their distinct labels in output: `--include-pass` never relabels a SKIP as PASS.

**Task-relevance validation.** To verify that a task-relevant test actually ran and passed (rather than being silently skipped), supply `--include-pass` with a regex matching the test(s) of interest:

```bash
python3 scripts/ci_monitor/ci_monitor.py --pr <PR_NUMBER> --include-pass 'MyFeatureTest'
```

This emits a `PASS` line when the matching test passes and a `SKIP` line if it was skipped (which would be a false-validation trap: the code path was never exercised).
With no pattern (`--include-pass ''`), every passing test is reported.

## Configuration

`ci_monitor.config.json` (next to the script) tunes three behaviors as regexes.
The monitor loads it once at startup; each value is optional, and a missing file, unreadable file, invalid JSON, or non-compiling regex falls back to the in-code default for that key without aborting the resilient poll loop.

| Config key | Default (in code) | Matched against | Purpose |
|---|---|---|---|
| `artifact_name_regex` | `^testresults-` | each run artifact's `name` (via `re.search`) | Selects which artifacts are downloaded and parsed for per-test markers. |
| `interesting_step_regex` | `(?!)` (never matches) | each completed step's `name` | Selects which steps emit a `step "..." -> ...` line on a *non-failing* conclusion. A genuine step failure (`failure`/`cancelled`/`timed_out`/`action_required`) always surfaces regardless of this regex, so the never-match default means "out of the box, surface a step only when it failed". |
| `test_marker_regex` | `##TEST##` | each raw ndjson line (via `re.search`) | Locates the marker that prefixes each test's JSON payload. The payload is parsed from the *end* of the matched span. |

This repo's committed `ci_monitor.config.json` sets:

- `artifact_name_regex`: `^testresults-` (matches `testresults-unit`, `testresults-e2e-overlay`, `testresults-e2e-gallery`).
- `interesting_step_regex`: `Build and run unit tests|E2ETest` (reproduces the named-step reporting on success).
- `test_marker_regex`: `##GB4PC_TEST##|##TEST##` (back-compat dual marker, see below).

**Back-compat dual marker.**
The default read marker is `##TEST##`, but this repo's CI still emits `##GB4PC_TEST##` (see `build.yml`), so the committed config matches both (`##GB4PC_TEST##|##TEST##`).
The payload offset is computed from the end of whichever alternative matched (`re.search(...).end()`), so both markers parse correctly.
Alternation order does not matter for this pair: neither marker is a prefix of the other (`##GB4PC_TEST##` has `G` after the leading `##`, while `##TEST##` has `T`), so they can never match at the same start position, and `##TEST##` does not appear inside `##GB4PC_TEST##` at all.
The full marker is simply listed first as a readable convention.
Switching the *emit* side to `##TEST##` (the Gradle/test reporters plus the `build.yml` greps) is a separate, larger change and out of scope here; the dual-marker config is the bridge, not an end state.

## Outcome vocabulary

| Line emitted          | Meaning                                                                                               |
|-----------------------|-------------------------------------------------------------------------------------------------------|
| `PR#N: Clear ...`     | All CI checks passed and `mergeable_state` is `clean` or `unstable`; PR may be merged.               |
| `PR#N: Blocked ...`   | A check failed (`failure`/`action_required`) or `mergeable_state` is `behind`/`dirty`. |
| `PR#N: Infra ...`     | A CI infrastructure problem (`cancelled`, `timed_out`, `stale`, `startup_failure`, or `mergeable_state=blocked`); escalate to user. |
| `PR#N: in_progress`   | CI still running; emitted only after >120 s of silence (no other output); relay to user as a brief status update. |
| `PR#N: step "..." -> ...` | A step of a tracked CI job reached a conclusion: a step whose name matches the configured `interesting_step_regex` (this repo: `Build and run unit tests`, `Run *E2ETest`), or any genuine step failure. **Informational**; surfaces *which group* finished/failed and when; never ends the loop. |
| `PR#N: FAIL [suite] name: ...` | A per-test failure (message + truncated trace) parsed from a `testresults-<group>` artifact, possibly followed by indented trace lines. **Informational**; emitted by default; suppress with `--no-include-fail`; never ends the loop. |
| `PR#N: SKIP [suite] name: ...` | A per-test skip parsed from a `testresults-<group>` artifact. **Informational**; emitted by default; suppress with `--no-include-skip`; never ends the loop. A skipped task-relevant test is a false-validation trap. |
| `PR#N: PASS [suite] name: ...` | A per-test pass parsed from a `testresults-<group>` artifact. **Informational**; suppressed by default; enable with `--include-pass [PATTERN]`; never ends the loop. |
| `PR#N: drain poll found no new diagnostic signals` | Printed immediately before a `Blocked`/`Infra` terminal line when every drain poll (see below) emitted nothing new. **Informational**; flags that the terminal line that follows carries no fresh `step`/`FAIL`/`SKIP`/`PASS` evidence, distinguishing this from a terminal line that is merely missing those signals by coincidence. |

- `step`/`FAIL`/`SKIP`/`PASS` lines are **informational test-result deltas**, not terminal outcomes.
- The Monitor reads results at **step granularity** from two polled REST signals: per-step `conclusion` (`/actions/runs/{id}/jobs`) and the `testresults-<group>` artifacts (`/actions/runs/{id}/artifacts`).
  It deliberately does **not** scrape the in-progress job log: `GET /actions/jobs/{job_id}/logs` returns 404 until the job completes, so markers are not readable mid-run that way.
- The named E2E test steps (`Run *E2ETest`) always conclude `success` by design: the CI workflow wraps the test invocation in an `if`/`else` that records `outcome=success|failure` to `$GITHUB_OUTPUT` but always exits 0. A `step "Run ...E2ETest" -> success` line means only that the wrapper script exited 0, not that the contained tests passed. The actual pass/fail verdict comes from a separate gate step (surfaced as a `step "Gate on ..." -> failure` line when it fails) and from the per-test `FAIL`/`SKIP`/`PASS` markers.
- Before emitting a `Blocked` or `Infra` terminal line, the Monitor pauses briefly and re-polls the `step`/`FAIL`/`SKIP`/`PASS` signals, repeating up to a few times. The `/actions/runs/{id}/jobs` and `/actions/runs/{id}/artifacts` endpoints can lag behind `/commits/{sha}/check-runs`: the poll where check-runs first reports the failing conclusion may still show the final gate step as not yet completed, or the `testresults-<group>` artifact as not yet listed. These extra drain polls give those endpoints a chance to catch up, so a `Blocked` terminal is not reported with zero diagnostic `step`/`FAIL` lines. Every drain attempt runs (the drain does not stop at the first attempt that emits something): the two endpoints can settle on different attempts (issue #419), e.g. the gate step appears on attempt 1 while the `testresults-<group>` artifact only lists on attempt 2, and stopping at the first fruitful attempt would drop the later signal for this process's lifetime. If every drain poll comes up empty, the Monitor prints `drain poll found no new diagnostic signals` immediately before the terminal line, so a `Blocked`/`Infra` with no diagnostics is explicitly flagged rather than looking like an undiagnosed coincidence. This bounded retry mitigates short lags (a few times the per-attempt delay); a lag that outlives a single Monitor process invocation surfaces as this flagged, undiagnosed terminal, and the Orchestrator's Monitor loop (see `agents/dev_orchestration.md`) responds by waiting 5 minutes and re-launching the Monitor once for a fresh, out-of-process recheck before acting on it.
