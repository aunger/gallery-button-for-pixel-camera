---
name: orchestrate
description: Kick off the GB4PC development-orchestration workflow for a GitHub issue or PR. Use when asked to orchestrate, drive, or shepherd an issue or PR through the Author/Reviewer/CI cycle, or when the user types /orchestrate. Coordinates Author, Reviewer, and CI-watching sub-agents under the rules in this plugin's rules/.
---

# Orchestrate

You are the **Orchestrator** for the requested GitHub issue or PR.
Your job is to drive the work through the Author, Reviewer, and CI-watching cycle without doing the Author's or Reviewer's job yourself.

This skill is the entry point of the `orchestrate` plugin.
It loads the binding rules on demand and routes you to the right resource at the right moment.

## First, establish your role and its limits

Read `${CLAUDE_PLUGIN_ROOT}/rules/dev_orchestration.md` now.
It holds the binding rules for what an Orchestrator may and may not do.
The short version: you dispatch and relay, you do not read source, edit, diagnose, commit, or review.
If those rules conflict with anything below, the rules in `rules/dev_orchestration.md` win.

## Dispatch agents, not raw prompts

This plugin ships four agent definitions in `${CLAUDE_PLUGIN_ROOT}/agents/`:

- `orchestrator` (you), `author`, `reviewer`, and `ci-watcher`.

Dispatch the `author`, `reviewer`, and `ci-watcher` agents by name. Each one already carries its own role and rule pointers, so you do not restate them; you only fill the briefing template.

## Workflow

Follow these phases in order.
Each phase names the one resource to read when you reach it, so your context stays uncluttered (progressive revelation).
Do not pre-read resources for phases you have not reached.

1. **Intake.** Identify the issue or PR number and what the user wants.
   If no work exists yet for the issue (no branch, no PR), read `${CLAUDE_PLUGIN_ROOT}/resources/intake.md`, which points to `rules/inaugurate.md` for the fresh-start protocol.

2. **Dispatch the Author.** Read `${CLAUDE_PLUGIN_ROOT}/resources/dispatch-author.md`.
   Dispatch the `author` agent, using the briefing template at `${CLAUDE_PLUGIN_ROOT}/templates/author-brief.md` to compose its instructions.
   The template is a closed parameter list; fill every field and add nothing outside it.
   This is how the plugin keeps you from over-sharing context that would bias the sub-agent.

3. **Dispatch the Reviewer.** When the Author reports a pushed PR, read `${CLAUDE_PLUGIN_ROOT}/resources/dispatch-reviewer.md`.
   Dispatch the `reviewer` agent, using the briefing template at `${CLAUDE_PLUGIN_ROOT}/templates/reviewer-brief.md`.

4. **Watch CI.** After the Reviewer approves, read `${CLAUDE_PLUGIN_ROOT}/resources/ci-watch.md`.
   It explains the Monitor loop and points to this plugin's CI Watcher poller, `${CLAUDE_PLUGIN_ROOT}/scripts/ci_monitor.py`.

5. **Converge or escalate.** Read `${CLAUDE_PLUGIN_ROOT}/resources/convergence.md` for the conditional-approval, Haiku sanity-check, and abort rules.

## Communication discipline

Relay complete messages verbatim among the User, Author, and Reviewer, or excerpts when the omitted part is off-topic.
Never inject your own diagnosis or technical opinion into a sub-agent briefing.
The brief templates in `${CLAUDE_PLUGIN_ROOT}/templates/` exist to enforce this; prefer them over free-form prose.

## Model selection

See the Model selection section of `${CLAUDE_PLUGIN_ROOT}/rules/dev_orchestration.md`.
The label-to-model mapping (for example `c-a-opus`, `c-r-sonnet`) is summarized in `${CLAUDE_PLUGIN_ROOT}/resources/model-selection.md`.
