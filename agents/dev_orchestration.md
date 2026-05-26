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
    Act only on lines containing Clear, Blocked, or Infra. Relay in_progress lines to the user as brief status updates (the script suppresses these unless no other output has been emitted for over 120 seconds).
    if Monitor emits a Blocked line  → goto newAuthor
    if Monitor emits an Infra line   → escalate to user; stop
    if Monitor emits a Clear line    → PR may be merged
    if Monitor times out (30 min)    → escalate to user; stop
```

### Monitor bash script

Use the following script verbatim as the `command` for the `Monitor` tool call. Replace `<PR_NUMBER>` with the actual PR number at runtime.

#### Spike result: partial-log availability

`GET /repos/{owner}/{repo}/actions/jobs/{job_id}/logs` returns a 302 redirect to a plain-text log
file. The GitHub REST API documentation does not explicitly state whether this file is available or
complete while the job is still in progress. In practice, GitHub Actions writes job log lines to
blob storage incrementally (near-real-time), and the redirect URL resolves to whatever bytes have
been flushed so far — so the endpoint **does serve partial logs for an in-progress job**. However,
this behavior is undocumented and may not be reliable under all conditions (e.g. high runner load,
log-storage lag).

**Primary path:** fetch the live job log on every poll iteration, grep for `##GB4PC_TEST##` FAIL
markers, and emit new failures as deltas. This works as soon as a test finishes, regardless of
whether the overall job has completed.

**Fallback path (if the live log is empty or unavailable):** poll the already-uploaded JUnit XML
artifacts. The `build-and-test` workflow uploads `unit-test-results` immediately after the unit-test
step — before the emulator starts — so unit failures are available as artifacts well before E2E
runs. The `e2e-test-results` artifact uploads after E2E completes. Parse these artifacts with the
same approach used by `scripts/file_test_failure_issues.py` (`TEST-*.xml` → `<failure>` /
`<error>` elements). Granularity is per-test but the signal arrives step-by-step rather than
continuously. The fallback section below is commented out in the script; uncomment it if the
primary path proves unreliable in practice.

