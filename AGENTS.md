# Instructions for agents

- If addressing a GitHub issue or PR without a specific assigned role, read `./agents/dev_orchestration.md`.
- If participating in a PR or review (as Author, Reviewer, or Orchestrator), read `./agents/pr_participation.md`.
- If creating a PR, read `./agents/pr_creation.md`.
- If editing code, read `./agents/code_edit.md`.
- If writing an issue description or a comment, read `./agents/writing.md`.
- If scanning for outstanding before-merging requirements (unautomated verification steps or changes outside the repo), or planning automation of such steps, read `./agents/verification_planning.md`.
- If carrying out the before-merging steps from a Verification Planner report on a PR (as a Verification Agent), read `./agents/pr_verify.md`.
- If a sub-agent that is delegating work to nested sub-agents via the Agent tool, read `./agents/subagent_delegation.md`.
- If bumping the Gradle, AGP, KGP, or Compose-plugin version, or working on a Dependabot PR or the `gradle/verification-metadata.xml` regeneration workflows, read `./gradle/README.md`.

## Scratchpad files

Every agent in a session, including each worktree sub-agent, is given the same scratchpad directory.
A helper left at its top level can be overwritten or run by another agent, which then reports results from a tree it never touched.

- Keep scratch files in a subdirectory of the scratchpad named after your worktree directory (`agent-<id>`), or for the top-level agent, the checkout directory, never at its top level.
- A helper that reports a test result prints the worktree path and `HEAD` it ran against, and you check both before reporting.

The second point is what catches a crossed result; the first only makes one rarer.
`.claude/hooks/post-tool-use-scratchpad-isolation.sh` warns a worktree agent whose `Bash` command, or `Write`, `Edit` or `NotebookEdit` path, reaches the scratchpad outside its subdirectory.

## Reading a commit's check-runs

A commit keeps every check-run ever attached to it.
Each new workflow run adds its check-runs beside the earlier ones of the same name without retiring them, and each label event on a PR fires the `No blocking labels` gate as a new workflow run, so one head commit can carry several failures of a check that is now green.
GitHub judges a required check by the latest run of its name on the commit, and so must you.

To read a commit's check-runs as they stand now, from `GET /repos/{owner}/{repo}/commits/{sha}/check-runs` or the `get_check_runs` method of `mcp__github__pull_request_read`:

- Read every page, until you hold `total_count` runs.
  The default page is 30 runs, newest first, and accumulated label-gate runs can push a check's only run off it (issue #1225).
- Keep only the run with the highest `id` for each `name` before reading or counting any conclusion.
  Judge recency by `id`, not `started_at`, which is null on a run still queued (issue #719).

To wait for CI to finish instead, use `python3 scripts/ci_monitor/ci_monitor.py --pr <N>` or `--sha <SHA>` (usage in `scripts/ci_monitor/README.md`).
It is a poll loop, not a snapshot reader: it streams progress lines until every check-run it counts has completed, and it sets no deadline of its own.
Only then does it print its per-check summary block, already collapsed to the latest run per name by `latest_check_runs` (`scripts/ci_monitor/ci_monitor.py:380`).
A role told not to block on CI, such as the Reviewer, reads the listing as above instead.
Until issue #1225 is fixed, the Monitor reads only the listing's first page, so a check with no row in its summary may have run anyway.

The Orchestrator fetches no check-runs (rule 4 of "Orchestrator communication discipline" in `agents/dev_orchestration.md`), so the listing rules above are not for it; the issue #1225 caveat on the Monitor's summary applies to it all the same.
