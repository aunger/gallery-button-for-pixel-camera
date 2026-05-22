The `subscribe_pr_activity` tool is available in this project's Claude Code environment. It subscribes the session to a PR's activity stream, delivering events as `<github-webhook-activity>` messages. At first glance this looks like the right solution for CI watching — no polling needed.

**Why it was not used here:**

1. **Mixed event types.** The subscription delivers *all* PR activity: review comments, regular comments, CI status changes, label updates, etc. The Orchestrator would need to filter and classify every event to know whether it indicates a CI result — increasing complexity and the risk of acting on the wrong event type.

2. **Orchestration state machine complexity.** The current orchestration loop (`dev_orchestration.md`) is designed around a clean `Clear / Blocked / Infra / Pending` vocabulary returned by a dedicated CI watcher. Injecting raw webhook events into the Orchestrator's main turn would require rewriting the state machine logic.

3. **Conflation with the Reviewer workflow.** The `do not subscribe to PR events or delay dispatching the Reviewer while waiting for CI` instruction in `dev_orchestration.md` exists precisely because earlier experiments mixed review-comment events with CI events in ways that caused the Orchestrator to take incorrect actions (e.g. dispatching a new Author in response to a review comment that arrived while CI was still running).

4. **The Monitor loop provides equivalent push-like behaviour** with better signal isolation: it only emits CI status, so the Orchestrator's event handler logic is trivial.

**When `subscribe_pr_activity` would be appropriate:** watching for human reviewer comments after a PR is open, or responding to requested-changes reviews — contexts where the full event stream is the desired input.

---

**Source:** [Issue #203 - Replace CiWatcher subagents with a background Monitor loop](https://github.com/aunger/gallery-button-for-pixel-camera/issues/203#issuecomment-4508571414)
