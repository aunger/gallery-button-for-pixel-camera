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

**Floor**: Reviewer tier ≥ impl tier − 1

## GitHub labels

- `c-haiku` Haiku
- `c-sonnet` Sonnet
- `c-opus` Opus

## Project-specific notes

- Any issue that touches `.github/workflows/build.yml` or `app/build.gradle.kts` should score **domain depth ≥ 2**, because GitHub Actions quirks (trigger types, `continue-on-error` propagation, artifact availability timing) and the Gradle/Android build system routinely introduce hidden complexity.
- Issues limited to editing files under `agents/` with exact wording provided typically score **6** (all 1s) — Haiku territory.

## Calibration examples

Scores are listed as: task type / ambiguity / context / domain / reasoning / investigation.

### Tier 1 — Haiku (6–8)

**#254 / PR #269 — Add reviewer rule: don't mention bylines** (impl: 6, review: 6)
Single `agents/` file, exact wording provided, no reasoning required.
1 / 1 / 1 / 1 / 1 / 1

**#250 / PR #251 — Make `requests` import hard** (impl: 6, review: 6)
Remove a soft-import guard in one Python file; change fully specified.
1 / 1 / 1 / 1 / 1 / 1

**#246 / PR #247 — Re-open closed issues when CI detects recurrence** (impl: 8, review: 7)
Two-step Python change (search API + reopen call) but fully specified and self-contained.
Impl: 2 / 1 / 1 / 1 / 2 / 1 — Review: 2 / 1 / 1 / 1 / 1 / 1

### Tier 2 — Sonnet (9–13)

**#237 / PR #238 — Auto-filed issue format fixes** (impl: 9, review: 8 → Haiku)
Four sub-tasks across a Python script and a workflow YAML, but each is well-specified.
Impl: 2 / 1 / 2 / 1 / 2 / 1 — Review: 2 / 1 / 2 / 1 / 1 / 1

**#257 / PR #262 — Emit per-test markers in Gradle** (impl: 10, review: 10)
New Gradle test listener + E2E marker emission; requires Kotlin DSL knowledge.
Impl: 2 / 1 / 2 / 2 / 2 / 1 — Review: 2 / 1 / 2 / 2 / 2 / 1

**#225 / PR #226 — Issue filer workflow not triggered** (impl: 11, review: 10)
Root cause diagnosis required; involves `workflow_run` trigger semantics and permission model.
Impl: 2 / 2 / 2 / 2 / 2 / 1 — Review: 2 / 1 / 2 / 2 / 2 / 1

### Tier 3 — Opus (14–18)

**#258 / PR #271 — Stream per-test CI signals + extract monitor script** (impl: 17, review: 14)
Two new parser systems, shell test infrastructure, cross-cutting changes; required empirical testing of partial-log API availability.
Impl: 3 / 2 / 3 / 3 / 3 / 3 — Review: 3 / 2 / 3 / 3 / 2 / 2

**#236 — CI monitor should surface failing tests (design)** (impl: 18, review: 16)
Full system design with competing approaches; undocumented API behavior; 5-file implementation plan.
Impl: 3 / 3 / 3 / 3 / 3 / 3 — Review: 3 / 2 / 3 / 3 / 3 / 2
