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
    Orchestrator launches a Monitor tool call (run_in_background: true, timeout_ms: 1800000)
    Each stdout line arrives as a task-notification event
    Act only on the terminal lines Clear, Blocked, or Infra. Relay in_progress lines to the user as brief status updates (the script suppresses these unless no other output has been emitted for over 120 seconds).
    Relay `step "..." -> ...` and `FAIL [...] ...` lines to the user as informational test-result deltas; they do NOT end the loop or start a new Author round.
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
last_output_ts=$(date +%s)

# Dedup state for the streamed test-result signals (removed when the loop exits).
seen_steps=$(mktemp); seen_arts=$(mktemp); seen_fails=$(mktemp)
trap 'rm -f "$seen_steps" "$seen_arts" "$seen_fails"' EXIT

# Print each line of $1 prefixed with the PR tag, and reset the 120s silence
# timer (any streamed output counts as liveness).
emit_block() {
  [ -z "$1" ] && return 0
  printf '%s\n' "$1" | sed "s/^/PR#${PR}: /"
  last_output_ts=$(date +%s)
}

while true; do
  sha=$(curl -s "${HEADERS[@]}" "https://api.github.com/repos/$OWNER/$REPO/pulls/$PR" | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('head',{}).get('sha',''))" 2>/dev/null)

  [ -z "$sha" ] && { echo "PR#${PR}: could not fetch SHA"; last_output_ts=$(date +%s); sleep 30; continue; }

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

  # --- Streamed test-result signals -----------------------------------------
  # Emitted independent of the overall check conclusion, so E2E failures surface
  # even while the check stays green via continue-on-error. Both signals are
  # purely informational: they reset the silence timer but never end the loop.
  run_id=$(curl -s "${HEADERS[@]}" \
    "https://api.github.com/repos/$OWNER/$REPO/actions/runs?head_sha=$sha&event=pull_request&per_page=5" | \
    python3 -c "
import sys,json
for r in json.load(sys.stdin).get('workflow_runs',[]):
    if r.get('status')!='cancelled': print(r['id']); break
" 2>/dev/null)

  if [ -n "$run_id" ]; then
    # Signal 1 — per-step conclusion deltas for the build-and-test job. Emit when
    # a step reaches 'completed' if it is one of the three named test steps (on any
    # conclusion) or genuinely failed; successful setup steps and skipped
    # conditional (upload-on-failure) steps are suppressed as noise. Deduped by step
    # number. Gives the live "which group finished/failed, and when" signal.
    steps_out=$(curl -s "${HEADERS[@]}" \
      "https://api.github.com/repos/$OWNER/$REPO/actions/runs/$run_id/jobs?per_page=30" | \
      SEEN="$seen_steps" python3 -c "
import sys,json,os
seen_f=os.environ['SEEN']
seen=set(open(seen_f).read().split()) if os.path.getsize(seen_f) else set()
new=[]; out=[]
for j in json.load(sys.stdin).get('jobs',[]):
    if j.get('name')!='build-and-test': continue
    for s in j.get('steps',[]):
        if s.get('status')!='completed': continue
        num=str(s.get('number'))
        if num in seen: continue
        new.append(num)
        name=s.get('name','?'); concl=s.get('conclusion') or '?'
        if name=='Build and run unit tests' or 'E2ETest' in name or concl in ('failure','cancelled','timed_out','action_required'):
            out.append('step \"%s\" -> %s' % (name, concl))
if new: open(seen_f,'a').write('\n'.join(new)+'\n')
print('\n'.join(out))
" 2>/dev/null)
    emit_block "$steps_out"

    # Signal 2 — per-test FAIL detail from the testresults-<group> artifacts the
    # workflow uploads after each test step. Download each new artifact once, parse
    # its ##GB4PC_TEST## ndjson markers, and emit new FAIL entries (message +
    # truncated trace), deduped across polls by suite#name.
    arts=$(curl -s "${HEADERS[@]}" \
      "https://api.github.com/repos/$OWNER/$REPO/actions/runs/$run_id/artifacts?per_page=100" | \
      SEEN="$seen_arts" python3 -c "
import sys,json,os
seen_f=os.environ['SEEN']
seen=set(open(seen_f).read().split()) if os.path.getsize(seen_f) else set()
for a in json.load(sys.stdin).get('artifacts',[]):
    n=a.get('name','')
    if n.startswith('testresults-') and not a.get('expired') and str(a['id']) not in seen:
        print('%s\t%s' % (a['id'], n))
" 2>/dev/null)
    while IFS=$'\t' read -r aid aname; do
      [ -z "$aid" ] && continue
      tmp=$(mktemp -d)
      if curl -sL "${HEADERS[@]}" \
           "https://api.github.com/repos/$OWNER/$REPO/actions/artifacts/$aid/zip" -o "$tmp/a.zip" \
         && unzip -qo "$tmp/a.zip" -d "$tmp" 2>/dev/null; then
        fails_out=$(find "$tmp" -name '*.ndjson' -exec cat {} + 2>/dev/null | SEEN_FAILS="$seen_fails" python3 -c "
import sys,json,os
seen_f=os.environ['SEEN_FAILS']
seen=set(open(seen_f).read().splitlines()) if os.path.getsize(seen_f) else set()
new=[]
for raw in sys.stdin:
    i=raw.find('##GB4PC_TEST##')
    if i==-1: continue
    try: m=json.loads(raw[i+len('##GB4PC_TEST##'):].strip())
    except Exception: continue
    if m.get('outcome')!='FAIL': continue
    key=m.get('suite','')+'#'+m.get('name','')
    if key in seen: continue
    seen.add(key); new.append(key)
    msg=(m.get('msg') or '').strip(); tr=(m.get('trace') or '').strip()
    if len(tr)>800: tr=tr[:800]+' ...(truncated)'
    line='FAIL [%s] %s: %s' % (m.get('suite','?'), m.get('name','?'), msg)
    if tr: line+='\n  '+tr.replace('\n','\n  ')
    print(line)
if new: open(seen_f,'a').write('\n'.join(new)+'\n')
")
        emit_block "$fails_out"
        echo "$aid" >> "$seen_arts"
      fi
      rm -rf "$tmp"
    done <<< "$arts"
  fi
  # --------------------------------------------------------------------------

  if [ "$result" = "in_progress" ]; then
    now=$(date +%s)
    if [ $((now - last_output_ts)) -gt 120 ]; then
      echo "PR#${PR}: in_progress"
      last_output_ts=$now
    fi
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
      last_output_ts=$(date +%s)
    fi
  elif [ "$result" = "Blocked" ] || [ "$result" = "Infra" ]; then
    echo "PR#${PR}: $result"; break
  else
    echo "PR#${PR}: $result"
    last_output_ts=$(date +%s)
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
