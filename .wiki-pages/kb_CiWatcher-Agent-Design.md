The CiWatcher agent is a specialized orchestration component for polling CI status in projects where the main workflow (Reviewer/Author agents) must not block waiting for CI to complete.

## The Problem It Solves

In PR workflows, the Reviewer agent must wait for CI to complete before deciding whether to approve or request changes. However, waiting synchronously has two problems:

### Problem 1: Turn-Time Limits
Agents have bounded turn duration (typically 5-30 minutes depending on the task). If you try to poll CI synchronously in the Reviewer's main loop:
- Each poll takes a few seconds
- To wait ~15 minutes for slow CI, you need ~180 polls
- This exhausts the agent's turn time budget before CI completes
- Result: Agent timeout, incomplete review

### Problem 2: Monitor Tool Timeout Mismatch
The `Monitor` tool (for background polling) has its own `timeout_ms` parameter. If you use Monitor for CI polling:
- Set timeout too short (~10 min): Monitor exits before emulator CI finishes (typical: 7-12 min per test)
- Set timeout too long (>30 min): Monitor never times out; Reviewer can't act on timeout
- Result: Mismatch between CI reality and Reviewer expectations

## The CiWatcher Solution

**Design:** A dedicated short-lived agent, spawned **by the Orchestrator**, that:
1. Polls CI status independently
2. Runs for a **bounded window** (~2.5 minutes per spawn, typically 3-5 spawns per PR)
3. Returns a simple status: `Clear` / `Blocked` / `Pending`
4. Exits immediately after returning

The Orchestrator:
- Spawns CiWatcher
- Gets back a status
- If `Pending`, waits ~2.5 minutes and spawns another CiWatcher
- Continues looping until CI is `Clear` or `Blocked`

## Why This Works

- **No turn-time issue:** CiWatcher is short-lived (minutes, not hours), so it completes before timeout
- **No timeout mismatch:** Orchestrator controls the loop, not Monitor's timeout
- **Clean separation:** Reviewer focuses on review; CiWatcher focuses on CI polling
- **Scalable:** Can spawn multiple CiWatchers in parallel if needed (though not typical)

## Operational Considerations

- CiWatcher should use the **Checks API** (`/repos/{owner}/{repo}/commits/{sha}/check-runs`), not legacy status API
- Poll interval: 30 seconds is reasonable (GitHub rate limits allow much higher)
- Status vocabulary must match Orchestrator expectations: `Clear`, `Blocked`, `Pending`, `Infra` (infrastructure failure)

---

**Source:** [PR #146 — CiWatcher Agent Design](https://github.com/aunger/gallery-button-for-pixel-camera/pull/146)
