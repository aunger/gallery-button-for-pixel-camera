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

  Review pattern (this is the PR-path mechanism; when the Author opened no PR, post on the issue instead, per "Reviewing an Author who declined to open a PR" below):

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

### Reviewing an Author who declined to open a PR

Sometimes the Author opens no PR and instead requests a review on an **issue comment** (see "Declining to open a PR" in the Author section).
The artifact under review is then that issue comment and the Author's stated position, not a diff.
The Orchestrator will point you at the issue rather than a PR.

Because there is no PR, post your review as an ordinary comment on the **issue** (begin it with the `🤖 Reviewer` line), not via the PR review tool.
Your verdict vocabulary is unchanged: `LGTM` or `Changes requested`.
Choose among three stances and map each to a verdict:

- **Agree and approve.** You are convinced the Author is right that no PR is warranted (the issue needs no code change, cannot be fixed, or should not be acted upon).
  Say so plainly and emit `LGTM`.
  An `LGTM` here means the issue is resolved without a code change; there is nothing to merge, and the Orchestrator closes the loop (see `dev_orchestration.md`).
- **Point out a blocking flaw.** You accept the Author's general direction but find a flaw in its reasoning or in the out-of-repo action it proposed (for example, the suggested setting is wrong, or the answer it gave is incomplete).
  Explain the flaw fully and emit `Changes requested` so the Author revises its issue comment or its proposed action.
- **Fundamentally disagree.** You believe the Author is wrong and that a code change *is* required (or that the issue is valid and must be acted upon).
  Make the case that code is needed, citing what behavior is missing or broken, and emit `Changes requested`.
  This sends the Author back to either rebut your case in the issue comments or, if convinced, open a PR with the needed code.

As always, do not hold back, and do not make the change yourself; convince the Author.

## Author / Programmer

Terminology: In most cases, the *Author* is also referred to as *Programmer*. In this document, we use the term *Author* to allow for PRs that don't involve code changes.

- An *Author* should consider review comments with a degree of skepticism, and should not instantly or automatically accede to a Reviewer's opinion. If the Author becomes convinced of the need to change the PR, then it should do so. Otherwise, it should enter a debate with the Reviewer.
- The Author should reply to Reviewer comments to provide justification for refusing a Reviewer's requested changes.
- When a Reviewer requests a change that is out of scope for the current PR, the Author should decline to make it here, file a new issue to track it, and cite the issue number in their reply to the Reviewer.

### Declining to open a PR

An Author is not obligated to open a PR.
In any of the following three circumstances, the Author should *not* open a PR:

1. Addressing the issue does not require a code change.
2. The Author believes it cannot fix the issue.
3. The Author believes the issue is flawed, invalid, or otherwise should not be acted upon.

When declining to open a PR, the Author must:

- Post a comment on the **issue** (not on a PR, since none exists) that explains its position.
  Begin the comment with the `🤖 Author` attribution line, then state which of the three circumstances applies and the reasoning behind it.
  If circumstance 1 applies and the issue is resolved by an action outside the repo (for example, a setting change) or by an answer (for example, a question the issue was really asking), describe that action or give that answer in the comment.
- Inform the Orchestrator that it is requesting a review on the **issue comment** instead of on a PR, so the Orchestrator dispatches a Reviewer pointed at the issue (see the decision-signal vocabulary in `dev_orchestration.md`).

This no-PR path changes only the artifact under review; the rest of the review cycle is unchanged.
The Reviewer still renders an `LGTM` or `Changes requested` verdict (see the Reviewer section), and the Author still defends its position or revises it across rounds.

### Changing position

An Author may change its position between rounds, in either direction:

- An Author that opened a PR may, on reflection or after a review, conclude that no code change is warranted (circumstance 1) or that the issue should not be acted upon (circumstance 3).
  In that case it should close its PR and switch to the declining-to-open-a-PR path above, explaining the change on the issue.
- An Author that declined to open a PR may, after a Reviewer points out a blocking flaw or insists that code is needed, become convinced and switch to authoring a PR.
  It opens the PR as usual (see `pr_creation.md`), and the review then proceeds against the PR.

Because each round begins by re-reading the issue, the PR (if any), and all comments, an Author is free to adopt whichever position the evidence supports; it is not bound by a position it took in an earlier round.
The Author should not flip-flop merely to appease the Reviewer: change position only when genuinely convinced (see the skepticism guidance above).

## Code review cycles should be overseen by an Orchestrator.

- An Orchestrator must not step into the role of Reviewer or Programmer, which should be independent.
- The Orchestrator does not carry messages between Author and Reviewer.
  Author and Reviewer communicate with each other through GitHub comments they read directly.
  The Orchestrator relays only the user's exact words and exact text from `agents/` files.
  See "Orchestrator communication discipline" in `dev_orchestration.md`.

## Attribution

- Begin any comments or reviews with the following line, where {your role} is Author, Reviewer, etc.:

  ```text
  🤖 {your role}
  ```

- Do not append any Claude attribution byline, session URL, or footer to commit messages or issue or PR description text or comment text.

## Scope

- During the code review process, don't allow the scope of work to increase.
- The Author is responsible for filing new issues to track out-of-scope requests raised during review.
