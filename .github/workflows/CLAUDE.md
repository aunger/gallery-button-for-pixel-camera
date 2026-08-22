# CI Architecture

## Failing CI with an explicit red-to-green allowlist

The CI pipeline fails the build when a test fails.
The only exception is a small, explicit allowlist of tests that are permitted to stay red during a deliberate red-to-green effort.

This replaced the previous `continue-on-error: true` design (issue #309).
`continue-on-error` swallowed *every* error, including non-test breakage (for example the broken `date` invocation in issue #307), so the build went green while genuine failures hid.

### Why an allowlist instead of `continue-on-error`?

During periods of active test stabilization we still want to let a few known-red tests stay red without blocking unrelated work.
The allowlist does that narrowly:

- A genuine, unexpected test failure fails the build, giving real signal.
- Infrastructure or tooling breakage that aborts a test step (no test result written) fails the build.
- Only the specific tests named in the allowlist are tolerated, and only temporarily.
- Failed tests are still filed as GitHub issues, and the pipeline still produces artifacts (APKs, screenshots, logcat) for debugging.

### How it works

1. **Unit tests and compile steps** run normally and fail the build on error (fast feedback loop).
2. **Instrumented and E2E test steps** do not use `continue-on-error`.
   Each records its real exit status as a step output and lets later steps run, so results, artifacts, and issue filing are never skipped.
3. **The `Gate on test failures` step** is the single authority on pass/fail.
   It runs `scripts/ci/test-support/check_allowed_failures.py` over the JUnit XML for each suite:
   - Any failing test fails the build, unless that test is named in `.github/allowed-test-failures.txt`.
   - Each test step's recorded outcome is passed via `--outcome`.
     A step that reported `failure` without writing any failing test result is treated as an infrastructure failure and still fails the build.
4. **Issue filing** (`file-issues` job) continues to file and re-open issues for failed tests.

### Allowing a test to fail temporarily

Add an entry to `.github/allowed-test-failures.txt`:

```text
com.gb4pc.e2e.GalleryButtonVisualE2ETest#someMethod   # tolerate one method
com.gb4pc.e2e.GalleryButtonVisualE2ETest              # tolerate the whole class
```

Blank lines and `#` comment lines are ignored.
A `#` glued to the preceding token is the class/method separator and is preserved; an inline comment must be preceded by whitespace.

Remove entries as the tests are fixed.
An empty allowlist means no failure is tolerated, which is the steady state.

### Related files

- `.github/workflows/build.yml`: the CI configuration, including the `Gate on test failures` step.
- `.github/allowed-test-failures.txt`: the red-to-green allowlist.
- `scripts/ci/test-support/check_allowed_failures.py`: the gate script (unit-tested in `scripts/ci/test-support/test_check_allowed_failures.py`).
- `scripts/ci/prs-and-issues/file_test_failure_issues.py`: auto-files issues for failed tests.
- `.github/workflows/archive-stale-test-failures.yml`: archives test failure issues when tests pass.

## A privileged pull-request job must not check out pull request code

A job that a `pull_request` event can trigger, and that holds any write scope other than `security-events`, must pin the `ref:` of every `actions/checkout` step:

```yaml
- uses: actions/checkout@v6
  with:
    ref: ${{ github.event.pull_request.base.sha || github.sha }}
```

The default checkout on a `pull_request` event is the pull request's own merge ref, so a job that then runs a script out of the checkout is executing pull-request-controlled code (issue #882).
Today that is inert: GitHub hands a fork's `pull_request` run a read-only `GITHUB_TOKEN` whatever the `permissions:` block asks for.
It stops being inert if "Send write tokens to workflows from fork pull requests" is ever enabled.
The jobs holding write scopes here act only through the API, on titles, labels, comment bodies and downloaded artifacts, so none of them needs pull request file contents.

`security-events: write` is the one exemption, because codeql.yml and semgrep.yml exist to analyze the pull request's code and must check out the merge ref to do it.
Every other write scope counts, including `contents: write`, which over pull-request-controlled code is a full compromise.

The `|| github.sha` half covers the events that carry no pull request context (`issues`, `issue_comment`, `push`, `workflow_dispatch`).
It has to come second: on a `pull_request` event `github.sha` is the merge ref itself.
Where the job only ever sees a pull request, whether because the workflow is triggered only by `pull_request` or because a job-level `if` gates it to that event, use `${{ github.event.pull_request.base.sha }}` alone rather than an unreachable fallback.

Do not reach for `pull_request_target` instead.
It grants a full write token and secrets in the base-repository context, so pairing it with a checkout of pull request code creates the vulnerability this rule exists to prevent.

### What the pinning does not achieve

This rule narrows the exposure.
It does not close it, and no one should enable that setting on the strength of it.

On a `pull_request` event the workflow file itself is read from the merge ref, not from the base branch.
A fork pull request would therefore never need to touch the script: it could edit the workflow YAML directly, adding a `run:` step or restoring the default `ref:`, and it could edit the guard so that it reports a clean tree.
What the pinning buys is that such a change has to appear in a workflow diff, where it gets read, instead of in a script buried under `scripts/ci/`.

The only real boundary available is the one `dependabot-verification-metadata-push.yml` relies on, stated in its own header: workflow files must live on the base branch to run at all, so a `workflow_run`-triggered job always executes the reviewed file on `main`.
No `pull_request`-triggered job has that property.

### The cost

A pull request that edits a script one of these pinned jobs runs no longer exercises its own edit.
The job checks out the base branch, so `main`'s copy of that script is what runs during the pull request's own CI, and the change only takes effect once it is merged.
Expect the new behavior not to show up on the pull request that introduces it, and do not read that as the change being broken.

This applies to every script a pinned job invokes, not to a fixed list: whatever the jobs named by `scripts/ci/test_privileged_workflow_checkouts.py` execute is subject to it.
Today that is the entry point of each of the five label and byline workflows plus `build.yml`'s issue-filing and CI-summary scripts.
It also reaches past the entry points, because they import each other.
`label_by_files.py` and `propagate_issue_labels.py` both `import enforce_mutually_exclusive_labels` and `import label_by_title`, so an edit to either of those changes the behavior of two more jobs and is equally invisible until merge.

Relatedly, a pull request that renames or moves one of these scripts *and* updates its workflow in the same change will fail that job: the workflow comes from the merge ref and names the new path, while the checkout supplies the base branch, where that path does not exist yet.
Land the rename separately, or accept one red job on it.

`scripts/ci/test_privileged_workflow_checkouts.py` enforces the rule over every workflow, and its docstring carries the full rationale and the guard's own limits (among them that it only inspects `actions/checkout` steps, so `gh pr checkout` or a bare `git fetch` would slip past).
