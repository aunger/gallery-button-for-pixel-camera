# orchestrate plugin

A self-contained Claude Code **plugin** that makes the GB4PC
development-orchestration workflow invokable as the `/orchestrate` slash command.
It packages everything the workflow needs: the entry skill, four agent
definitions, the orchestration rules, the CI Watcher poller, the dispatch timer,
the just-in-time hooks, and their tests.

It resolves issue #164.

## Why a plugin (not a bare skill)

Claude Code skills cannot define agents; plugins can. This workflow needs
discrete `orchestrator`, `author`, `reviewer`, and `ci-watcher` agents, so it is
packaged as a plugin (`.claude-plugin/plugin.json` plus `agents/`, `skills/`,
`hooks/`, `scripts/`, and `rules/` at the plugin root).

Because installed plugins cannot reach files outside their own directory, every
rule, script, and template the workflow depends on lives inside the plugin. The
plugin references its own files via `${CLAUDE_PLUGIN_ROOT}`, so it works the same
in this repo and after installation from a marketplace.

## How it loads here

This plugin lives at `.claude/skills/orchestrate/` and carries its own
`.claude-plugin/plugin.json`, so Claude Code loads it in place as
`orchestrate@skills-dir` (a skills-directory plugin, no marketplace install
required). Launch Claude Code from the repository root, then run `/orchestrate`.

## What it is

`SKILL.md` is the entry point. It puts the model in the Orchestrator role and
walks the workflow in phases, loading one resource per phase so the context
stays uncluttered (progressive revelation). It dispatches the bundled `author`,
`reviewer`, and `ci-watcher` agents rather than hand-written prompts.

## Layout

```
.claude-plugin/plugin.json  Plugin manifest (name, agents, hooks, skill).
SKILL.md                    /orchestrate entry point and phase walkthrough.
agents/                     Discrete agent definitions (frontmatter + system prompt).
  orchestrator.md
  author.md
  reviewer.md
  ci-watcher.md
rules/                      The binding workflow and conduct rules, reorganized by audience.
  orchestration.md          Orchestrator role, dispatch, conditional approval, delegation, abort.
  ci_monitor.md             CI Monitor loop, poller interface, outcome vocabulary (split from orchestration).
  inaugurate.md             Fresh-start protocol for an unworked issue.
  review_cycle.md           Reviewer and Author conduct during the review cycle.
  authoring.md              Author rules: code edits, commit hygiene, tests, and PR creation.
  task_complexity.md        Model-tier rubric (rough draft; not yet in use).
resources/                  Progressive-revelation prose, one per phase.
  intake.md
  dispatch-author.md
  dispatch-reviewer.md
  ci-watch.md
  convergence.md
  model-selection.md
templates/                  Closed-parameter briefings to reduce over-sharing.
  author-brief.md
  reviewer-brief.md
hooks/                      Behavior enforcement and just-in-time information.
  hooks.json                Wires orchestrate-guard.sh as a PreToolUse hook.
  orchestrate-guard.sh      Reminds the Orchestrator not to edit/commit/read-source itself.
  surface-phase.sh          Prints the resource to read for a given phase.
  README.md
scripts/                    Plugin tooling.
  ci_monitor.py             The CI Watcher poll loop.
  ci_monitor.sh             Superseded bash CI Watcher, kept for reference.
  dispatch_timer.py         Formats sub-agent dispatch timing.
tests/                      Unit tests for the scripts and hooks.
  test_ci_monitor.py
  test_dispatch_timer.py
  test_orchestrate_guard.sh
  test_surface_phase.sh
```

## Design notes

### Rules are organized by audience, not by their old `/agents` filenames

The six documents that used to live in the repo's `/agents` folder were
reorganized for the plugin rather than dumped in verbatim. `code_edit.md` and
`pr_creation.md`, which both addressed the Author, were combined into
`authoring.md`. `pr_participation.md` was renamed `review_cycle.md` to name what
it governs. `dev_orchestration.md` became `orchestration.md`, and its
self-contained CI Monitor section (the loop, the poller's flag interface, and
the line-by-line outcome vocabulary) was split into `ci_monitor.md` because that
is the reference the `ci-watcher` agent and the Phase 4 resource navigate to
directly. The result is one file per audience or concern, so an agent reads only
the document its role needs.

### The CI Watcher lives in the plugin

The CI Watcher poll loop (`scripts/ci_monitor.py`) and its full test suite
(`tests/test_ci_monitor.py`) are part of the plugin, not the repo root. The
`ci-watcher` agent runs it via `${CLAUDE_PLUGIN_ROOT}/scripts/ci_monitor.py`.

### Agent definitions are real definitions

`agents/*.md` are first-class plugin agent definitions: frontmatter (`name`,
`description`, `model`, and tool restrictions where relevant) plus a system
prompt that points at the plugin's own `rules/`. They are not pointers into an
external folder.

### Communication discipline via templates

`templates/author-brief.md` and `templates/reviewer-brief.md` are closed
parameter lists. The Orchestrator fills every field and adds nothing else, so it
cannot inject its own diagnosis into a sub-agent and bias it.

### Hooks are plugin-scoped

`hooks/hooks.json` wires `orchestrate-guard.sh` as a `PreToolUse` hook. Plugin
hooks fire only while the plugin is enabled, so the guard acts during
orchestration and stays out of the way during ordinary edits in repositories
where the plugin is absent.
