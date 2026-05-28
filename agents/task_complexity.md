# Task complexity rubric

> **Status: rough draft — not yet in use.**
> Do not apply it until this notice is replaced.

## Purpose

Route tasks to the right model tier. Score the six dimensions below, sum them, and map to a tier. Apply separately for the Author (implementation) and the Reviewer (code review), using the adjustments in the Reviewer section.

## Dimensions

| Dimension | 1 — Low | 2 — Medium | 3 — High |
|-----------|---------|------------|----------|
| **Task type** | Boilerplate, formatting, simple config edit | Standard feature, bug fix, refactor | Architecture, novel algorithm, system design |
| **Ambiguity** | Fully specified, clear acceptance criteria | Some interpretation needed | Underspecified, requires judgment or design |
| **Context breadth** | Single file / self-contained | A few related files | Cross-cutting; large-codebase awareness needed |
| **Domain depth** | Generic code or prose | Moderate domain knowledge | Specialized (GitHub Actions internals, Android build system, security, etc.) |
| **Reasoning chain** | Single step, pattern match | Multi-step, some tradeoffs | Long chain, competing concerns, novel reasoning |
| **Investigation** | Approach clear from the issue; at most a standard docs lookup | Some unknowns, resolvable via code inspection or documentation | Requires empirical investigation — undocumented API behavior, platform quirks that must be tested to understand |

## Scoring → tier

| Total | Tier | Model |
|-------|------|-------|
| 6–8 | 1 | Haiku |
| 9–13 | 2 | Sonnet |
| 14–18 | 3 | Opus |

## Author leveling

Score **context breadth** on what a thorough resolution requires, not just the files named in the issue.

## Reviewer leveling

Same dimensions and tier ranges, with:

- **Investigation** ≤ 2
- **Context breadth** ≤ Author's score

**Floor**: Reviewer tier ≥ Author tier − 1

## GitHub labels

Author complexity:
- `c-a-haiku` Haiku
- `c-a-sonnet` Sonnet
- `c-a-opus` Opus

Reviewer complexity:
- `c-r-haiku` Haiku
- `c-r-sonnet` Sonnet
- `c-r-opus` Opus

## General notes

- Build system and CI configuration files (Makefiles, Dockerfiles, workflow YAMLs, Gradle scripts, etc.) should score **domain depth ≥ 2** — these systems have undocumented interactions and hidden complexity that is easy to underestimate.
- Tasks limited to editing plain text or documents (policies, instructions, docs) with exact content provided typically score **6** (all 1s) — Haiku territory.

## Calibration examples

Scores are listed as: task type / ambiguity / context / domain / reasoning / investigation.

### Tier 1 — Haiku (6–8)

**#254 / PR #269 — Add reviewer rule: don't mention bylines** (Author: 6, Reviewer: 6)
Single `agents/` file, exact wording provided, no reasoning required.
1 / 1 / 1 / 1 / 1 / 1

**#250 / PR #251 — Make `requests` import hard** (Author: 6, Reviewer: 6)
Remove a soft-import guard in one Python file; change fully specified.
1 / 1 / 1 / 1 / 1 / 1

**#246 / PR #247 — Re-open closed issues when CI detects recurrence** (Author: 8, Reviewer: 7)
Two-step Python change (search API + reopen call) but fully specified and self-contained.
Author: 2 / 1 / 1 / 1 / 2 / 1 — Reviewer: 2 / 1 / 1 / 1 / 1 / 1 (investigation drops; reasoning is lighter once the code is written)

### Tier 2 — Sonnet (9–13)

**#237 / PR #238 — Auto-filed issue format fixes** (Author: 9, Reviewer: 8 → Haiku)
Four sub-tasks across a Python script and a workflow YAML, but each is well-specified. Reviewer applies floor: Haiku is Sonnet − 1, so Haiku is the minimum.
Author: 2 / 1 / 2 / 1 / 2 / 1 — Reviewer: 2 / 1 / 2 / 1 / 1 / 1

**#257 / PR #262 — Emit per-test markers in Gradle** (Author: 10, Reviewer: 10)
New Gradle test listener + E2E marker emission; requires Kotlin DSL knowledge. Reviewer needs equal domain depth to evaluate correctness of the listener hook and JSON escaping.
Author: 2 / 1 / 2 / 2 / 2 / 1 — Reviewer: 2 / 1 / 2 / 2 / 2 / 1

**#225 / PR #226 — Issue filer workflow not triggered** (Author: 11, Reviewer: 10)
Root cause diagnosis required; involves `workflow_run` trigger semantics and permission model.
Author: 2 / 2 / 2 / 2 / 2 / 1 — Reviewer: 2 / 1 / 2 / 2 / 2 / 1 (ambiguity drops once implementation is written)

### Tier 3 — Opus (14–18)

**#258 / PR #271 — Stream per-test CI signals + extract monitor script** (Author: 17, Reviewer: 14)
Two new parser systems, shell test infrastructure, cross-cutting changes; required empirical testing of partial-log API availability. Investigation drops from 3 to 2 for review; reasoning chain also lighter.
Author: 3 / 2 / 3 / 3 / 3 / 3 — Reviewer: 3 / 2 / 3 / 3 / 2 / 2

**#236 — CI monitor should surface failing tests (design)** (Author: 18, Reviewer: 16)
Full system design with competing approaches; undocumented API behavior; 5-file implementation plan. Investigation and ambiguity both drop for review.
Author: 3 / 3 / 3 / 3 / 3 / 3 — Reviewer: 3 / 2 / 3 / 3 / 3 / 2
