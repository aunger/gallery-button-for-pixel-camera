# Orchestrate plugin hooks

Two hooks support the `/orchestrate` workflow. As plugin hooks they fire only
while the `orchestrate` plugin is enabled, which is exactly the session scoping
the workflow needs: they act during orchestration, not during ordinary Author or
maintenance edits in a repo where the plugin is absent.

`hooks.json` in this directory wires `orchestrate-guard.sh` as a `PreToolUse`
hook on `Edit|Write|NotebookEdit|Bash`. Claude Code substitutes
`${CLAUDE_PLUGIN_ROOT}` with the plugin's install path, so the wiring is
location-independent.

## orchestrate-guard.sh (enforce a boundary)

A `PreToolUse` guard that reads the hook JSON object on stdin and reminds the
Orchestrator not to edit, write, or commit files itself, nor read source through
the shell (per `rules/orchestration.md`). It is advisory by default (prints
to stderr, exits 0). Set `ORCHESTRATE_GUARD_BLOCK=1` to make it a hard block
(exit 2) for the forbidden tools `Edit`, `Write`, and `NotebookEdit`. The Bash
source-reading advisory never hard-blocks, because its heuristic can have false
positives.

## surface-phase.sh (just-in-time information)

A helper that prints the one progressive-revelation resource to read for a given
workflow phase, so the Orchestrator loads only what the current phase needs:

```bash
"${CLAUDE_PLUGIN_ROOT}/hooks/surface-phase.sh" author
# -> ${CLAUDE_PLUGIN_ROOT}/resources/dispatch-author.md
```

Phases: `intake`, `author`, `reviewer`, `ci`, `converge`, `model`.
