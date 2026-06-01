# Phase 2 resource: Dispatch the Author

Read this when the issue is ready for implementation work.

The binding rules are in the "Assigning a Programmer" section of `agents/dev_orchestration.md`.
This resource adds only the mechanics specific to the skill.

## Steps

1. Choose the Author model per Model selection (see `resources/model-selection.md`).
2. Ensure a dedicated per-issue branch exists (one branch per issue, never shared with unrelated work).
3. Fill in `templates/author-brief.md` completely. Every field is required.
4. Dispatch the Author sub-agent with the filled template as its briefing, and nothing else from your own analysis.

## Resuming instead of replacing

For follow-up rounds, prefer resuming the existing Author over spawning a replacement, per the Delegation rules in `agents/dev_orchestration.md`.
When you resume, you still route through the template so the new instruction is scoped and free of your own diagnosis.

## Timing

Run `date -u` immediately before dispatching and immediately after the Author returns, and report both times to the user (Delegation rules).
