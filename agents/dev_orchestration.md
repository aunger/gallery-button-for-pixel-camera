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
- Inform the agent of its role as an expert software developer resolving the issue
- Inform the agent of its responsibility to commit its work to a branch and open a PR (if one doesn't already exist)
- *Create a dedicated per-issue branch* for the Programmer to use. Branch names should follow the pattern `fix/issue-N-short-description` for bug fixes or `feature/issue-N-short-description` for new features. Never direct two Programmers for unrelated issues to the same branch.
- Pass the branch name to the subagent
- Pass the issue number to the subagent
- Relay any relevant instruction from the user

## Assigning a Reviewer

- Create a Sonnet sub-agent unless the user requested otherwise
- Inform the agent of its role as an expert software reviewer who ensures high quality code and adherence to development plans
- Pass the issue number to the subagent
- Relay any relevant instruction from the user
- The Reviewer posts its review immediately upon completing its analysis and then exits. **Do not** instruct the Reviewer to poll CI or delay posting — CI checking is handled by the Orchestrator via a CiWatcher agent (see below).

## CI checking after a Reviewer exits (CiWatcher loop)

After the Reviewer exits and delivers its decision, the Orchestrator runs the following loop:

```
loop:
  if Reviewer gave approval:
    Orchestrator creates a CiWatcher agent (see agents/ci_watcher.md)
    CiWatcher returns one of: Clear, Blocked, or Pending
    if CiWatcher returned Pending → goto loop
  if Reviewer requested changes OR CiWatcher returned Blocked → goto newAuthor
  if CiWatcher returned Clear → PR may be merged
```

- **CiWatcher** is a short-lived agent that polls CI for up to 2.5 minutes and reports back. It does not post to GitHub.
- The Orchestrator loops (spawning a fresh CiWatcher each iteration) until CI settles or a new Author cycle is needed.
- Do not subscribe to PR events or delay dispatching the Reviewer while waiting for CI — the CiWatcher loop replaces that pattern.

## Delegation rules

- **One subagent per ticket.** Each issue or PR gets its own independent subagent.
- **One branch per ticket.** Each issue gets its own dedicated branch.  
- **Dispatch in parallel** for independent issues (unless otherwise instructed). Parallel independent issues must each have their own branch.
- **Do not pre-diagnose.** Do not include your own analysis of the root cause.
- **If a system hook or event signals uncommitted work, a test failure, or an error**, **pause 45 seconds**, then evaluate whether the agent or CI system is still actively working. If the agent was recently active (not idle or waiting for input), **or** the CI gates are in progress, **do not intervene** and continue waiting.
- **Agent completion and exit are the same event.** When a background subagent finishes its turn you receive a task-notification. There is no idle/suspended state between "completed" and "exited" — these terms refer to the same transition.
- **If an agent has exited without completing its task**, prefer resuming it over spawning a replacement. Use SendMessage with the original agent's ID to resume it with its full prior context intact — no reconstruction needed. Only spawn a replacement if the original agent's ID is unavailable or resumption fails.
  - *Caveat — time window:* the backend may only keep a completed agent's session alive for a limited time after exit. Attempt resumption promptly.
  - *Caveat — ID availability:* background agent IDs are returned at launch but are not persisted across Orchestrator context resets. If the ID is no longer available, fall back to spawning a replacement and reconstructing context from available sources (PR, issue, prior comments).

## When to abort

- **After three rounds** of the Programmer / Reviewer loop not reaching consensus (unless the user gave a different threshold)
- **If the Programmer gives up** or claims the issue cannot be solved as stated
- **If the Reviewer** agrees the PR may be merged
