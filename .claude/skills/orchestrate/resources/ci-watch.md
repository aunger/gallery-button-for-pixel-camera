# Phase 4 resource: Watch CI

Read this after the Reviewer approves (or conditional approval has been resolved to an approval).

The binding rules are in `rules/ci_monitor.md`, including the full outcome vocabulary and per-test filter flags.
This resource adds only the mechanics specific to the skill.

## The poller

The CI Watcher poll loop ships inside this plugin at `${CLAUDE_PLUGIN_ROOT}/scripts/ci_monitor.py`.
Prefer dispatching the `ci-watcher` agent (it already knows how to run this); if you run it directly, launch it via a Monitor tool call:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ci_monitor.py" --pr <PR_NUMBER>
```

Launch it with `run_in_background: true` and `timeout_ms: 1800000` (the 30-minute escalation threshold is enforced by the timeout, not inside the script).

## Reading the stream

Each stdout line is a task-notification event. Act only on terminal lines:

- `Clear`: CI passed; run the post-clear verification workflow below.
- `Blocked`: route back to the Author (Phase 2, prefer resume).
- `Infra`: escalate to the user and stop.

Relay `in_progress`, `step`, `FAIL`, `SKIP`, and `PASS` lines to the user as informational deltas. They never end the loop or start a new Author round.

## Targeted validation

To confirm a task-relevant test actually ran and passed (not silently skipped), pass `--include-pass 'YourTestName'`. See the per-test filter table in `rules/ci_monitor.md`.

## Post-clear verification workflow

Triggered only after Reviewer approval **and** a `Clear` line. The shape is: ask, then plan, then review, then execute. The binding version is the numbered procedure in the "CI checking after a Reviewer exits" section of `rules/ci_monitor.md`; the full nine steps are reproduced here.

1. Scan the issue description, the PR description, and all comments on both for verification steps, acceptance criteria, or manual test instructions that are **not** already covered by automated tests.
2. If none are found, the PR may be merged; you are done.
3. Add the `verification needed` label to the PR and/or issue where outstanding steps were found.
4. Show the user the list of outstanding unautomated verification steps.
5. Ask the user: do they want to run these tests manually, or have an agent plan automation for them?
6. If the user chooses manual testing or no automation, orchestration is complete; the PR may be merged once manual testing is done.
7. Otherwise spawn a **fresh** sub-agent (no prior conversation context) briefed with: the list of unautomated steps, a pointer to the existing test infrastructure (test directories, CI config, framework in use), and instructions to produce a concrete automation **plan** without implementing anything. Give it no other context.
8. Spawn a Reviewer to evaluate that plan. If the Reviewer requests changes, route back to the planning sub-agent (step 7) with the feedback and repeat until the plan is approved.
9. Once the plan is approved, dispatch an Author to implement the automation, then run the normal Author -> Reviewer -> CI Monitor cycle on the result. The PR may be merged after that work clears CI.
