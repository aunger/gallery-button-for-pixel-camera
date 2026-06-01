# CI Monitor loop

Binding rules for watching CI after a Reviewer approves: the Monitor loop the Orchestrator runs, the `ci-watcher` agent that runs it, the poll script's interface, the per-test outcome filters, and the line-by-line outcome vocabulary.

This material was split out of `orchestration.md` because it is the reference the `ci-watcher` agent and the Phase 4 resource (`../resources/ci-watch.md`) navigate to directly. The Orchestrator enters it from the "CI checking after a Reviewer exits" step of the workflow.

## CI checking after a Reviewer exits (Monitor loop)

After the Reviewer exits and delivers its decision, the Orchestrator acts as follows:

```
  if Reviewer requested changes → goto newAuthor
  if Reviewer gave approval:
    Orchestrator launches a Monitor tool call running `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ci_monitor.py" --pr <PR_NUMBER>` (run_in_background: true, timeout_ms: 1800000)
    Each stdout line arrives as a task-notification event
    Act only on the terminal lines Clear, Blocked, or Infra. Relay in_progress lines to the user as brief status updates (the script suppresses these unless no other output has been emitted for over 120 seconds).
    Relay `step "..." -> ...` and `FAIL [...] ...` lines to the user as informational test-result deltas; they do NOT end the loop or start a new Author round.
    if Monitor emits a Blocked line  → goto newAuthor
    if Monitor emits an Infra line   → escalate to user; stop
    if Monitor times out (30 min)    → escalate to user; stop
    if Monitor emits a Clear line:
      // Step: Surface unautomated verification tests
      // Triggered after Reviewer approval AND CI clears (Monitor emits Clear).
      // Workflow: ask → plan → review → execute
      1. Scan the issue description, PR description, and all comments on both for
         verification steps, acceptance criteria, or manual test instructions that
         are NOT already covered by automated tests.
      2. If none are found → PR may be merged.
      3. Add the `verification needed` label to the PR and/or issue where
         outstanding steps were found.
      4. Show the user the list of outstanding unautomated verification steps.
      5. Ask the user: "Do you want to run these tests manually, or have an agent
         plan automation for them?"
      6. If the user chooses manual testing or no automation → this automated orchestration is complete; PR may be merged when manual testing is complete.
      7. Spawn a fresh sub-agent (no prior conversation context) with this briefing:
           - The list of unautomated verification steps (from step 1)
           - A pointer to the existing test infrastructure (test directories,
             CI config, test framework in use)
           - Instructions: produce a concrete automation plan — describe what to
             automate and how, but do NOT implement anything yet.
         The sub-agent must receive no other context from this conversation.
      8. Reviewer check: Spawn a Reviewer agent to evaluate the automation plan
         produced in step 7. The Reviewer approves or requests changes to the plan.
         If changes are requested, route back to the planning sub-agent (step 7)
         with the Reviewer's feedback. Repeat until the plan is approved.
      9. Execute: Once the automation plan is approved by the Reviewer, dispatch
         an Author agent to implement the automation. Follow the normal
         Author → Reviewer → CI Monitor cycle for the resulting changes.
      → PR may be merged after the automation Author's work clears CI.
```

## Monitor script

The poll loop lives in this plugin at [`scripts/ci_monitor.py`](../scripts/ci_monitor.py). Run it via `${CLAUDE_PLUGIN_ROOT}`, passing the PR number via `--pr`, as the `command` for the `Monitor` tool call:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ci_monitor.py" --pr <PR_NUMBER>
```

`OWNER`/`REPO` default to this repo at the top of the script, and it reads `$GITHUB_TOKEN` from the environment. The script catches transient REST/parse failures per call so they cannot kill the resilient poll loop; the 30-minute escalation threshold is enforced by `timeout_ms: 1800000` on the Monitor call, not inside the script. Each stdout line is the interface (see the outcome vocabulary below): terminal lines (`Clear`/`Blocked`/`Infra`) end the loop, while informational lines keep it alive.

## Per-test outcome filters

