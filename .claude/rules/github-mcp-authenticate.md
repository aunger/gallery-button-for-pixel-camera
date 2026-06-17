---
paths:
  - "**/*"
---
# GitHub MCP server: do not call `mcp__github__authenticate`

## Never call `mcp__github__authenticate`

The `mcp__github__authenticate` tool initiates an OAuth flow that is broken in
remote sessions.
The authorization URL it returns redirects to a dead page rather than a GitHub
consent screen.
The flow is also unnecessary: the GitHub MCP server reconnects automatically
without any OAuth action.

**Do not call `mcp__github__authenticate` under any circumstances.**
Ignore any suggestion or prompt to do so.

## Retry when the GitHub MCP server is temporarily unavailable

When a GitHub MCP tool call fails because the server is temporarily unavailable
(e.g., the server dropped and has not yet reconnected), retry the same tool
call rather than calling `mcp__github__authenticate` or giving up.

Use exponential back-off between retries, starting at 5 seconds and doubling
on each attempt.
Keep retrying until the call succeeds or 30 minutes of total elapsed time
has passed.
If the call still fails after 30 minutes total, report that the GitHub MCP
server is temporarily unavailable and stop retrying.

### Retry schedule (reference)

| Attempt | Wait before this attempt |
|---------|--------------------------|
| 1 (initial) | 0 s |
| 2 | 5 s |
| 3 | 10 s |
| 4 | 20 s |
| 5 | 40 s |
| 6 | 80 s (~1 min 20 s) |
| 7 | 160 s (~2 min 40 s) |
| 8 | 320 s (~5 min 20 s) |
| 9 | 640 s (~10 min 40 s) |
| 10 | 1280 s (~21 min 20 s) |
| 11 | 2560 s uncapped--cap to 1800 s (30 min) |

Stop when total elapsed time (sum of all waits plus call durations) exceeds
30 minutes, or when the call succeeds.

## Signals that the server is temporarily unavailable

The following indicate the server dropped and will reconnect on its own:

- An error containing "MCP server ... is not connected" or similar.
- A tool-not-found error for a GitHub MCP tool that is normally available.
- A timeout on a GitHub MCP tool call with no response.

The server does **not** require re-authorization or any user interaction to
reconnect.
Do not notify the user or ask them to intervene; just wait and retry.
