# Delegating to nested sub-agents, as a sub-agent

If you are a sub-agent dispatching a nested sub-agent via the Agent tool,
follow these rules:

- **Use foreground (the default) when you need the results in the same turn.**
  Do not set `run_in_background: true`
  for any sub-agent whose findings you will act on before returning.
  Launching a sub-agent with `run_in_background: true` may force your turn to
  end before sub-agent results are available.
  The Agent tool blocks until the sub-agent completes,
  so the results are available when the call returns.
  A foreground sub-agent blocks the Agent tool call;
  you cannot exit before it finishes.
  The result is returned to you when the call returns--consume or record it,
  then exit.
- **Use `run_in_background: true` only for fire-and-forget work whose results
  are not needed in the current turn.**
  If you are unsure whether you will need the results, use foreground.
- **Never rely on a background sub-agent's output in the same turn.**
  Background sub-agents surface as task-notification events after you exit.
  Their results are not available to you during the current turn.
