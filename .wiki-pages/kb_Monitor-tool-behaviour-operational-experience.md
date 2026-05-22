The `Monitor` tool runs a bash command as a persistent background process. Each line it writes to stdout is delivered to the Orchestrator as a `<task-notification>` event. Key properties:

## How it works
- Launched with `run_in_background: true`; returns a task ID immediately.
- Every stdout line fires a notification. The Orchestrator receives these between its own turns (they arrive mid-conversation, not only when Claude is idle).
- A `timeout_ms` parameter kills the process if it runs too long. Set this to match the 30-minute escalation threshold: `1_800_000`.
- The process runs until it exits naturally (a `break` in the loop) or times out. There is no explicit "stop monitor" call needed — just ensure the loop exits on terminal states.

## What worked well
- **30-second poll interval** was a good balance: low noise, fast enough to catch CI results promptly.
- **Fetching `mergeable_state`** after all checks pass was necessary — GitHub needs a few seconds after checks complete before it computes mergeability. The loop handles this by continuing to poll when `mergeable_state` is not yet settled (`unknown` or `has_hooks`).
- **Emitting `in_progress` as a heartbeat** kept the Orchestrator aware the monitor was alive without requiring action.
- **Breaking on terminal states** (`Clear`, `Blocked`, `Infra`) let the Monitor exit cleanly and trigger a final completion notification.

## Operational notes
- On a PR with a slow CI pipeline (~10–15 min), the Monitor fired ~20–30 `in_progress` events before a terminal result. These are silent no-ops for the Orchestrator.
- The Monitor loop correctly distinguished infra failures (`cancelled`, `timed_out`, `stale`, `startup_failure` conclusions) from test failures (`failure`, `action_required`), which maps to the `Infra` vs `Blocked` vocabulary the Orchestrator already uses.
- `mergeable_state = "blocked"` can mean the PR needs a required review approval, not just a failing check — this was classified as `Infra` (escalate to user) rather than `Blocked` (new Author round), which was the right call.

---

**Source:** [Issue #203 - Replace CiWatcher subagents with a background Monitor loop](https://github.com/aunger/gallery-button-for-pixel-camera/issues/203#issuecomment-4508573714)
