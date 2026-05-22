There is no long-poll or HTTP-push API for PR or CI status in GitHub's public surface. The options are:

| Mechanism | Push or poll? | Usable in-session? | Notes |
|---|---|---|---|
| REST API (`/check-runs`, `/pulls`) | Poll | Yes | What the Monitor loop uses; 30 s interval is safe |
| GraphQL API | Poll | Yes | No subscriptions for PR/CI state |
| Webhooks | Push (server receives POST) | No | Requires a publicly reachable HTTPS endpoint; not available inside an ephemeral agent container |
| WebSocket / SSE | Push | No | GitHub's web UI gets live updates via an internal mechanism; no public API equivalent exists |
| `subscribe_pr_activity` MCP tool | Push (via MCP infrastructure) | Yes | Delivers events as `<github-webhook-activity>` messages — but mixes CI events with review comments and other PR activity (see separate comment) |

**Conclusion for in-session CI watching:** polling via the REST API is the only reliable, fully self-contained option. The Monitor tool makes this ergonomic by running the poll loop in the background.

---

**Source:** [Issue #203 - Replace CiWatcher subagents with a background Monitor loop](https://github.com/aunger/gallery-button-for-pixel-camera/issues/203#issuecomment-4508569279)
