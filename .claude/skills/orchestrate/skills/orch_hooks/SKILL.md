---
name: orch_hooks
description: "Disengage or re-engage the orchestrate plugin's hooks (the dispatch-form check and the Orchestrator guard) for this session. Usage: /orch_hooks disengage, /orch_hooks reengage, /orch_hooks status."
argument-hint: disengage | reengage | status
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/hooks/hook_state.py *)
---

# Orchestrate hooks: `$ARGUMENTS`

The orchestrate plugin's two hooks enforce the Orchestrator's rules: `check_dispatch.py` refuses a dispatch to a plugin agent that is not the dispatch form, and `orchestrator_guard.py`, once `/orchestrate` has run in a session, refuses edits, source reads, and free-text messages to agents in that session.
This skill switches both off, or back on, for the current session only.
A person runs it; the model cannot invoke it, because a guard the guarded party can lift is not a guard.

Run exactly this command and report its one line of output verbatim:

```text
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/hooks/hook_state.py $ARGUMENTS --session ${CLAUDE_SESSION_ID}
```

- `disengage` lists this session in the plugin's state file, `${CLAUDE_PLUGIN_ROOT}/.state/disengaged-sessions`; every hook that sees this session's id then exits before doing anything.
- `reengage` removes it.
- `status` says which state the session is in.

If the command prints a usage error, say so and do nothing else; do not edit the state file or set `ORCHESTRATE_HOOKS_DISENGAGED` yourself.
The hooks stay registered either way; disengaging changes what they do, not whether they run.
