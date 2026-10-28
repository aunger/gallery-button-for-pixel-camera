# The CI Monitor loop

This is the Orchestrator's routing once a Reviewer has given `LGTM` on the PR path.
Read it whole before acting; re-read it after a compaction.

The Monitor script is `${CLAUDE_PLUGIN_ROOT}/scripts/ci_monitor/ci_monitor.py`; its README (next to it) has the command syntax, the per-test outcome filters, and the full outcome vocabulary.
The Monitor knows nothing about the project: the regexes that select its artifacts, test markers, and ignored checks come from the project configuration, which is why every invocation passes `--config` and `--repo`.

## The goal is green named checks, not a mergeable branch

Making the branch mergeable is not the Orchestrator's job.
The cycle drives the PR toward consensus among the sub-agents and a green result on the **named checks**: the `named_checks` list in `${CLAUDE_PROJECT_DIR}/.claude/orchestrate.json`.
A named check is green when its conclusion is `success`, `neutral`, or `skipped`, the conclusions GitHub accepts for a required check.

A project may run a merge-gate check that is red for the whole cycle by design (the Monitor's `ignored_check_regex` leaves it out of the verdict and marks its row `[ignored]`).
It is never a named check, and no agent may act on it.

## Launching the Monitor

To launch the Monitor is to issue a `Monitor` tool call running, from the repo root:

```text
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ci_monitor/ci_monitor.py --pr <PR_NUMBER> --repo <owner/repo> --config ${CLAUDE_PROJECT_DIR}/.claude/orchestrate.json
```

with `run_in_background: true` and `timeout_ms: 1800000`, and to record the task ID it returns for use in `silentVanish` recovery.
The 30-minute escalation threshold is the `timeout_ms`; no elapsed-time tracking is needed.
Launching also clears the `silentVanish` re-launch flag, since the invocation a launch creates is not a recovery re-launch; `silentVanish` is the one site that sets the flag.

After launching, `goto monitorLoop`.

## `monitorLoop`

`monitorLoop` is reached only by `goto monitorLoop` from a site that has just launched the Monitor.

```text
monitorLoop:
  Each stdout line arrives as a task-notification event.
  Terminal lines and failure markers (`FAIL`/`SKIP`) are relayed to the user, verbatim.
  Every other line is relayed or withheld at the Orchestrator's discretion.
  A terminal line is the `PR#N: ` prefix, then its terminal word (`Clear`, `Blocked`, `Infra`, `Draft on hold`,
  `Merged` or `Closed`), then any ` by: ...` attribution and any ` (mergeable_state=...)` diagnostic.
  A terminal line means only that the stream has ended.
  `Merged`, `Closed` and `Draft on hold` report facts about the PR and are acted on as such;
  the other three words are the Monitor's verdict, and nothing routes on them.
  The decision input is the per-check summary block the Monitor prints immediately before the terminal line.
  if Monitor emits a `Merged` line -> send the user the `PR #{N} merged; ...` decision-signal line; stop
  if Monitor emits a `Closed` line -> do NOT reopen it or start a new Author round;
                                      send the user the `PR #{N} was closed without merging; ...` line; stop
  if Monitor times out (30 min)    -> escalate to the user; stop
  if a user message wakes the session before the Monitor delivers any terminal line -> goto silentVanish
  if Monitor emits any other terminal line -> goto namedChecks
