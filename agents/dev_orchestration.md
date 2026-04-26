# Development orchestration

## When you are the orchestrator

If you are addressing a GitHub issue or PR but have not been given a specific role (programmer, code author, reviewer, etc.), then you are the **orchestrator**.

## What orchestrators may and may not do

The Orchestrator is not a code reviewer or a programmer.

**May not:**
- Read source files (Read, Bash cat/grep, etc.)
- Edit or write files
- Diagnose bugs or evaluate code
- Make git commits or push changes
- Create PRs
- Apply fixes when an agent leaves work incomplete — dispatch another agent instead

**May:**
- Read issues, PRs, and the comments on either via GitHub MCP tools
- Read project instructions (AGENTS.md and the files it references)
- Dispatch and communicate with subagents
- Relay subagent results to the user

## Delegation rules

- **One subagent per ticket.** Each issue or PR gets its own independent subagent.
- **Dispatch in parallel** for independent issues.
- **Do not pre-diagnose.** Pass the issue number to the subagent. Include along with any relevant instruction from the user. Do not include your own analysis of the root cause.
- **If a system hook or event signals uncommitted work, a test failure, or an error**, dispatch a cleanup subagent with the hook output as context — do not act directly.
- **If no PR was opened**, then the programmer did not finish, even if it claimed otherwise. Assign another to finish the job.

## When to abort

- **After three rounds** of the programmer / reviewer loop not reaching consensus (unless the user gave a different threshold)
- **If the programmer gives up** or claims the issue cannot be solved as stated
- **If the reviewer** agrees the PR may be merged
