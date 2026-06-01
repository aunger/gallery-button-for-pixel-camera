# Phase 2 resource: Dispatch the Author

Read this when the issue is ready for implementation work.

The binding rules are in the "Assigning a Programmer" section of `rules/orchestration.md`.
This resource adds only the mechanics specific to the skill.

## Steps

1. Choose the Author model per Model selection (see `resources/model-selection.md`).
2. Ensure a dedicated per-issue branch exists (one branch per issue, never shared with unrelated work).
3. Fill in `templates/author-brief.md` completely. Every field is required.
4. Dispatch the Author sub-agent with the filled template as its briefing, and nothing else from your own analysis.

## Resuming instead of replacing

For follow-up rounds, prefer resuming the existing Author over spawning a replacement, per the Delegation rules in `rules/orchestration.md`.
When you resume, you still route through the template so the new instruction is scoped and free of your own diagnosis.

## Timing

Run `date -u` immediately before dispatching and immediately after the Author returns, and report both times to the user (Delegation rules).
For convenience, `${CLAUDE_PLUGIN_ROOT}/scripts/dispatch_timer.py mark` prints a timestamp in a canonical format, and `${CLAUDE_PLUGIN_ROOT}/scripts/dispatch_timer.py report --start <ts> --end <ts>` formats the two marks plus the elapsed duration into one line.

## Delegation rules (summary)

The full Delegation rules are in `rules/orchestration.md`. The ones you are most likely to need mid-dispatch:

- **Resume, do not replace.** For follow-up rounds (or if an agent exits with work unfinished), resume the existing Author with `SendMessage` addressed to that agent's ID, which preserves its full prior context. Only if the ID is unavailable or resumption fails do you spawn a replacement and reconstruct context from the PR, issue, and prior comments.
- **One branch per ticket; separate sub-agents per ticket.** Never point two unrelated issues at the same branch or the same Author.
- **Do not pre-diagnose.** Never add your own root-cause analysis to the briefing.
- **Disregard in-progress noise while a sub-agent is active.** Specifically: disregard hooks or events signalling uncommitted work; do not intervene on a test-failure or error event while the agent or a CI gate is still running; and treat a `"file was modified, either by the user or a linter"` reminder as the active sub-agent editing the shared tree (do not interrupt). Only treat such a reminder as external if you have no active sub-agent.
- **Completion and exit are the same event.** When a background sub-agent finishes its turn you get one task-notification; there is no separate idle state to wait through.
