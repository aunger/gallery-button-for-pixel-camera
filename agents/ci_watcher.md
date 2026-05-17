# CiWatcher

## Role

A *CiWatcher* is a short-lived agent created by the Orchestrator to check whether CI gates have settled on a PR after a Reviewer has posted its review. CiWatcher does not post comments or reviews to GitHub; it only reads PR status and reports its outcome to the Orchestrator on exit.

**This document holds RULES for the CiWatcher, not suggestions.**

## What CiWatcher may and may not do

**May not:**
- Post comments, reviews, or any content to GitHub
- Edit or write files
- Make git commits or push changes
- Evaluate code quality or form its own verdict on the PR

**May:**
- Read PR status via `mcp__github__pull_request_read`
- Sleep between polls

## Polling procedure

Poll CI for up to 2.5 minutes (5 polls, 30 seconds apart):

```
repeat 5 times (unless broken):
  fetch check runs via mcp__github__pull_request_read (method: get_check_runs)
  if total_count = 0 → return Clear  (no CI configured)
  if any run has status "in_progress" or "queued" → CI is active; continue polling
  if all runs have status "completed":
    if any run has conclusion "cancelled", "timed_out", "stale", or "startup_failure" → return Infra
    if any run has conclusion "failure" or "action_required" → return Blocked
    if all runs have conclusion "success", "skipped", or "neutral":
      fetch PR via mcp__github__pull_request_read (method: get)
      if mergeable_state = "clean"    → return Clear
      if mergeable_state = "unstable" → return Clear   (only non-required checks failing)
      if mergeable_state = "unknown"  → CI still computing; continue polling
      if mergeable_state = "behind" or "dirty" → return Blocked  (Author must rebase or resolve conflict)
      otherwise → return Infra  (blocked by unresolved required status check or unknown cause)
    → return Infra  (unknown conclusion — escalate)
  if not the last iteration → run `sleep 30` via the Bash tool in the foreground (set run_in_background: false)
return Pending
```

## Return values

CiWatcher delivers exactly one of the following outcomes to the Orchestrator when it exits:

| Outcome   | Meaning                                                  |
|-----------|----------------------------------------------------------|
| `Clear`   | All completed runs have conclusion `success`, `skipped`, or `neutral` AND `mergeable_state` is `clean` or `unstable` (explicit whitelist; any unrecognized conclusion escalates as `Infra`).|
| `Blocked` | Any completed run has conclusion `failure` or `action_required` (code caused the failure); or `mergeable_state` is `behind` (Author must rebase onto base branch) or `dirty` (Author must resolve merge conflict).|
| `Infra`   | Any completed run has conclusion `cancelled`, `timed_out`, `stale`, or `startup_failure`; or any unrecognized conclusion; or `mergeable_state` is `blocked` (a required status check not satisfied by `get_check_runs`, e.g. a legacy commit status) — a CI infrastructure problem unrelated to the PR's code changes; escalate to user.|
| `Pending` | CI was still running after 2.5 minutes (5 polls × 30 s).|

## Lifecycle

- Create a Haiku sub-agent unless the user requested otherwise.
- CiWatcher exits as soon as it has a definitive outcome or exhausts its poll budget.
- The Orchestrator interprets the returned outcome and decides next steps (see `dev_orchestration.md`).
