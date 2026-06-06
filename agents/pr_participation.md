# PR participation

## Reviews must be conversational, and slightly competitive, between **at least two parties**.

- This slightly competitive interaction between at least two parties is important to the SDLC, because it reduces the presence of untested ideas in our code.

## Reviewer

- A *Reviewer* must not make code changes itself, but should communicate discoveries clearly enough to convince an Author of the need to change the PR.
- A Reviewer should be explicit and fully explain any problems, but should not spend tokens to design their solutions.
- The Reviewer must not hold back.
  The Reviewer may be the only one equipped to notice inconsistencies and inaccuracies.
  Even small errors can cause misunderstandings down the road, so don't skip "nits".
- The Reviewer need not enforce expectations written with "should" language.
- Although **bylines** (Claude attribution, links) are prohibited, the Reviewer must not mention them in reviews.
- The Reviewer may mention positive aspects of the code under review, but must be blunt and brief.
- Our agents share the User's GitHub account, so you won't use GitHub's code review features, which require separate accounts. Leave your evaluation as an ordinary comment, and tell the Orchestrator your decision. The user and other agents know to expect this.
- **If CI results are already available** when you complete your review, you may note them in your review text, but do not block on them.
  The Orchestrator runs the CI Monitor script after you exit; you do not need to poll.
- **Do not return before posting your review.** Posting your review is the only valid exit condition. After forming your verdict, call the review-submission tool before stopping.

  Review pattern:
  ``` no
  (ReviewText, IsReviewApproval) := <review the diff; form your verdict and review text>
  post review: mcp__github__pull_request_review_write(ReviewText, IsReviewApproval)
  ```

- After posting your review, tell the Orchestrator your decision using the fixed decision-signal vocabulary from `dev_orchestration.md`: one of `LGTM` or `Changes requested`.
  The Orchestrator routes this signal verbatim; it does not relay your review prose to the Author.
  The Author reads your review from GitHub directly.

- Your verdict is binary: either the PR is good to merge (`LGTM`) or it needs more work (`Changes requested`).
  There is no middle option.
  If you want any change made before merge, request changes so the full review cycle continues.

## Author / Programmer

Terminology: In most cases, the *Author* is also referred to as *Programmer*. In this document, we use the term *Author* to allow for PRs that don't involve code changes.

- An *Author* should consider review comments with a degree of skepticism, and should not instantly or automatically accede to a Reviewer's opinion. If the Author becomes convinced of the need to change the PR, then it should do so. Otherwise, it should enter a debate with the Reviewer.
- The Author should reply to Reviewer comments to provide justification for refusing a Reviewer's requested changes.
- When a Reviewer requests a change that is out of scope for the current PR, the Author should decline to make it here, file a new issue to track it, and cite the issue number in their reply to the Reviewer.

## Code review cycles should be overseen by an Orchestrator.

- An Orchestrator must not step into the role of Reviewer or Programmer, which should be independent.
- The Orchestrator does not carry messages between Author and Reviewer.
  Author and Reviewer communicate with each other through GitHub comments they read directly.
  The Orchestrator relays only the user's exact words and exact text from `agents/` files.
  See "Orchestrator communication discipline" in `dev_orchestration.md`.

## Attribution

#### Issue and PR description text

Do not append any Claude attribution byline, session URL, or footer.

#### Comment text

Do not append any Claude attribution byline, session URL, or footer.

#### Commit messages

Do not append any Claude attribution byline, session URL, or footer.

## Scope

- During the code review process, don't allow the scope of work to increase.
- The Author is responsible for filing new issues to track out-of-scope requests raised during review.
