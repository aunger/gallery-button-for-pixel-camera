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

## Outcome vocabulary

| Line emitted          | Meaning                                                                                               |
|-----------------------|-------------------------------------------------------------------------------------------------------|
| `PR#N: Clear ...`     | All CI checks passed and `mergeable_state` is `clean` or `unstable`; PR may be merged.               |
| `PR#N: Blocked ...`   | A check failed (`failure`/`action_required`) or `mergeable_state` is `behind`/`dirty`. |
| `PR#N: Infra ...`     | A CI infrastructure problem (`cancelled`, `timed_out`, `stale`, `startup_failure`, or `mergeable_state=blocked`); escalate to user. |
| `PR#N: in_progress`   | CI still running; emitted only after >120 s of silence (no other output); relay to user as a brief status update. |
| `PR#N: step "..." -> ...` | A `build-and-test` step reached a conclusion: one of the three named test steps (`Build and run unit tests`, `Run *E2ETest`), or any genuine step failure. **Informational**; surfaces *which group* finished/failed and when; never ends the loop. |
| `PR#N: FAIL [suite] name: ...` | A per-test failure (message + truncated trace) parsed from a `testresults-<group>` artifact, possibly followed by indented trace lines. **Informational**; emitted by default; suppress with `--no-include-fail`; never ends the loop. |
| `PR#N: SKIP [suite] name: ...` | A per-test skip parsed from a `testresults-<group>` artifact. **Informational**; emitted by default; suppress with `--no-include-skip`; never ends the loop. A skipped task-relevant test is a false-validation trap. |
| `PR#N: PASS [suite] name: ...` | A per-test pass parsed from a `testresults-<group>` artifact. **Informational**; suppressed by default; enable with `--include-pass [PATTERN]`; never ends the loop. |

- `step`/`FAIL`/`SKIP`/`PASS` lines are **informational test-result deltas**, not terminal outcomes.
- The Monitor reads results at **step granularity** from two polled REST signals: per-step `conclusion` (`/actions/runs/{id}/jobs`) and the `testresults-<group>` artifacts (`/actions/runs/{id}/artifacts`).
  It deliberately does **not** scrape the in-progress job log: `GET /actions/jobs/{job_id}/logs` returns 404 until the job completes, so markers are not readable mid-run that way.
