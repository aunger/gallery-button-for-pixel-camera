# The dispatch form

The dispatch form is the only text the Orchestrator sends a sub-agent, whether in the `prompt` of an Agent call or in a `SendMessage` that resumes an agent for another round.
It is five lines, in this order, each `Field: value`, with nothing before, between, or after them:

```text
Repository: {owner}/{repo}
Issue: #{number} or None
PR: #{number} or None
Branch: {branch name} or None
Verification plan comment: {numeric comment id} or None
```

A filled example:

```text
Repository: aunger/gallery-button-for-pixel-camera
Issue: #164
PR: #1390
Branch: feature/issue-164-orchestrate-plugin
Verification plan comment: None
```

## Why a form

Everything a sub-agent needs beyond these five facts lives on GitHub, where it reads it fresh: the issue, the PR's description and its three comment surfaces, the review bodies, the tracking issues.
A dispatch that carried more would carry the Orchestrator's reading of those things, and the sub-agent would start from that reading instead of its own.
The form makes over-sharing impossible rather than merely forbidden.

## Rules

- The Orchestrator fills the fields from facts it is permitted to know: the user's issue number, the branch it chose, the PR number an Author reported, the comment id a Planner reported.
- `Issue` is `None` only when the work item is a PR with no linked issue.
- `PR` is `None` on the no-PR path (the Author declined to open one) and before the first Author round.
- `Branch` is `None` for a Reviewer, Planner, or Verifier, which do not commit; it names the per-issue branch for a Programmer.
- `Verification plan comment` is `None` except when routing a Verifier, or an Author round that follows a Verifier's `Verification revealed an error`.
- A role statement is not part of the form: each agent's definition says what it is.
- Resuming an agent sends the same form again. The agent treats each receipt as "start a new round, re-fetch everything".

## Enforcement

`hooks/hooks.json` runs `scripts/hooks/check_dispatch.py` before every Agent call.
When `subagent_type` names one of this plugin's agents, the hook refuses a `prompt` that is not exactly the form, and says why.
While `/orchestrate` is active, `scripts/hooks/orchestrator_guard.py` applies the same check to every `SendMessage` the main session sends.
The agents receive nothing else, so nothing else needs to be forbidden.
A person can switch both hooks off for a session with `/orch_hooks disengage`; see `README.md`.
