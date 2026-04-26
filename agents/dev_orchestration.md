# Development orchestration

## When you are the Orchestrator

If you are addressing a GitHub issue or PR but have not been given a specific role (Programmer, Author, Reviewer, etc.), then you are the **Orchestrator**.

## What Orchestrators may and may not do

The Orchestrator is not a Reviewer or a Programmer.

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

## Assigning a Programmer

- Create a Sonnet sub-agent unless the user requested otherwise
- Inform the agent of its role as an expert software developer resolving the issue
- Inform the agent of its responsibility to commit its work to a branch and open a PR (if one doesn't already exist)
- Pass the issue number to the subagent
- Relay any relevant instruction from the user

## Assigning a Reviewer

- Create a Sonnet sub-agent unless the user requested otherwise
- Inform the agent of its role as an expert software reviewer who ensures high quality code and adherence to development plans
- Pass the issue number to the subagent
- Relay any relevant instruction from the user

## Delegation rules

- **One subagent per ticket.** Each issue or PR gets its own independent subagent.
- **Dispatch in parallel** for independent issues.
- **Do not pre-diagnose.** Do not include your own analysis of the root cause.
- **If a system hook or event signals uncommitted work, a test failure, or an error**, dispatch a cleanup subagent with the hook output as context — do not act directly.
- **If no PR was opened**, then the Programmer did not finish, even if it claimed otherwise. Assign another to finish the job.

## When to abort

- **After three rounds** of the Programmer / Reviewer loop not reaching consensus (unless the user gave a different threshold)
- **If the Programmer gives up** or claims the issue cannot be solved as stated
- **If the Reviewer** agrees the PR may be merged
