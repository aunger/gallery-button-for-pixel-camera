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

A job that a `pull_request` event can trigger, and that holds `issues: write` or `pull-requests: write`, must pin the `ref:` of every `actions/checkout` step:

```yaml
- uses: actions/checkout@v6
  with:
    ref: ${{ github.event.pull_request.base.sha || github.sha }}
```

The default checkout on a `pull_request` event is the pull request's own merge ref, so a job that then runs a script out of the checkout is executing pull-request-controlled code (issue #882).
Today that is inert: GitHub hands a fork's `pull_request` run a read-only `GITHUB_TOKEN` whatever the `permissions:` block asks for.
It stops being inert the moment "Send write tokens to workflows from fork pull requests" is enabled, and nothing in the tree would notice.
The jobs holding those two scopes act only through the API, on titles, labels, comment bodies and downloaded artifacts, so none of them needs pull request file contents.

The `|| github.sha` half covers the events that carry no pull request context (`issues`, `issue_comment`, `push`, `workflow_dispatch`).
It has to come second: on a `pull_request` event `github.sha` is the merge ref itself.
Where a workflow is triggered only by `pull_request`, use `${{ github.event.pull_request.base.sha }}` alone.

Do not reach for `pull_request_target` instead.
It grants a full write token and secrets in the base-repository context, so pairing it with a checkout of pull request code creates the vulnerability this rule exists to prevent.

`scripts/ci/test_privileged_workflow_checkouts.py` enforces the rule over every workflow, and its docstring carries the full rationale, including why jobs holding only `security-events: write` (codeql.yml, semgrep.yml) and `contents: read` (`dependabot-verification-metadata-regen.yml`) are correctly exempt.
