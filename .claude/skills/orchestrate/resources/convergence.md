# Phase 5 resource: Converge or escalate

Read this to route a Reviewer verdict and to know when to stop.

The binding rules are the "Conditional approval", "Author disagreement", and "When to abort" sections of `rules/orchestration.md`.
This resource is a routing summary; the linked sections govern.

## Routing a verdict

- **Changes requested:** route to the Author (Phase 2, prefer resume), then dispatch a full Reviewer again (Phase 3).
- **Plain approval:** go to Phase 4 CI watching (`resources/ci-watch.md`).
- **Conditional approval:** follow the conditional-approval workflow below.

## Author disagreement is normal, not a stall

The Author is permitted to disagree with a review point and make its case in PR comments rather than acquiescing (see the "Author disagreement" section of `rules/orchestration.md`).
When this happens during a cycle, do not treat it as a stall or a failure to converge. Relay the exchange between Author and Reviewer verbatim and let the cycle continue.
It counts toward the abort threshold only as one of the normal Author/Reviewer rounds (see "When to abort" below).

## Conditional approval

If the requested change involves multiple locations, design choices, or non-trivial logic, treat it as changes requested (full cycle).
Otherwise:

1. Route the single named change to the Author (prefer resume).
2. After the Author commits it, spawn a Haiku sanity-check agent (model: haiku) with narrowed context: only the Reviewer's verbatim instruction and the Author's new diff. No full PR diff, no prior review history.
3. Prompt the Haiku agent with exactly this (verbatim, do not paraphrase or abbreviate):

   > The Reviewer requested
   > [specific change]
   >
   > The Author responded with
   > [diff]
   >
   > Answer one of three ways: (A) the Author fully addressed the requested change and introduced no other concerns; (B) the Author did not address the requested change (incomplete or missing work, no new concerns raised); or (C) the Author's response raises a new concern beyond the scope of the original request.

4. Route on the Haiku answer:
   - **A** -- treat as approved; go to Phase 4 (do not run another full review cycle).
   - **B** -- the PR has not converged; resume the normal cycle with a full Reviewer (Phase 3).
   - **C** -- the response raises a new concern; stop the cycle and escalate to the user.
   - **Anything that is not a clear-cut A, B, or C** (a hedged or ambiguous answer, e.g. "it mostly addresses the concern but...") -- also abort and escalate to the user. Do not treat an unclear answer as a B to route back to the Author.

## When to abort

Stop and escalate to the user when:

- Four rounds of Author/Reviewer have not reached consensus (unless the user set a different threshold).
- The Author gives up or says the issue cannot be solved as stated.
- The Author introduces a new concern after conditional approval (Haiku answers C), or the Haiku agent gives any answer that is not a clear-cut A or B.
