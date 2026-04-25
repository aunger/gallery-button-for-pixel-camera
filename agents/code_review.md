# Code review standards

## Code reviews must be conversational, and slightly competitive, between **at least two parties**.

- A *Reviewer* agent (or even its managing agent) must not make code changes itself, but should communicate discoveries clearly enough to convince a code author of the need to change the PR.
- An *Author* agent should consider review comments with a degree of skepticism, and should not instantly or automatically accede to a reviewer's opinion. If the Author becomes convinced of the need to change the PR, then it should do so. Otherwise, it should enter a debate with the Reviewer.
- This slightly competitive interaction between at least two parties is important to the SDLC, because it reduces the presence of untested ideas in our code.

## Code review cycles should be overseen by a manager agent.

- If a reviewer requests a change, the manager may take on the role of coder to address it, but only for mechanical fixes (typos, import cleanup, renaming) where no design judgment is required.
- A manager must not step into the role of reviewer, which should be independent.

## Scope

- During the code review process, don't allow the scope of work to increase.
- Instead, spawn related items in the issue tracker.

## Creating a PR

Do not combine unrelated work in a single PR. You may resolve multiple issues with one PR, but **only** if they are both symptoms of the same technical issue.
