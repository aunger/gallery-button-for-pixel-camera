# Phase 3 resource: Dispatch the Reviewer

Read this when the Author reports a pushed PR ready for review.

The binding rules are in the "Assigning a Reviewer" section of `rules/orchestration.md`, and the Reviewer's own conduct rules are in `rules/review_cycle.md`.
This resource adds only the mechanics specific to the skill.

## Steps

1. Choose the Reviewer model per Model selection (see `resources/model-selection.md`). Honor the Reviewer floor: Reviewer tier is at least Author tier minus 1.
2. Fill in `templates/reviewer-brief.md` completely.
3. Dispatch the Reviewer sub-agent with the filled template and nothing else from your own analysis.
4. Do not pre-diagnose the PR or hint at what you think the Reviewer should find.

## After the Reviewer returns

The Reviewer posts its verdict as an ordinary PR comment (shared account; no GitHub review feature).
Read the verdict and route per Phase 5 (`resources/convergence.md`):

- Changes requested: route back to the Author (Phase 2, prefer resume).
- Approval: proceed to Phase 4 CI watching (`resources/ci-watch.md`).
- Conditional approval: follow the conditional-approval branch in `resources/convergence.md`.

## Timing

Run `date -u` before dispatch and after return; report both to the user.
`${CLAUDE_PLUGIN_ROOT}/scripts/dispatch_timer.py mark` prints a canonical timestamp, and `${CLAUDE_PLUGIN_ROOT}/scripts/dispatch_timer.py report --start <ts> --end <ts>` formats both marks and the elapsed duration into one line.

The Delegation rules summarized in `resources/dispatch-author.md` (resume over replace via `SendMessage`, the "disregard in-progress noise" rules, and "completion and exit are the same event") apply to dispatching the Reviewer too.
