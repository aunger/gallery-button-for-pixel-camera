# /orchestrate skill

A Claude Code skill that makes the GB4PC development-orchestration workflow
invokable as the `/orchestrate` slash command. It implements the decision in
issue #150 and the scope in issue #164.

## What it is

`SKILL.md` is the entry point. It puts the model in the Orchestrator role and
walks it through the workflow in phases, loading one resource per phase so the
context stays uncluttered (progressive revelation).

The binding rules still live in the repo's `agents/` markdown files. Per the
decision in #150, that prose is kept as-is and is not duplicated here. The skill
references it; it does not replace it.

## Layout

```
SKILL.md                    Entry point and phase walkthrough.
agents/                     Thin agent definitions (frontmatter + pointer to agents/ prose).
  orchestrator.md
  author.md
  reviewer.md
  ci-watcher.md
resources/                  Progressive-revelation, one per phase.
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
  orchestrate-guard.sh      Reminds the Orchestrator not to edit/commit itself.
  surface-phase.sh          Prints the resource to read for a given phase.
  README.md
scripts/                    Skill tooling.
  ci_watch.sh               Forwards to the canonical repo-root ci_monitor.py.
  dispatch_timer.py         Formats sub-agent dispatch timing.
tests/                      Unit tests for the scripts and hooks.
```

## Design notes

### The CI poller is not duplicated into the skill

The canonical CI poll loop stays at `scripts/ci_monitor.py` in the repo root.
Both the standalone `agents/` workflow and this skill share that one tested
implementation. The issue asked to move agent tools into the skill, but moving
the poller would have (a) broken the `python3 scripts/ci_monitor.py` instruction
in `agents/dev_orchestration.md`, which #150 said to keep as-is, and (b) split
the heavily tested `test_ci_monitor.py` away from its subject. Instead the skill
provides a thin `scripts/ci_watch.sh` wrapper that forwards to the canonical
poller, avoiding duplicated logic.

### Agent definitions are thin pointers

`agents/*.md` here carry the frontmatter that makes each role a discrete,
addressable agent definition, then point at the canonical prose in the repo's
`agents/` folder. This gives #164 its "discrete agent definition files" without
rewriting the prose that #150 said to keep.

### Communication discipline via templates

`templates/author-brief.md` and `templates/reviewer-brief.md` are closed
parameter lists. The Orchestrator fills every field and adds nothing else, so it
cannot inject its own diagnosis into a sub-agent and bias it.
