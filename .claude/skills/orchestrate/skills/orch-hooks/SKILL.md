---
name: orch-hooks
description: "Disengage or engage the orchestrate plugin's hooks (the dispatch-form check and the Orchestrator guard) for this session. Usage: /orch-hooks disengage, /orch-hooks engage, /orch-hooks status."
argument-hint: disengage | engage | status
disable-model-invocation: true
---

# Orchestrate hooks: `$ARGUMENTS`

The orchestrate plugin's hooks enforce the Orchestrator's rules: one refuses a dispatch to a plugin agent that is not the dispatch form, and the Orchestrator guard, once `/orchestrate` has run in a session, refuses edits, source reads, and other commands in that session.
`/orch-hooks disengage` switches them off for the current session only, `/orch-hooks engage` switches them back on, and `/orch-hooks status` reports.

The change has already been made by the time you read this.
The plugin applies it in a hook that runs when a person types the command, which is why only a person can disengage the hooks: a guard the guarded party can lift is not a guard.
That hook added one line beginning `orch-hooks result:` to the context of this message.

Repeat that line to the user verbatim, and do nothing else.
If no such line is present, say that the plugin's hook did not run, so nothing changed.
Do not run any command, edit any file, or set `ORCHESTRATE_HOOKS_DISENGAGED` to change the state yourself.
