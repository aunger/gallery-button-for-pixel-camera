A `stop` hook fires at the **end of each response turn**, not at the end of the session.

Every time Claude finishes generating a response and hands control back to the user (or to the harness), the stop hook fires. This means:

- It fires repeatedly throughout a session, once per assistant turn.
- When a subagent fires its stop hook, the subagent is still alive — it receives the hook's output as a new "user" message and can act on it (e.g. commit and push) before its final exit.
- The hook is therefore useful for enforcing end-of-turn invariants ("always commit before exiting"), not for one-time session teardown.

This is relevant to CI watching because a stop hook on the Orchestrator could, in principle, snapshot state at the end of every turn — but it cannot be used as a session-end callback to clean up a dangling Monitor.

---

**Source:** [Issue #203 - Replace CiWatcher subagents with a background Monitor loop](https://github.com/aunger/gallery-button-for-pixel-camera/issues/203#issuecomment-4508567438)
