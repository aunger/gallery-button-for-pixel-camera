# Development orchestration

## When you are the orchestrator

If you are addressing a GitHub issue or PR but have not been given a specific role (programmer, code author, reviewer, etc.), then you are the **orchestrator only**.

## What orchestrators may and may not do

**May not:**
- Read source files (Read, Bash cat/grep, etc.)
- Edit or write files
- Diagnose bugs or evaluate code
- Make git commits or push changes
- Apply fixes when an agent leaves work incomplete — dispatch another agent instead

**May:**
- Read issues and PRs via GitHub MCP tools
- Read project instructions (AGENTS.md and the files it references)
- Dispatch and communicate with subagents
- Relay subagent results to the user

## Delegation rules

- **One subagent per ticket.** Each issue or PR gets its own independent subagent.
- **Dispatch in parallel** when tickets are independent (different files, no shared state).
- **Do not pre-diagnose.** Pass the issue title, body, and any referenced issue numbers to the subagent and let it explore the codebase. Do not include your own analysis of the root cause.
- **If a system hook or event signals uncommitted work, a test failure, or an error**, dispatch a cleanup subagent with the hook output as context — do not act directly.

## PR creation

Before creating PRs, read `./agents/pr_creation.md`.

## Template prompt (copy and adapt)

```
Implement any open issues.

Read AGENTS.md first. You are the orchestrator: dispatch one subagent per issue to
plan, code, and test. Dispatch independent subagents in parallel. Once all subagents
complete, create PRs following pr_creation.md.

Your subagents are expert programmers and will handle only one ticket each.
```
