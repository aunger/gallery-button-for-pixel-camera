# Orchestrate skill hooks

Two hooks support the `/orchestrate` workflow. They are scoped to the skill, not
wired as repo-global hooks, because they should act only while the orchestration
workflow is running, not during ordinary Author or maintenance edits.

## orchestrate-guard.sh (enforce a boundary)

A `PreToolUse`-shaped guard that reads a hook JSON object on stdin and reminds
the Orchestrator not to edit, write, or commit files itself (per
`agents/dev_orchestration.md`). It is advisory by default (prints to stderr,
exits 0). Set `ORCHESTRATE_GUARD_BLOCK=1` to make it a hard block (exit 2) for
the forbidden tools `Edit`, `Write`, and `NotebookEdit`.

To enable it for an orchestration session, register it as a `PreToolUse` hook in
a session-scoped or user settings file (not the repo-global
`.claude/settings.json`, which would fire it for every edit in the repo):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|NotebookEdit",
        "hooks": [
          { "type": "command",
            "command": "$CLAUDE_PROJECT_DIR/.claude/skills/orchestrate/hooks/orchestrate-guard.sh" }
        ]
      }
    ]
  }
}
```

## surface-phase.sh (just-in-time information)

A helper that prints the one progressive-revelation resource to read for a given
workflow phase, so the Orchestrator loads only what the current phase needs:

```bash
.claude/skills/orchestrate/hooks/surface-phase.sh author
# -> .claude/skills/orchestrate/resources/dispatch-author.md
```

Phases: `intake`, `author`, `reviewer`, `ci`, `converge`, `model`.
