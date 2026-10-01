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

Every sub-agent in a session is given the same scratchpad directory, though each has its own worktree.
A helper left at its top level can be overwritten or run by another agent, which then reports results from a tree it never touched.

- Keep scratch files in a subdirectory of the scratchpad named after the directory you work in (for a worktree, `agent-<id>`), never at its top level.
- A helper that reports a test result prints the worktree path and `HEAD` it ran against, and you check both before reporting.

The second point is what catches a crossed result; the first only makes one rarer.
`.claude/hooks/post-tool-use-scratchpad-isolation.sh` warns a worktree agent whose `Bash` command, or `Write`, `Edit` or `NotebookEdit` path, reaches the scratchpad outside its subdirectory.
