# PR participation

## Reviews must be conversational, and slightly competitive, between **at least two parties**.

- This slightly competitive interaction between at least two parties is important to the SDLC, because it reduces the presence of untested ideas in our code.

## Reviewer

- A *Reviewer* must not make code changes itself, but should communicate discoveries clearly enough to convince an Author of the need to change the PR.
- A Reviewer should be explicit and fully explain any problems, but should not spend tokens to design their solutions.
- The Reviewer may mention positive aspects of the code under review, but must be blunt and brief.
- The Reviewer need not enforce expectations written with "should" language.
- Although **bylines** (Claude attribution, links) are prohibited, the Reviewer should not mention them in reviews.
- Our agents share the User's GitHub account, so you won't use GitHub's code review features, which require separate accounts. Leave your evaluation as an ordinary comment, and tell the Orchestrator your decision. The user and other agents know to expect this.
- **Do not wait for CI.** Review the diff, form your verdict, and post your review immediately. The Orchestrator uses a CiWatcher agent to check CI status after you exit; you do not need to poll.
- **If CI results are already available** when you complete your review, you may note them in your review text, but do not block on them.
- **Do not return before posting your review.** Posting your review is the only valid exit condition. After forming your verdict, call the review-submission tool before stopping.

  Review pattern:
  ``` no
  (ReviewText, IsReviewApproval) := <review the diff; form your verdict and review text>
  post review: mcp__github__pull_request_review_write(ReviewText, IsReviewApproval)
  ```

- A Reviewer **may** give conditional approval: an approval combined with minimal and specific instructions for the Author to take before merging.
  - This is only appropriate when the request is unlikely to be contested.
  - The remaining change must be simple: a single mechanical edit (rename, deletion, reword, or move) at one location, requiring no design judgment. If the remaining change is more complex than this, request changes instead so the full review cycle continues.
  - Clearly separate the approval signal from the instruction so the Orchestrator can parse both.
  - Phrase it unambiguously, e.g. "Approved, pending [specific change]." or "Approved — please [specific action] before merging."
  - Do not bury the approval or the instruction inside other prose; make each a distinct sentence.

## Author / Programmer

Terminology: In most cases, the *Author* is also referred to as *Programmer*. In this document, we use the term *Author* to allow for PRs that don't involve code changes.

- An *Author* should consider review comments with a degree of skepticism, and should not instantly or automatically accede to a Reviewer's opinion. If the Author becomes convinced of the need to change the PR, then it should do so. Otherwise, it should enter a debate with the Reviewer.
- The Author should reply to Reviewer comments to provide justification for refusing a Reviewer's requested changes.

## Code review cycles should be overseen by an Orchestrator.

- An Orchestrator must not step into the role of Reviewer or Programmer, which should be independent.

## Attribution

#### Issue and PR description text

Do not append any Claude attribution byline, session URL, or footer.

#### Comment text

Do not append any Claude attribution byline, session URL, or footer.

#### Commit messages

Do not append any Claude attribution byline, session URL, or footer.

## Scope

- During the code review process, don't allow the scope of work to increase.
- Instead, spawn related items in the issue tracker.
