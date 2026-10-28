# Delegating to nested sub-agents, as a sub-agent

If you dispatch a nested sub-agent with the Agent tool, follow these rules:

- **Use foreground (the default) when you need the results in the same turn.**
  Do not set `run_in_background: true` for any sub-agent whose findings you will act on before returning.
  Launching a sub-agent in the background may force your turn to end before its results are available.
  A foreground sub-agent blocks the Agent tool call; you cannot exit before it finishes, and its result is returned to you when the call returns.
  Consume or record it, then exit.
- **Use `run_in_background: true` only for fire-and-forget work whose results are not needed in the current turn.**
  If you are unsure whether you will need the results, use foreground.
- **Never rely on a background sub-agent's output in the same turn.**
  Background sub-agents surface as task-notification events after you exit; their results are not available to you during the current turn.
- Keep scratch files in a subdirectory of the shared scratchpad named after your worktree directory (`agent-<id>`), never at its top level, and before reporting a helper's result confirm it printed the worktree and `HEAD` it ran against.
