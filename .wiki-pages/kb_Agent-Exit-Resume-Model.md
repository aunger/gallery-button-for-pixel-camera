Claude Code agents have a specific exit/resume model that affects how orchestrators should manage agent lifecycles.

## Key Principle: Exit ≡ Completion

There is **no suspended state** between execution and exit. When an agent finishes its work and hands control back to the user (or orchestrator):
- It has **completed** its execution
- It immediately **exits**
- These are the same event, not two separate states

An agent cannot be suspended waiting for input; it either runs or doesn't.

## How This Affects Orchestrators

### Prefer Resuming Over Spawning

When you need to continue work that an agent just finished:

**Better approach:** Use `SendMessage` to resume the recently-exited agent
```
SendMessage(to: agent_id, message: "...")
```

**Worse approach:** Spawn a new agent
```
Agent(description: "...", prompt: "...")  # Creates fresh agent, loses context
```

**Why:** `SendMessage` to a recently-exited agent preserves its full context — all prior messages, tool results, reasoning. The agent wakes up with perfect continuity.

### Time-Window Limits

There's a **time limit** on how long after exit you can resume an agent. The exact window depends on the environment:
- Cloud sessions: typically a few hours
- Local sessions: may vary

**Implication:** Don't rely on resuming an agent after a long pause. If you're uncertain about the time window, spawning a fresh agent (with explicit context-passing) is safer.

## Implementation Pattern

```
# Agent finishes and exits
agent_result = Agent(...)

# Immediately resume if you need more from the same agent
SendMessage(to: agent_id, message: "Next task: ...")

# Later (hours later): safer to spawn fresh
Agent(description: "...", prompt: "Previous context: ...\n\nNext task: ...")
```

---

**Source:** [PR #139 — Agent Exit/Resume Model Clarification](https://github.com/aunger/gallery-button-for-pixel-camera/pull/139)
