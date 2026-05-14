# PR participation

## Reviews must be conversational, and slightly competitive, between **at least two parties**.

- This slightly competitive interaction between at least two parties is important to the SDLC, because it reduces the presence of untested ideas in our code.

## Reviewer

- A *Reviewer* must not make code changes itself, but should communicate discoveries clearly enough to convince an Author of the need to change the PR.
- A Reviewer should be explicit and fully explain any problems, but should not spend tokens to design their solutions.
- The Reviewer may mention positive aspects of the code under review, but must not use many words to do it.
- The Reviewer need not enforce expectations written with "should" language.
- **Before approving, wait** for the CI gates (usually builds and tests) to complete. You need not wait if other required changes are outstanding, but do not send final approval unless the PR is **currently green**.
- **If you must wait, poll** the status every 30 seconds. Do not rely on the flaky CI event hooks. **Update your own status every 30 seconds too**, so the Orchestrator doesn't think you're done.
- **If the CI gates fail**, report this in your review. Based on the result and your expert evaluation, you may still decide to approve for a false positive.
- **Do not return before posting your review.** Posting your review is the only valid exit condition. "Waiting for CI" is a loop body, not a final state. After your polling loop completes — whether CI is green, failed, or you hit the poll cap — you must call the review-submission tool before stopping.

  Polling pattern:
  ```
  repeat up to 15 times:
    fetch PR status
    if all checks are SUCCESS or SKIPPED → break
    if any check is FAILURE → break
    sleep 30 seconds
  → post your review (APPROVE, REQUEST_CHANGES, or COMMENT noting CI still pending)
  ```

## Author / Programmer

Terminology: In most cases, the *Author* is also referred to as *Programmer*. In this document, we use the term *Author* to allow for PRs that don't involve code changes.

- An *Author* should consider review comments with a degree of skepticism, and should not instantly or automatically accede to a Reviewer's opinion. If the Author becomes convinced of the need to change the PR, then it should do so. Otherwise, it should enter a debate with the Reviewer.

## Code review cycles should be overseen by an Orchestrator.

- An Orchestrator must not step into the role of Reviewer or Programmer, which should be independent.

## Scope

- During the code review process, don't allow the scope of work to increase.
- Instead, spawn related items in the issue tracker.
