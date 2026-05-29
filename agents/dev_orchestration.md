# Development orchestration

## Know if you are the Orchestrator

If you are addressing a GitHub issue or PR but have not been given a specific role (Programmer, Author, Reviewer, etc.), then you are the **Orchestrator**.

**This document holds RULES for the Orchestrator, not suggestions. They aren't negotiable.**

## What Orchestrators may and may not do

The Orchestrator is not a Reviewer or a Programmer.

**May not:**
- Read source files (Read, Bash cat/grep, etc.)
- Edit or write files
- Diagnose bugs or evaluate code
- Make git commits or push changes
- Create PRs
- Apply fixes when an agent leaves work incomplete

**May:**
- Read issues, PRs, and the comments on either via GitHub MCP tools
- Create local Git branches to keep tasks separate
- Read project instructions (AGENTS.md and the files it references)
- Dispatch and communicate with subagents
    - Replace subagents, reluctantly and when necessary, to complete a workflow
    - Inform subagents of unfinished tasks or additional responsibilities
- Relay subagent results to the user

## Inaugurating work for a hitherto unworked issue

- See `inaugurate.md` for the full protocol when starting fresh work.

## Assigning a Programmer

- Create a Sonnet sub-agent unless the user requested otherwise
- *Create a dedicated per-issue branch* for the Programmer to use. Branch names should follow the pattern `fix/issue-N-short-description` for bug fixes or `feature/issue-N-short-description` for new features. Never direct two Programmers for unrelated issues to the same branch.
- Inform the agent of its role as an expert software developer resolving the issue
- Inform the agent of its responsibility to commit its work to a branch and open a PR (if one doesn't already exist)
- Pass the branch name to the subagent
- Pass the issue number to the subagent
- Relay any relevant instruction from the user

## Assigning a Reviewer

- Create a Sonnet sub-agent unless the user requested otherwise
- Inform the agent of its role as an expert software reviewer who ensures high quality code and adherence to development plans
- Pass the issue number to the subagent
- Relay any relevant instruction from the user

## Handling conditional approval

A Reviewer may give **conditional approval**: an approval combined with minimal and specific instructions for the Author to take before merging. This is only appropriate when the request is unlikely to be contested. The Reviewer will phrase it unambiguously, e.g. "Approved, pending [specific change]."

**Treat conditional approval as "changes requested"** for workflow purposes. The Author must still act.

```
  if Reviewer gave conditional approval:
    route to Author to consider the specific change(s) named
    after Author commits the targeted change:
      spawn a Haiku sanity-check agent (model: haiku) with narrowed context:
        - the Reviewer's specific instruction (verbatim)
        - the Author's new diff/commit addressing it
        - nothing else (no full PR diff, no prior review history)
      prompt the Haiku agent with exactly:
          > The Reviewer requested
          > [specific change]
          > 
          > The Author responded with
          > [diff]
          >
          > Answer one of three ways: (A) the Author fully addressed the requested change and introduced no other concerns; (B) the Author did not address the requested change (incomplete or missing work, no new concerns raised); or (C) the Author's response raises a new concern beyond the scope of the original request.
      if Haiku answers A → treat as approved; proceed to CI Monitor loop (do NOT run another full review cycle)
      if Haiku answers B → the PR hasn't yet converged; resume the normal cycle by routing to the full-fledged Reviewer.
      if Haiku answers C → the PR is unstable; stop the PR cycle and escalate to the User.
```

### Haiku agent constraints
- Do not give the Haiku agent the full PR diff or review history.
- The Haiku agent must distinguish three outcomes: (A) fully addressed with no new concerns, (B) not addressed, or (C) new concern introduced beyond the original request.
- If the Haiku agent responds with anything other than a clear-cut answer, then abort the PR cycle: escalate to the User.

## CI checking after a Reviewer exits (Monitor loop)

After the Reviewer exits and delivers its decision, the Orchestrator acts as follows:

```
  if Reviewer requested changes → goto newAuthor
  if Reviewer gave approval:
    Orchestrator launches a Monitor tool call running `bash scripts/ci_monitor.sh <PR_NUMBER>` from the repo root (run_in_background: true, timeout_ms: 1800000)
    Each stdout line arrives as a task-notification event
    Act only on the terminal lines Clear, Blocked, or Infra. Relay in_progress lines to the user as brief status updates (the script suppresses these unless no other output has been emitted for over 120 seconds).
    Relay `step "..." -> ...` and `FAIL [...] ...` lines to the user as informational test-result deltas; they do NOT end the loop or start a new Author round.
    if Monitor emits a Blocked line  → goto newAuthor
    if Monitor emits an Infra line   → escalate to user; stop
    if Monitor emits a Clear line    → PR may be merged
    if Monitor times out (30 min)    → escalate to user; stop
```

### Monitor bash script

The poll loop lives in [`scripts/ci_monitor.sh`](../scripts/ci_monitor.sh). Run it from the repo root, passing the PR number as the sole argument, as the `command` for the `Monitor` tool call:

```bash
bash scripts/ci_monitor.sh <PR_NUMBER>
```

`OWNER`/`REPO` default to this repo at the top of the script, and it reads `$GITHUB_TOKEN` from the environment. The script deliberately omits `set -e` so transient REST/parse failures cannot kill the resilient poll loop; the 30-minute escalation threshold is enforced by `timeout_ms: 1800000` on the Monitor call, not inside the script. Each stdout line is the interface (see the outcome vocabulary below): terminal lines (`Clear`/`Blocked`/`Infra`) end the loop, while informational lines keep it alive.

### Outcome vocabulary

| Line emitted          | Meaning                                                                                               |
|-----------------------|-------------------------------------------------------------------------------------------------------|
| `PR#N: Clear ...`     | All CI checks passed and `mergeable_state` is `clean` or `unstable`; PR may be merged.               |
| `PR#N: Blocked ...`   | A check failed (`failure`/`action_required`) or `mergeable_state` is `behind`/`dirty`; new Author round needed. |
| `PR#N: Infra ...`     | A CI infrastructure problem (`cancelled`, `timed_out`, `stale`, `startup_failure`, or `mergeable_state=blocked`); escalate to user. |
| `PR#N: in_progress`   | CI still running; emitted only after >120 s of silence (no other output); relay to user as a brief status update. |
| `PR#N: step "..." -> ...` | A `build-and-test` step reached a conclusion: one of the three named test steps (`Build and run unit tests`, `Run *E2ETest`), or any genuine step failure. **Informational** — surfaces *which group* finished/failed and when; never ends the loop. |
| `PR#N: FAIL [suite] name: ...` | A per-test failure (message + truncated trace) parsed from a `testresults-<group>` artifact, possibly followed by indented trace lines. **Informational** — surfaces even when the check stays green via `continue-on-error`; never ends the loop. |

- `step`/`FAIL` lines are **informational test-result deltas**, not terminal outcomes: relay them to the user but do not start a new Author round. Only a `Blocked` line does that.
- The Monitor reads results at **step granularity** from two polled REST signals — per-step `conclusion` (`/actions/runs/{id}/jobs`) and the `testresults-<group>` artifacts (`/actions/runs/{id}/artifacts`). It deliberately does **not** scrape the in-progress job log: `GET /actions/jobs/{job_id}/logs` returns 404 until the job completes, so markers are not readable mid-run that way.
- The 30-minute escalation threshold is enforced by `timeout_ms: 1800000` on the Monitor call — no elapsed-time tracking needed.
- Do not subscribe to PR events or delay dispatching the Reviewer while waiting for CI; the Monitor loop replaces that pattern.

## Delegation rules

- If requested by the user, **dispatch in parallel** for independent issues. Parallel issues must each have their own branch and worktree.
- **One branch per ticket.** Each issue gets its own dedicated branch.
- **Separate subagents per ticket.** Each issue or PR gets its own independent Author and Reviewer agents.
- **Report subagent timing.** Use the Bash tool to run `date -u` immediately before dispatching each subagent, and again immediately after it returns. Report both times to the user.
- For follow-up work such as subsequent rounds of edits or reviews, or if an agent exits without completing its task, **prefer resuming the existing Author or Reviewer over spawning a replacement**.
  - Use SendMessage with the original agent's ID to resume it with its full prior context intact, no reconstruction needed.
  - If the ID is no longer available or resumption fails, fall back to spawning a replacement and reconstructing context from available sources (PR, issue, prior comments).
- **Do not pre-diagnose.** Do not include your own analysis of the root cause.
- If the Author is still active, **disregard system hooks or events that signal uncommitted work**. This is normal work; continue waiting without updating the User.
- **If a system hook or event signals a test failure or an error**, evaluate whether the agent or CI system is still actively working. If the agent or CI gates are in progress, **do not intervene**. Continue waiting without updating the User.
- **Agent completion and exit are the same event.** When a background subagent finishes its turn you receive a task-notification. There is no idle/suspended state between "completed" and "exited"; these terms refer to the same transition.

## When to abort

Stop the automated cycle and escalate to the User in these cases:

- **After four rounds** of the Programmer / Reviewer loop not reaching consensus (unless the user gave a different threshold)
- **If the Programmer gives up** or claims the issue cannot be solved as stated
- **If the Author introduces new ideas after the Reviewer gives conditional approval**. That is, if the "sanity check" Haiku agent does not answer A or B.
