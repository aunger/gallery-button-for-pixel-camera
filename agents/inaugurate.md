# Inaugurating work on new issues

Use this protocol when acting as Orchestrator and tasked with starting fresh work on one or more issues that have no existing PR or branch.

## One branch per issue — no exceptions

Each issue must be developed on its own dedicated branch. This is the branch-level enforcement of the one-topic-per-PR rule.

- Never direct two Programmers for unrelated issues to the same branch.
- Name branches: `fix/issue-N-short-description` for bug fixes, `feature/issue-N-short-description` for new features.

## Session-designated branches

A session setup may specify a single "development branch." Treat this as context or a naming hint — not as the branch each Programmer must commit to. Each Programmer still gets their own per-issue branch (which may incorporate the session branch name as inspiration, e.g. `fix/issue-56-...`).

## Dispatching each Programmer

Use the dispatch template defined in `dev_orchestration.md`.
Fill the branch name (following the naming pattern above) and the issue number (given by the user) as literal tokens.
Commit/PR duties and branch-isolation rules are discoverable by the sub-agent via CLAUDE.md and need not be re-narrated.

## Parallel dispatch

Dispatching Programmers in parallel for independent issues is correct — but each must receive a different branch name. Verify this before sending the parallel dispatch.
