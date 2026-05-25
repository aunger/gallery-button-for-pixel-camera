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

**Haiku agent constraints:**
- Do not give the Haiku agent the full PR diff or review history.
- The Haiku agent must distinguish three outcomes: (A) fully addressed with no new concerns, (B) not addressed, or (C) new concern introduced beyond the original request.
- If the Haiku agent responds with anything other than a clear-cut A, then return to the normal PR cycle: next invoke the full Reviewer.

## CI checking after a Reviewer exits (Monitor loop)

After the Reviewer exits and delivers its decision, the Orchestrator acts as follows:

```
  if Reviewer requested changes → goto newAuthor
  if Reviewer gave approval:
    Orchestrator launches a Monitor tool call (run_in_background: true, timeout_ms: 1800000)
    Each stdout line arrives as a task-notification event
    Act only on lines containing Clear, Blocked, or Infra; ignore in_progress heartbeats
    if Monitor emits a Blocked line  → goto newAuthor
    if Monitor emits an Infra line   → escalate to user; stop
    if Monitor emits a Clear line    → PR may be merged
    if Monitor times out (30 min)    → escalate to user; stop
```

### Monitor bash script

Use the following script verbatim as the `command` for the `Monitor` tool call. Replace `<PR_NUMBER>` with the actual PR number at runtime.

```bash
OWNER="aunger"
REPO="gallery-button-for-pixel-camera"
PR=<PR_NUMBER>
HEADERS=(-H "Authorization: Bearer $GITHUB_TOKEN" -H "Accept: application/vnd.github+json")

while true; do
  sha=$(curl -s "${HEADERS[@]}" "https://api.github.com/repos/$OWNER/$REPO/pulls/$PR" | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('head',{}).get('sha',''))" 2>/dev/null)

  [ -z "$sha" ] && { echo "PR#${PR}: could not fetch SHA"; sleep 30; continue; }

  check_data=$(curl -s "${HEADERS[@]}" \
    "https://api.github.com/repos/$OWNER/$REPO/commits/$sha/check-runs" 2>/dev/null)

  result=$(echo "$check_data" | python3 -c "
import sys,json
d=json.load(sys.stdin)
runs=d.get('check_runs',[])
total=d.get('total_count',0)
if total==0:
    print('Clear'); exit()
statuses=[r['status'] for r in runs]
conclusions=[r.get('conclusion','') for r in runs if r['status']=='completed']
if any(s in ('in_progress','queued') for s in statuses):
    print('in_progress')
elif all(s=='completed' for s in statuses):
    if any(c in ('cancelled','timed_out','stale','startup_failure') for c in conclusions): print('Infra')
    elif any(c in ('failure','action_required') for c in conclusions): print('Blocked')
    else: print('all_passed')
else:
    print('in_progress')
" 2>/dev/null)

  if [ "$result" = "in_progress" ]; then
    echo "PR#${PR}: in_progress"
  elif [ "$result" = "all_passed" ]; then
    mergeable=$(curl -s "${HEADERS[@]}" "https://api.github.com/repos/$OWNER/$REPO/pulls/$PR" | \
      python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('mergeable_state','unknown'))" 2>/dev/null)
    if [ "$mergeable" = "clean" ] || [ "$mergeable" = "unstable" ]; then
      echo "PR#${PR}: Clear (mergeable_state=$mergeable)"; break
    elif [ "$mergeable" = "behind" ] || [ "$mergeable" = "dirty" ]; then
      echo "PR#${PR}: Blocked (mergeable_state=$mergeable)"; break
    elif [ "$mergeable" = "blocked" ]; then
      echo "PR#${PR}: Infra (mergeable_state=blocked)"; break
    else
      echo "PR#${PR}: all_passed mergeable_state=$mergeable (still computing)"
    fi
  elif [ "$result" = "Blocked" ] || [ "$result" = "Infra" ]; then
    echo "PR#${PR}: $result"; break
  else
    echo "PR#${PR}: $result"
  fi

  sleep 30
done
```

### Outcome vocabulary

| Line emitted          | Meaning                                                                                               |
|-----------------------|-------------------------------------------------------------------------------------------------------|
| `PR#N: Clear ...`     | All CI checks passed and `mergeable_state` is `clean` or `unstable`; PR may be merged.               |
| `PR#N: Blocked ...`   | A check failed (`failure`/`action_required`) or `mergeable_state` is `behind`/`dirty`; new Author round needed. |
| `PR#N: Infra ...`     | A CI infrastructure problem (`cancelled`, `timed_out`, `stale`, `startup_failure`, or `mergeable_state=blocked`); escalate to user. |
| `PR#N: in_progress`   | CI still running; heartbeat only — no action required.                                                |

- The 30-minute escalation threshold is enforced by `timeout_ms: 1800000` on the Monitor call — no elapsed-time tracking needed.
- Do not subscribe to PR events or delay dispatching the Reviewer while waiting for CI; the Monitor loop replaces that pattern.

## Delegation rules

- **Separate subagents per ticket.** Each issue or PR gets its own independent Author and Reviewer agents.
- **One branch per ticket.** Each issue gets its own dedicated branch.  
- If requested by the user, **dispatch in parallel** for independent issues. Parallel issues must each have their own branch and worktree.
- **Do not pre-diagnose.** Do not include your own analysis of the root cause.
- If the Author is still active, quietly **disregard system hooks or events that signal uncommitted work**; this is part of normal work.
- **If a system hook or event signals a test failure or an error**, evaluate whether the agent or CI system is still actively working. If the agent or CI gates are in progress, **do not intervene** and quietly continue waiting.
- **Agent completion and exit are the same event.** When a background subagent finishes its turn you receive a task-notification. There is no idle/suspended state between "completed" and "exited" — these terms refer to the same transition.
- **If an agent has exited without completing its task**, prefer resuming it over spawning a replacement. Use SendMessage with the original agent's ID to resume it with its full prior context intact — no reconstruction needed. Only spawn a replacement if the original agent's ID is unavailable or resumption fails.
  - *Caveat — time window:* the backend may only keep a completed agent's session alive for a limited time after exit. Attempt resumption promptly.
  - *Caveat — ID availability:* background agent IDs are returned at launch but are not persisted across Orchestrator context resets. If the ID is no longer available, fall back to spawning a replacement and reconstructing context from available sources (PR, issue, prior comments).

## When to abort

- **After four rounds** of the Programmer / Reviewer loop not reaching consensus (unless the user gave a different threshold)
- **If the Programmer gives up** or claims the issue cannot be solved as stated
- **If the Author introduces new ideas after the Reviewer gives conditional approval** or claims the issue cannot be solved as stated
