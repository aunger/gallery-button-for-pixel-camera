# PR participation

## Reviews must be conversational, and slightly competitive, between **at least two parties**.

- This slightly competitive interaction between at least two parties is important to the SDLC, because it reduces the presence of untested ideas in our code.

## Reviewer

- A *Reviewer* must not make code changes itself, but should communicate discoveries clearly enough to convince an Author of the need to change the PR.
- A Reviewer should be explicit and fully explain any problems, but should not spend tokens to design their solutions.
- The Reviewer may mention positive aspects of the code under review, but must not use many words to do it.
- The Reviewer need not enforce expectations written with "should" language.
- **Before approving, wait** for the CI gates (usually builds and tests) to complete. You need not wait if other required changes are outstanding, but do not send final approval unless the PR is **currently green**.
- **If you must wait, poll** the status every 30 seconds. Do not rely on the flaky CI event hooks. Note: subagents cannot proactively send mid-task status messages to the Orchestrator — communication from subagent to Orchestrator only happens on completion. The Orchestrator will be notified when you exit; keep your polling loop running and do not exit until your review is posted.
- **The polling loop is only viable for short CI runs (under ~3 minutes of active poll time).** Beyond that you will exhaust the subagent active-turn time limit and exit before posting. For long-running CI (e.g. the emulator suite, 7–12 minutes), the Orchestrator is responsible for gating your dispatch on CI completion (see `dev_orchestration.md`). When dispatched in that mode you can assume CI has already resolved — read current status with `mcp__github__pull_request_read` once and post your review immediately, without polling.
- **If the CI gates fail**, report this in your review. Based on the result and your expert evaluation, you may still decide to approve for a false positive.
- **Do not return before posting your review.** Posting your review is the only valid exit condition. "Waiting for CI" is a loop body, not a final state. After your polling loop completes — whether CI is green, failed, or you hit the poll cap — you must call the review-submission tool before stopping.

  Polling pattern:
  ```
  (ReviewText, IsReviewApproval) := <review the diff; form your verdict and review text>
  repeat up to 20 times:
    fetch PR status via mcp__github__pull_request_read
    if all checks SUCCESS or SKIPPED → break
    if any check FAILURE → break
    sleep 30 seconds
  if any check FAILURE:
    reconsider IsReviewApproval — set to false if the failure is caused by this PR's changes
  post review unconditionally: mcp__github__pull_request_review_write(ReviewText, IsReviewApproval)
  ```

## Author / Programmer

Terminology: In most cases, the *Author* is also referred to as *Programmer*. In this document, we use the term *Author* to allow for PRs that don't involve code changes.

- An *Author* should consider review comments with a degree of skepticism, and should not instantly or automatically accede to a Reviewer's opinion. If the Author becomes convinced of the need to change the PR, then it should do so. Otherwise, it should enter a debate with the Reviewer.

## Code review cycles should be overseen by an Orchestrator.

- An Orchestrator must not step into the role of Reviewer or Programmer, which should be independent.

## Scope

- During the code review process, don't allow the scope of work to increase.
- Instead, spawn related items in the issue tracker.
