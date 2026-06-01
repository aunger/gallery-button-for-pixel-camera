# Phase 5 resource: Converge or escalate

Read this to route a Reviewer verdict and to know when to stop.

The binding rules are the "Conditional approval", "Author disagreement", and "When to abort" sections of `agents/dev_orchestration.md`.
This resource is a routing summary; the linked sections govern.

## Routing a verdict

- **Changes requested:** route to the Author (Phase 2, prefer resume), then dispatch a full Reviewer again (Phase 3).
- **Plain approval:** go to Phase 4 CI watching (`resources/ci-watch.md`).
- **Conditional approval:** follow the conditional-approval workflow below.

## Conditional approval

If the requested change involves multiple locations, design choices, or non-trivial logic, treat it as changes requested (full cycle).
Otherwise:

1. Route the single named change to the Author (prefer resume).
2. After the Author commits it, spawn a Haiku sanity-check agent with narrowed context: only the Reviewer's verbatim instruction and the Author's new diff. No full PR diff, no prior review history. Use the exact prompt in `agents/dev_orchestration.md`.
3. Haiku answer A: treat as approved, go to Phase 4. Answer B: resume the normal cycle with a full Reviewer. Answer C: stop and escalate to the user.

## When to abort

Stop and escalate to the user when:

- Four rounds of Author/Reviewer have not reached consensus (unless the user set a different threshold).
- The Author gives up or says the issue cannot be solved as stated.
- The Author introduces new ideas after conditional approval (Haiku does not answer A or B).
