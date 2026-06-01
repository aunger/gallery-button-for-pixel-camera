# Phase 4 resource: Watch CI

Read this after the Reviewer approves (or conditional approval has been resolved to an approval).

The binding rules are in the "CI checking after a Reviewer exits (Monitor loop)" section of `agents/dev_orchestration.md`, including the full outcome vocabulary and per-test filter flags.
This resource adds only the mechanics specific to the skill.

## The poller

The canonical poll loop lives at `scripts/ci_monitor.py` (repo root, not inside the skill, so that both the slash-command flow and the standalone `agents/` flow share one tested implementation).
Run it from the repo root via a Monitor tool call:

```bash
python3 scripts/ci_monitor.py --pr <PR_NUMBER>
```

Launch it with `run_in_background: true` and `timeout_ms: 1800000` (the 30-minute escalation threshold is enforced by the timeout, not inside the script).

## Reading the stream

Each stdout line is a task-notification event. Act only on terminal lines:

- `Clear`: CI passed; proceed to the post-clear verification step in `agents/dev_orchestration.md`.
- `Blocked`: route back to the Author (Phase 2, prefer resume).
- `Infra`: escalate to the user and stop.

Relay `in_progress`, `step`, `FAIL`, `SKIP`, and `PASS` lines to the user as informational deltas. They never end the loop or start a new Author round.

## Targeted validation

To confirm a task-relevant test actually ran and passed (not silently skipped), pass `--include-pass 'YourTestName'`. See the per-test filter table in `agents/dev_orchestration.md`.
