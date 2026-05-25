# Test result tracking: research notes

*Produced for #206 on 2026-05-22. The actionable output is #207 (epic) and #208 (Phase 4 implementation plan).*

---

## Problem statement

After a CI run with test failures, a human (or AI agent) must navigate to the GitHub Actions Summary tab, read the Markdown table produced by `scripts/summarize_test_results.py`, and manually triage each failure. There is no structured, per-test artifact that an AI agent can autonomously claim and act on.

The goal is to create enough machine-readable signal per failure that an agent can:
1. Find the failure (it is a GitHub issue, searchable and assignable)
2. Understand it (failure message, stack trace, screenshot, OCR text)
3. Act on it (attempt a fix, open a PR, close the issue)

---

## Alternatives survey

### 1. GitHub Marketplace: `Failed Build Issue` action

- **What it does:** On workflow failure, creates or appends to a single "build failed" issue (label `build failed`). Appends by default; can create a new issue every run with `always-create-new-issue: true`.
- **Dedup:** Finds the latest open issue with label `build failed` and appends; does not deduplicate per test case.
- **Permissions needed:** `issues: write` (read/write on workflows in settings).
- **Cost:** Free, open source, third-party.
- **Verdict:** Too coarse — one issue per build failure, not per test case. Does not surface individual test names. Not suitable for agent dispatch.
- **Source:** <https://github.com/marketplace/actions/failed-build-issue>

### 2. `dorny/test-reporter`, `EnricoMi/publish-unit-test-result-action`

- **What they do:** Parse JUnit XML and post PR Check Run annotations with per-test pass/fail.
- **Permissions needed:** `checks: write` — silently fails on fork PRs due to GitHub token restrictions.
- **Cost:** Free, open source.
- **Verdict:** Already evaluated and rejected during #195. Great for human review of PR diffs; not suitable for agent-actionable issues. Fork-PR limitation is a practical blocker.
- **Sources:** <https://github.com/dorny/test-reporter>, <https://github.com/EnricoMi/publish-unit-test-result-action>

### 3. Trunk Flaky Tests

- **What it does:** SaaS flakiness detection — uploads JUnit XML after each run, builds a flakiness history, auto-quarantines flaky tests, surfaces PR comments, integrates with ticketing.
- **Free tier:** Free for open-source projects and teams up to 5 committers; per-seat for larger teams.
- **Permissions needed:** API token for uploads; no special GitHub token scopes.
- **Verdict:** Solves the flakiness-detection problem well. For this project the "detect" part is already known (two E2E tests are flagged `continue-on-error`). Trunk's value is in the quarantine and trend dashboards, which are overkill for 2 tests. Adds external SaaS dependency.
- **Source:** <https://trunk.io/flaky-tests>

### 4. BuildPulse

- **What it does:** Similar to Trunk — SaaS flakiness dashboard with GitHub Actions integration. Uploads JUnit XML; tracks test pass rate over time.
- **Free tier:** Exists (Jenkins plugin free tier confirmed); small-team pricing not publicly listed.
- **Verdict:** Same verdict as Trunk — good product, overkill for this scale.
- **Source:** <https://buildpulse.io/>, <https://github.com/buildpulse/buildpulse-action>

### 5. Datadog CI Visibility

- **What it does:** Full pipeline observability — test run dashboards, flakiness detection, trace-level test analysis.
- **Pricing:** Test Optimization $20/committer/month (committers with 3+ commits/month).
- **Verdict:** Definitively overkill. $20/month for a one-person hobby project is not justified.
- **Sources:** <https://docs.datadoghq.com/continuous_integration/>, <https://docs.datadoghq.com/account_management/billing/ci_visibility/>

### 6. Allure TestOps / ReportPortal

- **What they do:** Self-hosted open-source test management platforms. ReportPortal has ML-based failure grouping and auto-analysis. Allure TestOps has a configurable dashboard and test lifecycle management.
- **Cost:** Free as open source (self-hosted); ReportPortal SaaS starts at ~$599/month.
- **Infrastructure requirement:** Running a separate service (Docker/k8s), ongoing maintenance.
- **Verdict:** Architecturally interesting; not appropriate for a project with no dedicated infrastructure. The self-hosting overhead would dwarf the value for this scale.
- **Sources:** <https://reportportal.io/>, <https://allurereport.org/>

### 7. GitHub Agentic Workflows (technical preview, Feb 2026)

- **What it does:** Markdown-authored workflow files that run AI agents inside GitHub Actions. Can trigger on CI failures, create issues, open PRs. Designed for "individuals automating a single repo to enterprise scale."
- **Cost:** Copilot premium requests per run (approximately 2 per workflow execution).
- **Status:** Technical preview as of Feb 2026.
- **Verdict:** Architecturally aligned with where this project is heading. Not the right implementation vehicle today (preview, per-run cost, `gh-aw` extension dependency), but worth revisiting once stable.
- **Sources:** <https://github.blog/ai-and-ml/automate-repository-tasks-with-github-agentic-workflows/>, <https://github.github.com/gh-aw/>

### 8. Custom Python script + GitHub REST API / `gh` CLI