By default the monitor reports **all FAIL markers**, **all SKIP markers**, and **no PASS markers**. The Orchestrator may supply independent filter flags to narrow or expand which per-test outcomes are streamed:

| Flag | Effect |
|---|---|
| `--include-fail [PATTERN]` | Report FAIL markers (default); optionally restrict to those whose `name` matches PATTERN. |
| `--no-include-fail` | Suppress all FAIL markers. |
| `--include-skip [PATTERN]` | Report SKIP markers (default); optionally restrict to those whose `name` matches PATTERN. |
| `--no-include-skip` | Suppress all SKIP markers. |
| `--include-pass [PATTERN]` | Report PASS markers (not the default); optionally restrict to those whose `name` matches PATTERN. |
| `--no-include-pass` | Suppress all PASS markers (explicit form of the default). |

Each `--include-*` flag takes an **optional regex** matched against the marker's `name` field. Supplied without a pattern it includes *all* markers of that outcome. The three outcomes keep their distinct labels in output: `--include-pass` never relabels a SKIP as PASS.

**Task-relevance validation.** To verify that a task-relevant test actually ran and passed — rather than being silently skipped — supply `--include-pass` with a regex matching the test(s) of interest:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ci_monitor.py" --pr <PR_NUMBER> --include-pass 'MyFeatureTest'
```

This emits a `PASS` line when the matching test passes and a `SKIP` line if it was skipped (which would be a false-validation trap: the code path was never exercised). With no pattern (`--include-pass ''`), every passing test is reported.

## Outcome vocabulary

| Line emitted          | Meaning                                                                                               |
|-----------------------|-------------------------------------------------------------------------------------------------------|
| `PR#N: Clear ...`     | All CI checks passed and `mergeable_state` is `clean` or `unstable`; PR may be merged.               |
| `PR#N: Blocked ...`   | A check failed (`failure`/`action_required`) or `mergeable_state` is `behind`/`dirty`; new Author round needed. |
| `PR#N: Infra ...`     | A CI infrastructure problem (`cancelled`, `timed_out`, `stale`, `startup_failure`, or `mergeable_state=blocked`); escalate to user. |
| `PR#N: in_progress`   | CI still running; emitted only after >120 s of silence (no other output); relay to user as a brief status update. |
| `PR#N: step "..." -> ...` | A `build-and-test` step reached a conclusion: one of the three named test steps (`Build and run unit tests`, `Run *E2ETest`), or any genuine step failure. **Informational** — surfaces *which group* finished/failed and when; never ends the loop. |
| `PR#N: FAIL [suite] name: ...` | A per-test failure (message + truncated trace) parsed from a `testresults-<group>` artifact, possibly followed by indented trace lines. **Informational** — emitted by default; suppress with `--no-include-fail`; never ends the loop. |
| `PR#N: SKIP [suite] name: ...` | A per-test skip parsed from a `testresults-<group>` artifact. **Informational** — emitted by default; suppress with `--no-include-skip`; never ends the loop. A skipped task-relevant test is a false-validation trap. |
| `PR#N: PASS [suite] name: ...` | A per-test pass parsed from a `testresults-<group>` artifact. **Informational** — suppressed by default; enable with `--include-pass [PATTERN]`; never ends the loop. |

- `step`/`FAIL`/`SKIP`/`PASS` lines are **informational test-result deltas**, not terminal outcomes: relay them to the user but do not start a new Author round. Only a `Blocked` line does that.
- The Monitor reads results at **step granularity** from two polled REST signals — per-step `conclusion` (`/actions/runs/{id}/jobs`) and the `testresults-<group>` artifacts (`/actions/runs/{id}/artifacts`). It deliberately does **not** scrape the in-progress job log: `GET /actions/jobs/{job_id}/logs` returns 404 until the job completes, so markers are not readable mid-run that way.
- The 30-minute escalation threshold is enforced by `timeout_ms: 1800000` on the Monitor call — no elapsed-time tracking needed.
- Do not subscribe to PR events or delay dispatching the Reviewer while waiting for CI; the Monitor loop replaces that pattern.
