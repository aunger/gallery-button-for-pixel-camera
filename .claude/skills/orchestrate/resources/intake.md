# Phase 1 resource: Intake

Read this when you are starting the workflow and need to decide whether work already exists.

## Decide the starting state

- If the issue already has a branch and an open PR, skip to Phase 2 (dispatch or resume the Author) only if changes are needed, or to Phase 3 (Reviewer) if the PR is ready for review.
- If the issue has no branch and no PR, this is fresh work. Read `rules/inaugurate.md` for the full fresh-start protocol before dispatching anyone.

## What you must collect before dispatching

- The issue or PR number.
- The verbatim user instruction (you will relay relevant parts, unaltered).
- The branch name. For fresh work, follow the naming pattern in `rules/inaugurate.md`: `fix/issue-N-short-description` or `feature/issue-N-short-description`.

## Multiple issues at once

If the user asks you to orchestrate more than one independent issue, you may **dispatch in parallel** (per the Delegation rules in `rules/dev_orchestration.md` and the Parallel dispatch section of `rules/inaugurate.md`).
Each parallel issue must have its **own distinct branch and worktree** and its **own independent Author and Reviewer**.
Verify the branch names differ before sending the parallel dispatch; never point two unrelated issues at the same branch.

## Do not

- Do not read source files or diagnose the issue yourself. That is the Author's job.
- Do not reword the user's instruction. Relay it verbatim.
