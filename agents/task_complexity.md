# Task complexity rubric

> **Status: rough draft — not yet in use.**
> This rubric is under review and has not been wired into the orchestration workflow.
> Do not apply it until it replaces this notice.

## Purpose

Route tasks to the right model tier by scoring five dimensions of task complexity.
Score each dimension 1–3, sum them, and map the total to a tier.

## Dimensions

| Dimension | 1 — Low | 2 — Medium | 3 — High |
|-----------|---------|------------|----------|
| **Task type** | Boilerplate, formatting, simple config edit | Standard feature, bug fix, refactor | Architecture, novel algorithm, system design |
| **Ambiguity** | Fully specified, clear acceptance criteria | Some interpretation needed | Underspecified, requires judgment or design |
| **Context breadth** | Single file / self-contained | A few related files | Cross-cutting; large-codebase awareness needed |
| **Domain depth** | Generic code or prose | Moderate domain knowledge | Specialized (GitHub Actions internals, Android build system, security, etc.) |
| **Reasoning chain** | Single step, pattern match | Multi-step, some tradeoffs | Long chain, competing concerns, novel reasoning |

## Scoring → tier

| Total | Tier | Model |
|-------|------|-------|
| 5–7 | 1 | Haiku |
| 8–11 | 2 | Sonnet |
| 12–15 | 3 | Opus |

**Modifier:** if the issue requires empirical investigation to determine the approach (e.g. undocumented API behavior, unknown platform semantics), add **+2** to the total before mapping to a tier.

## Project-specific notes

- Any issue that touches `.github/workflows/build.yml` or `app/build.gradle.kts` should score **domain depth ≥ 2**, because GitHub Actions quirks (trigger types, `continue-on-error` propagation, artifact availability timing) and the Gradle/Android build system routinely introduce hidden complexity.
- Issues limited to editing files under `agents/` with exact wording provided are typically score **5** (all 1s) — Haiku territory.

## Calibration examples

These examples were scored against actual closed issues and PRs in this repo.

### Tier 1 — Haiku (5–7)

**#254 / PR #269 — Add reviewer rule: don't mention bylines** (score: 5)
Single `agents/` file, exact wording provided, no reasoning required.
1 / 1 / 1 / 1 / 1

**#250 / PR #251 — Make `requests` import hard** (score: 5)
Remove a soft-import guard in one Python file; change fully specified.
1 / 1 / 1 / 1 / 1

**#246 / PR #247 — Re-open closed issues when CI detects recurrence** (score: 7)
Two-step Python change (search API + reopen call) but fully specified and self-contained.
2 / 1 / 1 / 1 / 2

### Tier 2 — Sonnet (8–11)

**#237 / PR #238 — Auto-filed issue format fixes** (score: 8)
Four sub-tasks across a Python script and a workflow YAML, but each is well-specified.
2 / 1 / 2 / 1 / 2

**#257 / PR #262 — Emit per-test markers in Gradle** (score: 9)
New Gradle test listener + E2E marker emission; requires Gradle Kotlin DSL knowledge.
2 / 1 / 2 / 2 / 2

**#225 / PR #226 — Issue filer workflow not triggered** (score: 10)
Root cause diagnosis required; involves `workflow_run` trigger semantics and permission model.
2 / 2 / 2 / 2 / 2

**#264 / PR #266 — Upload per-step artifact plumbing** (score: 10)
Multi-step workflow change; premise correction needed after inspecting actual CI structure.
2 / 2 / 2 / 2 / 2

### Tier 3 — Opus (12–15)

**#258 / PR #271 — Stream per-test CI signals + extract monitor script** (score: 14)
Two new parser systems, shell test infrastructure, cross-cutting changes; empirical API investigation required (+2).
3 / 2 / 3 / 3 / 3

**#236 — CI monitor should surface failing tests (design)** (score: 15)
Full system design with competing approaches; undocumented API behavior; 5-file implementation plan.
3 / 3 / 3 / 3 / 3