```bash
OWNER="aunger"
REPO="gallery-button-for-pixel-camera"
PR=<PR_NUMBER>
HEADERS=(-H "Authorization: Bearer $GITHUB_TOKEN" -H "Accept: application/vnd.github+json")
last_output_ts=$(date +%s)
# Temp file tracks suite#name keys of FAILs already emitted; persists across loop iterations.
seen_fails_file=$(mktemp)
trap 'rm -f "$seen_fails_file"' EXIT

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

  # ── Test-failure delta (primary path: live job log) ───────────────────────
  # Resolve the workflow run for this SHA, then the build-and-test job id.
  run_id=$(curl -s "${HEADERS[@]}" \
    "https://api.github.com/repos/$OWNER/$REPO/actions/runs?head_sha=$sha&event=pull_request&per_page=5" | \
    python3 -c "
import sys,json
runs=json.load(sys.stdin).get('workflow_runs',[])
# Pick the most-recent non-cancelled run.
for r in runs:
    if r.get('status') != 'cancelled':
        print(r['id']); break
" 2>/dev/null)

  if [ -n "$run_id" ]; then
    job_id=$(curl -s "${HEADERS[@]}" \
      "https://api.github.com/repos/$OWNER/$REPO/actions/runs/$run_id/jobs?per_page=20" | \
      python3 -c "
import sys,json
jobs=json.load(sys.stdin).get('jobs',[])
for j in jobs:
    if j.get('name') == 'build-and-test':
        print(j['id']); break
" 2>/dev/null)

    if [ -n "$job_id" ]; then
      # Fetch the job log (302 → plain text; -L follows the redirect).
      # For an in-progress job this returns partial content; for a completed job
      # it returns the full log. If the response is empty, the fallback applies.
      job_log=$(curl -sL "${HEADERS[@]}" \
        "https://api.github.com/repos/$OWNER/$REPO/actions/jobs/$job_id/logs" 2>/dev/null)

      if [ -n "$job_log" ]; then
        # Parse ##GB4PC_TEST## markers, emit new FAILs only (deduped by suite#name).
        echo "$job_log" | grep '##GB4PC_TEST##' | \
        SEEN_FAILS_FILE="$seen_fails_file" python3 -c "
import sys, json, os

seen_file = os.environ.get('SEEN_FAILS_FILE', '')
seen = set()
if seen_file:
    try:
        seen = set(open(seen_file).read().splitlines())
    except OSError:
        pass

new_seen = []
for raw in sys.stdin:
    raw = raw.strip()
    idx = raw.find('##GB4PC_TEST##')
    if idx == -1:
        continue
    payload = raw[idx + len('##GB4PC_TEST##'):].strip()
    try:
        m = json.loads(payload)
    except json.JSONDecodeError:
        continue
    if m.get('outcome') != 'FAIL':
        continue
    key = m.get('suite','') + '#' + m.get('name','')
    if key in seen:
        continue
    new_seen.append(key)
    seen.add(key)
    msg   = m.get('msg','').strip()
    trace = m.get('trace','').strip()
    # Truncate trace to 800 chars (markers already cap it, but guard here too).
    if len(trace) > 800:
        trace = trace[:800] + ' ... (truncated)'
    suite = m.get('suite','?')
    name  = m.get('name','?')
    ms    = m.get('ms','?')
    line  = f'FAIL [{suite}] {name} ({ms}ms): {msg}'
    if trace:
        line += '\n  ' + trace.replace('\n', '\n  ')
    print(line, flush=True)

if seen_file and new_seen:
    with open(seen_file, 'a') as f:
        f.write('\n'.join(new_seen) + '\n')
" | \
        while IFS= read -r fail_line; do
          echo "PR#${PR}: $fail_line"
          last_output_ts=$(date +%s)
        done
      fi
      # ── Fallback path (uncomment if live logs prove unreliable) ──────────────
      # If job_log is empty even when the job is in progress, fall back to
      # polling the unit-test-results artifact (uploaded after the unit-test step,
      # before E2E starts). Artifact parsing mirrors scripts/file_test_failure_issues.py.
      #
      # artifact_id=$(curl -s "${HEADERS[@]}" \
      #   "https://api.github.com/repos/$OWNER/$REPO/actions/runs/$run_id/artifacts?name=unit-test-results" | \
      #   python3 -c "
      # import sys,json
      # arts=json.load(sys.stdin).get('artifacts',[])
      # if arts: print(arts[0]['id'])
      # " 2>/dev/null)
      # if [ -n "$artifact_id" ]; then
      #   # Download zip, extract, grep TEST-*.xml for <failure> / <error> elements.
      #   # (Implement parsing analogous to parse_failures() in file_test_failure_issues.py.)
      # fi
    fi
  fi
  # ── End test-failure delta ────────────────────────────────────────────────

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

| Line emitted                           | Meaning                                                                                               |
|----------------------------------------|-------------------------------------------------------------------------------------------------------|
| `PR#N: Clear ...`                      | All CI checks passed and `mergeable_state` is `clean` or `unstable`; PR may be merged.               |
| `PR#N: Blocked ...`                    | A check failed (`failure`/`action_required`) or `mergeable_state` is `behind`/`dirty`; new Author round needed. |
| `PR#N: Infra ...`                      | A CI infrastructure problem (`cancelled`, `timed_out`, `stale`, `startup_failure`, or `mergeable_state=blocked`); escalate to user. |
| `PR#N: in_progress`                    | CI still running; emitted only after >120 s of silence (no other output); relay to user as a brief status update. |
| `PR#N: FAIL [suite] name (Nms): msg`  | A test marked `FAIL` in the CI job log (`##GB4PC_TEST##` marker). Emitted as a delta — only the first occurrence per `suite#name` key across all iterations. Includes the failure message and (on the next indented line) a truncated stack trace. These lines are emitted independent of the overall check conclusion (E2E steps are `continue-on-error` and can fail while the check stays green). Each `FAIL` line resets the silence timer. |

- The 30-minute escalation threshold is enforced by `timeout_ms: 1800000` on the Monitor call — no elapsed-time tracking needed.
- Do not subscribe to PR events or delay dispatching the Reviewer while waiting for CI; the Monitor loop replaces that pattern.
- `FAIL` lines do not by themselves cause the Orchestrator to route to a new Author — they are informational. Only a terminal `Blocked` line triggers a new Author round. The Orchestrator should relay `FAIL` lines to the user as additional detail alongside status updates.

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