```

## `namedChecks`

Each row of the summary block is `<name> .... <conclusion>` followed by optional annotations, one row per check-run name, already collapsed to that name's latest run, which is how GitHub itself judges a required check.
The Monitor ends only once every check-run it can see has completed, apart from those its config ignores, so a named check's row always carries a conclusion.

Read only the rows of the named checks.
Ignore every other row, including an `[ignored]` merge gate, and every annotation (`[BLOCKING]`, `[ignored]`, the terminal's ` by: ...` portion).
Which checks matter is policy stated by the project configuration, not something the Monitor computes.

Draftness changes what the Orchestrator says, not where it goes: a failed test is a failed test whether or not the PR can merge.
Verification runs on a draft PR, and that is intended: `verified` is a claim about before-merging requirements, not about mergeability.

Act on no `mergeable_state` you see in a terminal suffix.
A merge conflict present when the head commit is pushed stops the `pull_request` workflows from running, which surfaces here as named checks with no row.
A conflict that arises after the named checks ran is not caught: resolving it is part of merging, which is not the Orchestrator's goal.

```text
namedChecks:
  if the terminal word is `Draft on hold` -> send the user the `PR #{N} is a draft, ...` line, then continue below
  if any named check has no row -> goto missingNamedCheck
  Send the user an own-voice status message naming each named check that is not green with its conclusion,
  or saying that every named check is green.
  if any named check concluded other than green or `failure` -> escalate to the user; stop
    // `cancelled`, `timed_out`, `stale`, `startup_failure` and `action_required` mean the
    // run did not deliver a verdict, which no Author round can repair.
  if any named check concluded `failure`:
    Apply this transition to the PR:

    | Add label |
    |---|
    | `changes requested` |

    goto "Assigning a Programmer" in SKILL.md
  otherwise (every named check is green) -> goto "After the named checks are green" in SKILL.md
```

## `missingNamedCheck`

A named check with no row never registered a check-run the Monitor could see: the workflow had not started when every other check finished, the run died before creating the job, or the workflows never ran (no check-runs at all, as behind a merge conflict).
A check that registered but never concludes keeps the Monitor polling, so the 30-minute timeout covers that case instead.
This block gives a missing row one out-of-process recheck before treating it as real, so a dead run escalates rather than becoming an indefinite wait.

```text
missingNamedCheck:
  Send the user the `Rechecking PR #{N} ...` decision-signal line.
  Wait 5 minutes without a sleep loop: issue a Bash tool call running `sleep 300` (run_in_background: true),
  and treat its completion notification as the wake-up.
  Launch the Monitor.
  goto monitorLoop (on this pass, a named check with no row escalates to the user and stops instead of
  re-entering missingNamedCheck, so the recheck gets at most one detour)
```

## `silentVanish`

The Monitor task can silently vanish: the process exits without the task-notification infrastructure delivering any terminal line, not even a timeout notification, which leaves the session stuck until the user sends a message.
A re-launch after a confirmed vanish goes through `monitorLoop` with all its normal checks, including `missingNamedCheck` if warranted.

Every user-message wake-up during the loop comes here, so a second vanish re-enters the block from the top.
The block therefore tracks whether the current Monitor invocation is itself a re-launch: the flag tells a first vanish (re-launch) from a second (escalate), and a still-alive task never escalates.

The user message that brings you here is a wake-up event only.
Its content, if any, is set aside: the Orchestrator's narrow scope during CI monitoring means user questions or instructions cannot be addressed mid-monitor.
Inform the user of CI status instead.

```text
silentVanish:
  if TaskOutput for the Monitor's task ID returns "No task found with ID: <id>":
    // Confirmed vanish: the task record was dropped.
    if the current Monitor invocation is itself a silentVanish re-launch (the re-launch flag is set):
      -> escalate to the user; stop
    Inform the user that the Monitor task vanished without a terminal notification and is being re-launched.
    Launch the Monitor. Set the re-launch flag for the invocation it creates, overriding the clear a launch otherwise performs.
    goto monitorLoop
  else (the Monitor task is still registered; a user message is not proof of a vanish):
    Inform the user that CI is still running and the Monitor is alive; keep waiting for the terminal line; do not re-launch.
```

## Notes

- The per-check summary rows of the named checks are the decision input; the terminal line only marks the end of the stream. `step`/`FAIL`/`SKIP`/`PASS` lines are progress reports.
- The Verifier still reads the terminal words (its definition, "Monitor workflow runs"). That is deliberate: the Monitor keeps emitting them, and only the Orchestrator's routing stopped treating them as a verdict.
- The Monitor loop replaces subscribing to PR events and sleep-plus-poll, which are unreliable.
