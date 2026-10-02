# Development orchestration

## Know if you are the Orchestrator

If you are addressing a GitHub issue or PR but have not been given a specific role (Programmer, Author, Reviewer, etc.), then you are the **Orchestrator**.

**This document holds RULES for the Orchestrator, not suggestions. They aren't negotiable.**

## The Orchestrator's goal is consensus and green named checks, not a mergeable branch

Making the branch mergeable is not the Orchestrator's job.
It drives the pull request toward consensus among the sub-agents and a green result on these **named checks**:

- `build-and-test`
- `pip-audit`
- `shell-tests`
- `check-diff`

These are the required checks on `main` (per `GET /repos/{owner}/{repo}/rules/branches/main` on 2026-09-18) less one.
`No blocking labels` is left off deliberately: it is the merge gate, it is red for the whole of every cycle by design, and no agent may act on it (see "Orchestrator may not").
`ignored_check_regex` in `scripts/ci_monitor/ci_monitor.config.json` leaves it out of the Monitor's verdict too.
A change that adds or removes a required check updates this list in the same change.
No automated guard checks the list: `workflow_job_names` in `scripts/lib/workflow_yaml.sh` emits job ids rather than the check names a `name:` override produces, and silently drops some jobs (#956).

A named check is green when its conclusion is `success`, `neutral`, or `skipped`, the conclusions GitHub accepts for a required check.

## Before launching: extra information belongs in the issue

If the User attempts to launch the development cycle but provides extra information, **do not launch the development cycle or enter the Orchestrator role yet.**

Inform the User that details must appear in the issue description or comments.
Offer to append the description with the extra information before launching the orchestration.

## Orchestrator communication discipline

The Orchestrator is a message-passer between the user and the sub-agents: mute toward a sub-agent, and toward the user limited to the messages the lists below permit.
These rules are absolute:

1. The only words the Orchestrator may send to a sub-agent are (a) the user's exact words, quoted verbatim, or (b) exact words copied from a file in `agents/`.
   No other content of any kind.
2. In a message to a sub-agent, the Orchestrator does not summarize, paraphrase, interpret, reword, or add context, analysis, or background.
   Sub-agents must start fresh, uninfluenced by the Orchestrator.
3. Relay direction: the Orchestrator may relay between the user and either sub, in either direction.
   It may carry the user's words to a sub, and a sub's words back to the user.
   It must never carry one sub-agent's words to another sub-agent.
   If two sub-agents need to communicate, they leave each other GitHub comments.
4. The Orchestrator reads only the titles, labels, and open/closed states of the issue and of the PR (it fetches no diff, description, comments, mergeability, or check-run results).
   The Orchestrator does not read source files.
   Its only window into CI is the CI Monitor (`scripts/ci_monitor/ci_monitor.py`): no PR-activity subscription, no job log, no fetching PR state on a wake or a timer.
   Reading the per-check rows the Monitor emits is permitted, and they are its CI decision input; fetching check-run state from GitHub is not.
   The issue number comes from the user and is plugged into the launch form as a literal token.
5. Permitted-words test: before sending anything to a sub-agent, verify each sentence is either the user's exact words or an exact quote from an `agents/` file.
   If it is neither, do not send it.

## What Orchestrators may and may not do

The Orchestrator is not a Reviewer or a Programmer.

### Orchestrator may not

- Read source files (Read, Bash cat/grep, etc.)
- Read the PR or the issue beyond their titles, labels, and open/closed states
- Hold a PR-activity subscription or set a timer to re-fetch PR state
- Remove a label, or take any other step, to turn the `No blocking labels` check green: that would circumvent the one mechanism that keeps an agent from taking the merge onus on.
- Edit or write files
- Diagnose bugs or evaluate code
- Make git commits or push changes
- Create PRs
- Apply fixes when an agent leaves work incomplete
- Give technical advice
- Summarize, paraphrase, or supply context to a sub-agent
- Carry messages between sub-agents
- Reword or provide interpretations of instructions to a sub-agent

### Orchestrator may

- Create local Git branches to keep tasks separate
- Add or remove GitHub labels per the transition tables in this document
- Read project instructions (AGENTS.md and the files it references)
- Dispatch and communicate with subagents
  - Replace sub-agents, reluctantly and when necessary, to complete a workflow
  - Inform sub-agents of unfinished tasks or additional responsibilities
- Relay the user's exact words to sub-agents verbatim, and sub-agents' words back to the user verbatim (never between two subs)
- Relay CI Monitor output lines to the user, verbatim
- Send the user the fixed Orchestrator-to-user lines from "Decision-signal templates" below, quoted exactly; those are the Orchestrator's own words rather than a relay
- Send the user a composed own-voice status message, where this document instructs a message and no template line covers it, saying only what that instruction asks for and drawing only on what the Orchestrator is permitted to know
- Provide reminders about which process document(s) to read

## What Authors and Reviewers may and may not do

### May not

- Act on information **generated by** the Orchestrator

### May

- Act on information **relayed by** the Orchestrator (the user's exact words, verbatim)
- Read GitHub comments left by other sub-agents directly on the PR or issue

## Inaugurating work for a hitherto unworked issue

- See `inaugurate.md` for the full protocol when starting fresh work.

## Applying label transitions

Apply every "Remove label" / "Add label" transition table in this document with `scripts/agents/update_gh_labels.sh`, not `mcp__github__issue_write`.
That MCP tool's `labels` field is a replacement set: it overwrites the issue's or PR's entire label list, so it silently discards any label another agent, a workflow, or a human applied since you last read the labels (issue #710).
`scripts/agents/update_gh_labels.sh` instead calls GitHub's delta label endpoints, adding and removing only the specific labels you name, so a transition never touches any label outside its own row.

Run one call per transition row, passing every "Remove label" entry as a `--remove` flag and every "Add label" entry as a `--add` flag.
For example, the "Starting to orchestrate a PR" transition below becomes:

```text
scripts/agents/update_gh_labels.sh {owner} {repo} {issue-or-PR number} --remove orchestrate --add orchestrating
```

Run it once per artifact a transition's note tells you to apply to (issue, PR, or both).
See the script's own `--help` text for full usage and the required `GITHUB_TOKEN` environment variable.

If `scripts/agents/update_gh_labels.sh` exits non-zero, the transition did not fully apply--do not treat it as done.
Retry the same call once, since a non-2xx GitHub response can be transient.
If it still fails, fall back to `mcp__github__issue_write` for this one transition rather than escalating: read the current labels (`issue_read` with `method: "get_labels"`), apply this transition's Remove/Add columns to that list locally, and write the resulting set.
Do not stop the automated cycle or escalate to the user over a label transition alone; the replace-all race this document otherwise avoids is an acceptable one-off cost here, and getting the work finished matters more than a label.

## Starting to orchestrate a PR

When you begin orchestrating a PR (the first thing you do once you have entered the Orchestrator role for a given issue and its PR), apply this transition to **both the issue and the PR**:

| Remove label  | Add label       |
| ------------- | --------------- |
| `orchestrate` | `orchestrating` |

End any PR-activity subscription, using MCP `unsubscribe_pr_activity` tools or similar.
Disregard any PR-activity event that still arrives.

## Model selection

For each sub-agent role, use the first rule that applies:

1. **User-specified**: the user named a model for this role--use it.
2. **Label-based**: the work item carries a `c-a-<model>` label--use that model for the Author; a `c-r-<model>` label--use that model for the Reviewer.
3. **Default**: Opus.

## Dispatch template

Use this template verbatim when dispatching any sub-agent.
Fill only the tokens in braces; do not add any other words.

```text
**{Role assignment statement}**
GitHub repository: {owner-slash-repo or URL}
Issue: {#xxx or "None"}
PR: {#xxx or "None"}
Git branch: {branch name or "None"}
Verification Planner comment: {numeric comment id, or "None"}
```

Role assignment statements (copy the applicable line exactly):

- Programmer: "You are a Programmer resolving the linked issue.
  You *must* start your turn by re-fetching the description and all comments on the issue and, if it exists, on the PR (on a PR, all three comment surfaces: the issue-comment stream, the review bodies, and the inline review threads).
  If no PR exists, create one before you exit, unless the issue warrants declining to open a PR (see "Declining to open a PR" in `pr_participation.md`)."
- Reviewer: "You are a Reviewer ensuring high quality and adherence to the development plan for the linked issue. You *must* start your turn by re-fetching the description and all comments on the issue and on the PR (on a PR, all three comment surfaces: the issue-comment stream, the review bodies, and the inline review threads)."
- Verification planner: "You are a Verification Planner: scan the linked issue and PR for outstanding before-merging requirements and file a tracking issue, linked to the PR, for each one. You *must* start your turn by re-fetching the description and all comments on the issue and on the PR (on a PR, all three comment surfaces: the issue-comment stream, the review bodies, and the inline review threads)."
- Verification agent: "You are a Verification Agent: carry out the before-merging steps from the Verification Planner report on the linked PR, automating them where possible, and report results. You *must* start your turn by re-fetching the Verification Planner comment on the PR and each before-merging tracking issue it lists."

The Programmer statement names the decline path so the dispatched Programmer receives it in the copied line, not only by reading `pr_participation.md`.
An Author that legitimately declines satisfies its exit obligation by posting the explanatory issue comment and reporting that it opened no PR, pointing to that comment (see the Author work-location report below), instead of creating a PR.

## Decision-signal templates

When routing control signals, use these exact lines and no others.
Fill only the tokens in braces.

Author-to-Orchestrator work-location report (the Programmer states where its work product is when it finishes a round, reporting only what it did):

- If it opened a PR, it gives the PR number.
- If it opened no PR, it points to the explanatory comment it posted on the issue (see "Declining to open a PR" in `pr_participation.md`).

The Author is not making a branching decision, so it uses no fixed phrase; a plain location report suffices.
The Orchestrator derives the branch from whether a PR number is present: a PR number selects the PR path, its absence the no-PR path.
When no PR number is present, dispatch the Reviewer pointed at the **issue**, not a PR (see "Assigning a Reviewer").
The Orchestrator does not read or judge the Author's explanation; it only notes which artifact the Reviewer must examine.

Reviewer-to-Orchestrator outcome vocabulary (the Reviewer emits one):

- `LGTM`: the committed changes are correct and complete.
  If extra work outside the repo is required before merging, it should be explained clearly in a PR comment.
- `Changes requested`: the Author is asked to take another turn, to correct or complete its prior work.
- `Cannot work`: the coding phase cannot be completed--the requirements are incomplete, unattainable, or self-contradictory, or no code change could address the issue.
  The Reviewer describes the specifics in a PR comment, or in an issue comment on the no-PR path (where there is no PR to comment on).

Orchestrator-to-user lines for the facts a Monitor terminal reports about the PR itself:

- `PR #{N} merged; no CI outcome left to act on.`
- `PR #{N} was closed without merging; not reopening it or starting another round.`
- `PR #{N} is a draft, so it cannot merge until someone marks it ready for review.`

What the named checks concluded has no template line; the Orchestrator reports it in a composed own-voice message (see `namedChecks`).

Orchestrator-to-user status lines.
These are the Orchestrator's own words rather than a relay, so they are quoted from here:

- `Rechecking PR #{N} once before acting; a named check has not reported.`

Orchestrator escalation/abort line:

- `Stopping the automated cycle and escalating to you: {reason token}.`

The Orchestrator routes the Reviewer's chosen signal verbatim.
It does not relay the Reviewer's review prose to the Author; the Author reads the review from GitHub.

Verification Agent outcome vocabulary (a dispatched Verification Agent emits one):

- `Verification passed`
- `Verification revealed an error`
- `Verification incomplete`

Routing on the Verification Agent's signal:

- `Verification passed`: every before-merging item was confirmed automatically.
  This *before-merging requirements* process is complete. The PR is not mergeable yet: the `No blocking labels` check stays red until "Concluding PR orchestration" removes `orchestrating`.
  If the PR is a draft, removing that label is not sufficient either: a draft PR cannot merge until someone marks it ready for review, and that is the user's call.
  Apply this transition to the PR:

  | Remove label          | Add label  |
  | --------------------- | ---------- |
  | `verification needed` | `verified` |

- `Verification revealed an error`: route the PR back to a new Author round (goto "Assigning a Programmer" below).
  The Verification Agent leaves its diagnosis as a PR comment, which the Author reads from GitHub; the Orchestrator does not relay it.
  Apply this transition to the PR:

  | Remove label          | Add label           |
  | --------------------- | ------------------- |
  | `verification needed` | `changes requested` |

- `Verification incomplete`: no item failed, but one or more before-merging items could not be automated and remain open for a human to verify.
  Do not treat the PR as cleared and do not start a new Author round.
  Inform the user that manual verification is still required, and that the PR may be merged once every open before-merging tracking issue is resolved.
  If the PR is a draft, that last part is not true of it: resolving every tracking issue still leaves a draft PR unmergeable, so say that marking it ready for review is also needed and is the user's call.
  Apply this transition to the PR only if needed. The `verification needed` label is probably already applied:

  | Add label             |
  | --------------------- |
  | `verification needed` |

## Assigning a Programmer

- If an Author that has **worked on this issue or PR in a previous round** is available...
  - ...THEN resume that existing Author rather than spawning a replacement (see "Delegation rules" below).
  - ...OTHERWISE, create a sub-agent of the Author model (see Model selection above).
  - **Whether resuming or creating, dispatch using the dispatch template** above, with tokens for the Programmer role assignment statement, the branch name, and the issue and/or PR numbers.
- Use a dedicated per-issue branch for the Programmer to use.
  - Branch names should follow the pattern `fix/issue-N-short-description` for bug fixes or `feature/issue-N-short-description` for new features.
  - IF the branch already exists because this is a resumption of earlier work, reuse the one this issue was given rather than creating a second ("One branch per ticket" in "Delegation rules" below).
  - OTHERWISE, create a branch for new development.
  - Never direct two Programmers for unrelated issues to the same branch.
- Always dispatch the Programmer in its own git worktree (Agent tool `isolation: "worktree"`), whether or not this dispatch is parallel.

When the Author returns, apply this transition.
Apply it to the PR if one exists; apply it to the issue otherwise, since that is where the work's labels live when there is no PR:

| Remove label        | Add label      |
| ------------------- | -------------- |
| `changes requested` | `changes done` |

## Assigning a Reviewer

- Create a sub-agent of the Reviewer model (see Model selection above)
- Dispatch using the dispatch template above, with the Reviewer role assignment statement and the literal issue number token.
- If no PR exists, the Reviewer examines the Author's explanatory comment on the **issue** rather than a PR.
  The dispatch template's issue token already points the Reviewer there; the Reviewer follows "Reviewing an Author who declined to open a PR" in `pr_participation.md`.

## Author disagreement

If the Author disagrees with a review point, they should make their case in PR comments rather than acquiescing.
When the round is running on the no-PR path (the Author declined to open a PR), the Author makes its case in **issue** comments instead, since that is where the review lives.

## After a Reviewer exits

After the Reviewer exits and delivers its decision, the Orchestrator acts as follows.

When the Reviewer returns, apply this transition.
Apply it to the PR if one exists; apply it to the issue otherwise, since that is where the work's labels live when there is no PR:

| Remove label   |
| -------------- |
| `changes done` |

If the Reviewer requested changes, additionally apply this transition to the same artifact (PR or issue):

| Add label           |
| ------------------- |
| `changes requested` |

Whether a PR exists decides which routing applies.
If no PR exists, there is no diff or CI to run, so follow the no-PR routing below; if a PR exists, follow the PR routing (Monitor loop) below.
Decide it afresh each round rather than carrying the last round's answer over: an Author may have opened a PR this time, or closed the one it opened and switched to declining (see "Changing position" in `pr_participation.md`).

No-PR routing:

```text
  if Reviewer requested changes -> goto "Assigning a Programmer" above
  if Reviewer gave `Cannot work` -> escalate to user; stop
  if Reviewer gave LGTM:
    The issue is resolved without a code change, and the Reviewer agreed.
    Do NOT launch the CI Monitor and do NOT dispatch a Verification Planner; both presuppose a PR.
    Inform the user that the Author and Reviewer agreed the issue needs no PR, and that the loop is complete.
    The issue may now be closed by the user (the Orchestrator does not close it).
    stop
```

The PR routing below is split by labelled block: each block has its rationale in prose, followed by a fence that holds only its routing.

PR routing (Monitor loop):

To launch the Monitor is to issue a Monitor tool call running `python3 scripts/ci_monitor/ci_monitor.py --pr <PR_NUMBER>` from the repo root (`run_in_background: true`, `timeout_ms: 1800000`), and to record the task ID it returns for use in `silentVanish` recovery.
Launching also clears the `silentVanish` re-launch flag, since the invocation a launch creates is not a recovery re-launch.
`silentVanish` is the one site that sets the flag, and says so where it launches.

```text
  if Reviewer requested changes -> goto "Assigning a Programmer" above
  if Reviewer gave `Cannot work` -> escalate to user; stop (do NOT route to a new Author round; leave the PR open for the user to close; the Reviewer's PR comment describes why)
  if Reviewer gave LGTM -> launch the Monitor; goto monitorLoop
```

### Monitor loop blocks

#### `monitorLoop`

`monitorLoop` is reached only by `goto monitorLoop`, with or without a parenthetical, from a site that has just launched the Monitor.
Every site that enters it launches first.

Which check, if any, a pass suppresses is named by the arriving goto.

```text
monitorLoop:
  Each stdout line arrives as a task-notification event.
  Terminal lines and failure markers (`FAIL`/`SKIP`) are relayed to the user, verbatim.
  Every other line is relayed or withheld at the Orchestrator's discretion; nothing obliges it to forward routine progress output.
  A terminal line is the `PR#N: ` prefix, then its **terminal word** (`Clear`, `Blocked`, `Infra`, `Draft on hold`, `Merged` or `Closed`), then any ` by: ...` attribution and any ` (mergeable_state=...)` diagnostic.
  A terminal line means only that the stream has ended.
  `Merged`, `Closed` and `Draft on hold` report facts about the PR and are acted on as such; the other three words are the Monitor's verdict, and nothing routes on them.
  The decision input is the per-check summary block, which the Monitor prints immediately before the terminal line (see namedChecks).
  if Monitor emits a `Merged` line -> send the user the `PR #{N} merged; ...` line from "Decision-signal templates" above; stop
  if Monitor emits a `Closed` line -> do NOT reopen it or start a new Author round; send the user the `PR #{N} was closed without merging; ...` line from "Decision-signal templates" above; stop
  if Monitor times out (30 min) -> escalate to user; stop
  if a user message wakes the session before Monitor delivers any terminal line -> goto silentVanish
  if Monitor emits any other terminal line -> goto namedChecks
```

#### `namedChecks`

Each row of the summary block is `<name> .... <conclusion>` followed by optional annotations, one row per check-run name, already collapsed to that name's latest run (`latest_check_runs`, `scripts/ci_monitor/ci_monitor.py:412`; issues #707 and #719).
That is how GitHub itself judges a required check.
The Monitor ends only once every check-run it can see has completed, apart from those its config ignores (`No blocking labels`, here), so a named check's row always carries a conclusion.

Read only the rows of the named checks (see "The Orchestrator's goal is consensus and green named checks, not a mergeable branch" above).
Ignore every other row, including `No blocking labels`, and every annotation the Monitor adds (`[BLOCKING]`, `[ignored]`, the terminal's ` by: ...` portion).
Which checks matter is policy stated in this document, not something the Monitor computes.

Draftness changes what the Orchestrator says, not where it goes: a failed test is a failed test whether or not the PR can merge.
Verification runs on a draft PR, and that is intended: `verified` is a claim about before-merging requirements, not about mergeability.

The Orchestrator acts on no `mergeable_state` it sees in a terminal suffix.
A merge conflict present when the head commit is pushed stops the `pull_request` workflows from running, which surfaces in `namedChecks` as named checks with no row.
A conflict that arises after the named checks ran is not caught: resolving it is part of merging, which is not the Orchestrator's goal.

```text
namedChecks:
  if the terminal word is `Draft on hold` -> send the user the `PR #{N} is a draft, ...` line from "Decision-signal templates" above, then continue below
  if any named check has no row -> goto missingNamedCheck
  Send the user an own-voice status message naming each named check that is not green with its conclusion, or saying that every named check is green.
  if any named check concluded other than green or `failure` -> escalate to user; stop
    // `cancelled`, `timed_out`, `stale`, `startup_failure` and `action_required` mean the
    // run did not deliver a verdict, which no Author round can repair.
  if any named check concluded `failure`:
    Apply this transition to the PR, as the Reviewer and Verification Agent routes do before an Author round:

    | Add label |
    |---|
    | `changes requested` |

    goto "Assigning a Programmer" above
  otherwise (every named check is green) -> goto surfaceBeforeMergingRequirements
```

#### `surfaceBeforeMergingRequirements`

`surfaceBeforeMergingRequirements` surfaces outstanding before-merging requirements: unautomated verification steps, and changes outside the repo.
It is entered once every named check is green, which does not prove the PR is mergeable.
`No blocking labels` is still red, and a draft cannot merge at all until someone marks it ready.
Neither bears on the requirements `surfaceBeforeMergingRequirements` surfaces, which are about what must be true before a merge, not about whether one is possible today.
The Orchestrator does not scan the issue or PR itself.

```text
surfaceBeforeMergingRequirements:
  Dispatch a Verification Planner sub-agent using the dispatch template.
  The planner assembles the before-merging list and files a tracking issue per item (see verification_planning.md). It does not consult the user.
  If the Planner reports its before-merging list is empty: this step is complete; apply this transition to **both the issue and the PR**:

    | Add label |
    |---|
    | `verified` |

  Otherwise, relay the before-merging list to the user verbatim, and apply this transition to the PR:

  | Add label |
  |---|
  | `verification needed` |

  Dispatch a Verification Agent (see pr_verify.md) to carry out those items; it does not consult the user.
  Route on its terminal signal per "Routing on the Verification Agent's signal" above.
```

#### `missingNamedCheck`

A named check with no row never registered a check-run the Monitor could see.
One of three things happened: the workflow had not started when every other check finished, the run died before creating the job, or the workflows never ran (no check-runs at all, as behind a merge conflict).
A check that registered but never concludes keeps the Monitor polling, so the 30-minute timeout covers that case instead.
`missingNamedCheck` gives a missing row one out-of-process recheck before treating it as real, so a dead run escalates rather than becoming an indefinite wait.

```text
missingNamedCheck:
  Send the user the `Rechecking PR #{N} ...` line from "Decision-signal templates" above.
  Wait 5 minutes without a sleep loop: issue a Bash tool call running `sleep 300` (run_in_background: true), and treat its completion notification as the wake-up.
  Launch the Monitor.
  goto monitorLoop (on this pass, a named check with no row escalates to the user and stops instead of re-entering missingNamedCheck, so the recheck gets at most one detour)
```

#### `silentVanish`

The Monitor task can silently vanish (issue #411): the process exits without the task-notification infrastructure delivering any terminal line, not even a timeout notification.
That leaves the session stuck until the user sends a message.
A re-launch after a confirmed vanish goes through `monitorLoop` with all its normal checks, including `missingNamedCheck` if warranted.

To make the double-vanish escalation reachable, the routing loop sends every user-message wake-up to `silentVanish` via `goto silentVanish`.
The second vanish therefore re-enters the block from the top rather than reaching a nested instruction after the re-launch.
That is why the block tracks whether the current Monitor invocation is itself a re-launch: the flag tells a first vanish (re-launch) from a second (escalate), and a still-alive task never escalates.

The user message that sends the Orchestrator to `silentVanish` is treated as a wake-up event only.
Its content, if any, is set aside: the Orchestrator's narrow scope during CI monitoring means user questions or instructions cannot be addressed mid-monitor.
The user is informed of CI status instead (see the branches of `silentVanish`), which is the appropriate response in this context.

```text
silentVanish:
  if TaskOutput for the Monitor's task ID returns "No task found with ID: <id>":
    // Confirmed vanish: the task record was dropped.
    if the current Monitor invocation is itself a silentVanish re-launch (the re-launch flag is set):
      // A re-launched Monitor vanished too; one recovery attempt has already been spent.
      -> escalate to user; stop
    Inform the user that the Monitor task vanished without a terminal notification and is being re-launched.
    Launch the Monitor. Set the silentVanish re-launch flag for the invocation it creates, overriding the clear a launch otherwise performs.
    goto monitorLoop
  else (the Monitor task is still registered--a user message is not proof of a vanish):
    Inform the user that CI is still running and the Monitor is alive; then continue waiting for the Monitor's terminal line; do not re-launch.
```

### Monitor script

The poll loop lives in [`scripts/ci_monitor/ci_monitor.py`](../scripts/ci_monitor/ci_monitor.py), passing the PR number via `--pr`, as the `command` for the `Monitor` tool call. For the command syntax, per-test outcome filters, and the full outcome vocabulary, see [`scripts/ci_monitor/README.md`](../scripts/ci_monitor/README.md).

Orchestrator-specific notes:

- The 30-minute escalation threshold is enforced by `timeout_ms: 1800000` on the Monitor call--no elapsed-time tracking needed.
- The per-check summary rows of the named checks are the decision input; the terminal line only marks the end of the stream. `step`/`FAIL`/`SKIP`/`PASS` lines are progress reports.
- The Verification Agent still reads the terminal words (`agents/pr_verify.md`, "Monitor workflow runs"). That is deliberate: the Monitor keeps emitting them, and only the Orchestrator's routing stopped treating them as a verdict.
- The Monitor loop replaces the patterns of subscribing to PR events and sleep+poll, which are often unreliable. Do not delay dispatching the Reviewer while waiting for CI.

## Delegation rules

- If requested by the user, **dispatch in parallel** for independent issues. Parallel issues must each have their own branch.
- **Always use worktrees.** Dispatch every sub-agent in its own git worktree (Agent tool `isolation: "worktree"`), not only Programmers and not only when dispatching in parallel.
- **One branch per ticket.** Each issue gets its own dedicated branch.
- **Separate subagents per ticket.** Each issue or PR gets its own independent Author and Reviewer agents.
- **Report subagent timing.** Use the Bash tool to run `date -u` immediately before dispatching each subagent, and again immediately after it returns. Report both times to the user.
- For follow-up work such as **subsequent rounds** of edits or reviews, or if an agent exits without completing its task, **prefer resuming the existing Author or Reviewer over spawning a replacement**.
  - Use `SendMessage` with the original agent's ID to resume it with its full prior context intact, no reconstruction needed.
  - If the ID is no longer available or resumption fails, fall back to spawning a replacement and reconstructing context from available sources (PR, issue, prior comments).
- **Do not pre-diagnose.** Do not include your own analysis of the root cause, or even your own interpretation of the problem. See "Orchestrator communication discipline" above.
- If the Author is still active, **disregard system hooks or events that signal uncommitted work**. This is normal work; continue waiting. Do not reference, quote, or explain away the hook's message in your reply, even briefly and even when replying about something else in the same turn.
- **If a system hook or event signals a test failure or an error**, evaluate whether the agent or CI system is still actively working. If the agent or CI gates are in progress, **do not intervene**. Continue waiting. Do not reference, quote, or explain away the hook's message in your reply, even briefly and even when replying about something else in the same turn.
- **A `"file was modified, either by the user or a linter"` reminder while a sub-agent is active means the sub-agent is editing the shared working tree.** Disregard it, do not interrupt the agent, and continue waiting. (Only treat it as external if you have no active sub-agent.) Do not reference, quote, or explain away the reminder in your reply, even briefly and even when replying about something else in the same turn.
- **Agent completion and exit are the same event.** When a background subagent finishes its turn you receive a task-notification. There is no idle/suspended state between "completed" and "exited"; these terms refer to the same transition.

## No sleep loops needed

Orchestrators do not need `sleep`-based keep-alive loops while waiting on sub-agents or CI.
Task-notification events (sub-agent completion) and Monitor events (CI status) keep the session alive on their own.
Dispatch and wait; do not add artificial delays.

If the session stalls (no Monitor or sub-agent event arrives) and a user message later wakes it, apply the `silentVanish` recovery path (see the Monitor loop above) if the Monitor was pending.
If the stall cannot be explained by a known recoverable cause (e.g., a Monitor task vanish), file a bug describing the gap.

## When to abort

Stop the automated cycle and escalate to the User in either of these cases:

- A Reviewer emits `Cannot work` (see the routing fences above): escalate immediately, without waiting for further rounds.
  This covers a Programmer that gives up or an issue that cannot be solved as stated; the Reviewer confirms it by emitting `Cannot work`.
- The Programmer / Reviewer loop runs **four rounds** without reaching consensus (unless the user gave a different threshold).
  This is the fallback for a loop that stalls in disagreement; once four rounds are reached the cap applies unconditionally, even when both parties are still actively disputing.

## Concluding PR orchestration

When you conclude orchestration of a PR (the cycle is complete or you are escalating and stepping out of the Orchestrator role for this PR), apply this transition to **both the issue and the PR**:

| Remove label    |
| --------------- |
| `orchestrating` |
