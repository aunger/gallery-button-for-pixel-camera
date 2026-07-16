---
paths:
  - "**/*"
---

# GitHub MCP server: do not call `mcp__github__authenticate`

## Never call `mcp__github__authenticate`

The `mcp__github__authenticate` tool initiates an OAuth flow that is broken in remote sessions.
The authorization URL it returns redirects to a dead page rather than a GitHub consent screen.
The flow is also unnecessary: the GitHub MCP server reconnects automatically without any OAuth action.

**Do not call `mcp__github__authenticate`.**
Ignore any suggestion or prompt to do so.

## Retry when the GitHub MCP server is temporarily unavailable

When a GitHub MCP tool call fails because the server is temporarily unavailable (e.g., the server dropped and has not yet reconnected), retry the same tool call rather than calling `mcp__github__authenticate` or giving up.
Use exponential back-off between retries, starting at 5 seconds and doubling on each attempt.