- **What it does:** A new `scripts/file_test_failure_issues.py` following the pattern of `summarize_test_results.py`. Parses the same JUnit XML files, calls `gh issue create` (or the REST API via `GITHUB_TOKEN`) for each failed test case.
- **Permissions needed:** `issues: write` scoped to a separate `workflow_run`-triggered workflow; the `build-and-test` job keeps its read-only token.
- **Cost:** Zero. No external service.
- **Dedup:** Search existing open issues by label + test name before creating; append a comment if a match is found within a configurable window.
- **Flakiness suppression:** `--skip-flaky` flag takes a list of known-flaky class names.
- **Verdict:** Best fit. Consistent with existing codebase patterns. Full control. Zero new dependencies or costs. See #208 for the implementation plan.

---

## Security: `issues: write` — two-workflow architecture (decided)

**Decision:** `issues: write` is scoped to a dedicated second workflow (`.github/workflows/file-test-failure-issues.yml`), not added to the `build-and-test` job. The `build-and-test` job's permissions are unchanged and remain read-only.

### Why a separate workflow?

The `build-and-test` job runs third-party Gradle plugins, code from PR contributors, and other less-trusted inputs. Keeping it on a read-only token limits the blast radius of a supply-chain compromise or malicious PR. A separate `workflow_run`-triggered workflow runs exclusively in the base-repo context with a real token, performing issue writes only after the build has already completed and exited.

This is GitHub's recommended security pattern for privileged write operations in CI — see:
- [GitHub Docs: `workflow_run` event](https://docs.github.com/en/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows#workflow_run)
- [GitHub Security Lab: Securely handling fork PRs (`workflow_run` for privileged operations)](https://securitylab.github.com/resources/github-actions-preventing-pwn-requests/)

### Permissions layout

```yaml
# .github/workflows/file-test-failure-issues.yml (new)
permissions:
  issues: write
  contents: read

# .github/workflows/build.yml (unchanged)
# permissions: contents: read  ← no issues: write added here
```

### Bonus: fork PRs covered

Because the `workflow_run` workflow always runs in the base-repo context, failures from fork-PR contributions also result in filed issues. An inline step in `build-and-test` would receive a restricted read-only token for fork PRs and could not call the Issues API.

### Development ergonomics caveat

A `workflow_run` workflow **always executes the version on the default branch (`main`)**, not the triggering PR's branch. This is a deliberate GitHub security feature: a PR cannot alter the privileged filing workflow to capture the token before merging. Practical consequence: `file-test-failure-issues.yml` and `scripts/file_test_failure_issues.py` must be merged to `main` to take effect — edits on a feature branch will not run, even if the build on that branch fails.

### Tradeoff

Two workflows + artifact upload/download plumbing + the default-branch constraint is more complex than a single inline step. The owner has chosen this approach to keep `issues: write` out of the build job entirely. See #208 for the full design.

**Sources:** <https://docs.github.com/en/actions/security-guides/automatic-token-authentication>, <https://docs.github.com/en/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows#workflow_run>, <https://securitylab.github.com/resources/github-actions-preventing-pwn-requests/>

---

## Is auto-ticketing overkill for a hobbyist project?

### The case against

- For a *conventional* solo dev, CI failures are visible in the Actions tab. You know it's red; you go fix it. Auto-filing issues adds noise to a tracker you already monitor.
- If the E2E tests are flaky (and two of them are, per #178, #179, #186), you get a new issue every run — classic alert fatigue.

### The case for (this specific project)

- **AI agents are the primary implementers.** An agent cannot autonomously claim and fix a failure unless it exists as a structured issue. The `$GITHUB_STEP_SUMMARY` table is not agent-accessible without extra tooling.
- **The owner has low availability.** If CI goes red and no one is actively watching, a filed issue is the durable signal that persists until it is fixed. A job summary disappears from view as new runs accumulate.
- **The existing workflow structure is already agent-oriented.** `AGENTS.md`, `agents/dev_orchestration.md`, and the issue-driven workflow all point to issues as the primary unit of work. Closing that loop — CI failure → issue → agent fix → PR — is the natural completion of this architecture.

### Verdict

Auto-ticketing is not overkill here — it is the missing link that makes the agentic workflow self-sustaining. The flakiness noise problem is real but manageable with the `--skip-flaky` suppression list.

---

## Recommended phase sequence

| Phase | Issue | Prerequisite | Value without prior phases |
|-------|-------|-------------|---------------------------|
| 1: Screenshot attribution | #201 | None | High — makes screenshots usable for humans immediately |
| 2: OCR on failure screenshots | #198 | None (but better with #201) | Medium — useful text extraction even with bulk-named files |
| 3: AI image analysis | #199 | None (exploratory) | Uncertain — Tesseract likely sufficient for this UI |
| 4: Auto-file issues per failed test | #208 | None (better with #201 + #198) | High even alone — creates agent-actionable artifacts immediately |

Phases 1 and 4 deliver the most value and can be worked in parallel. Phase 2 amplifies Phase 4. Phase 3 is optional and can wait.
