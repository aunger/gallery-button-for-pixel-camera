# PR participation

## Reviews must be conversational, and slightly competitive, between **at least two parties**.

- This slightly competitive interaction between at least two parties is important to the SDLC, because it reduces the presence of untested ideas in our code.

## A PR carries comments on three surfaces

Reading a PR means reading all three.
Each is a separate call, and none of them reports what the others hold:

| Surface               | What it holds                                              | `mcp__github__pull_request_read` method |
| --------------------- | ---------------------------------------------------------- | --------------------------------------- |
| Issue-comment stream  | Ordinary comments on the PR                                | `get_comments`                          |
| Review bodies         | The top-level text of each submitted review                | `get_reviews`                           |
| Inline review threads | Comments anchored to a line of the diff, and their replies | `get_review_comments`                   |

An issue has no diff, so it carries only the first surface: "all comments on the issue" is one call.

Answer an inline comment in a thread (`mcp__github__add_reply_to_pull_request_comment`), rather than as a new PR comment, which leaves the thread shown as unanswered.

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

  (This is the PR-path mechanism. If the Author opened no PR, see "Reviewing an Author who declined to open a PR" below.)

  ```text
  (ReviewText, IsReviewApproval) := <review the diff; form your verdict and review text>
  post review: mcp__github__pull_request_review_write(ReviewText, IsReviewApproval)
  ```

- After posting your review, tell the Orchestrator your decision using the fixed decision-signal vocabulary from `dev_orchestration.md`: one of `LGTM`, `Changes requested`, or `Cannot work`.
  The Orchestrator routes this signal verbatim; it does not relay your review prose to the Author.
  The Author reads your review from GitHub directly.

- Your verdict is one of exactly three words, because it selects among three different Orchestrator actions:

  - `LGTM`: the PR is good to merge. If you want any change made before merge, do not use this; request changes instead so the full review cycle continues.
  - `Changes requested`: the PR needs more work, and another Author round can supply it. This sends the Author back to correct or complete its work.
  - `Cannot work`: the coding phase cannot be completed by any further Author round, because the requirements are unattainable or self-contradictory, or a blocker is outside the Author's control (for example, the CI infrastructure itself is broken). This escalates to the user instead of looping. Explain the specifics in your review comment (or, on the no-PR path, in your issue comment). Do not reach for it merely because the PR is imperfect: use `Changes requested` whenever another round could help.

### CI checks during the development cycle

Not all CI checks can pass while the development cycle is active, and that is expected.
Some required checks, such as "No blocking labels", validate the post-cycle state of a PR and fail by design during agentic reviews.

### Reviewing an Author who declined to open a PR

Sometimes the Author opens no PR and instead posts its position as an **issue comment** (see "Declining to open a PR" in the Author section).
The artifact under review is then that issue comment and the Author's stated position, not a diff.
The Orchestrator will point you at the issue rather than a PR.

Because there is no PR, post your review as an ordinary comment on the **issue** (begin it with the `🤖 Reviewer` line), not via the PR review tool.
Your verdict vocabulary is unchanged: `LGTM`, `Changes requested`, or `Cannot work`.
Choose among these stances and map each to a verdict:

- **Agree and approve.** You are convinced the Author is right that no PR is warranted (the issue needs no code change, cannot be fixed, or should not be acted upon).
  Say so plainly and emit `LGTM`.
  An `LGTM` here means the issue is resolved without a code change; there is nothing to merge.
- **Point out a blocking flaw.** You accept the Author's general direction but find a flaw in its reasoning or in the out-of-repo action it proposed (for example, the suggested setting is wrong, or the answer it gave is incomplete).
  Explain the flaw fully and emit `Changes requested` so the Author revises its issue comment or its proposed action.
- **Fundamentally disagree.** You believe the Author is wrong, the issue is valid, and a change is required and possible.
  Make the case that code is needed, citing what behavior is missing or broken, and emit `Changes requested`.
  This sends the Author back to either rebut your case in the issue comments or, if convinced, open a PR with the needed code.
- **The issue is unworkable.** You conclude the issue as stated cannot be resolved by any Author, or a required blocker is genuinely outside Author control.
  Explain why fully and emit `Cannot work`, which escalates to the user instead of looping.

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
3. The issue is flawed, invalid, or otherwise should not be acted upon.

When declining to open a PR, the Author must:

- Post a comment on the **issue** (not on a PR, since none exists) that explains its position.
  Begin the comment with the `🤖 Author` attribution line, then state which of the three circumstances applies and the reasoning behind it.
  If circumstance 1 applies and the issue is resolved by an action outside the repo (for example, a setting change) or by an answer (for example, the issue is really asking a question), describe that action or give that answer in the comment.
- Tell the Orchestrator that it opened no PR and point to the **issue comment** it posted instead, using the work-location report in `dev_orchestration.md`.
  The Orchestrator then points a Reviewer at the issue.

This no-PR path changes only the artifact under review; the rest of the review cycle is unchanged.
The Reviewer still renders an `LGTM`, `Changes requested`, or `Cannot work` verdict (see the Reviewer section), and the Author still defends its position or revises it across rounds.

### Changing position

An Author may change its position between rounds, in either direction:

- An Author that opened a PR may, after a review, conclude that no code change is warranted or that the issue should not be acted upon.
  In that case it should close its PR and switch to the declining-to-open-a-PR path above, explaining the change on the issue.
- An Author that declined to open a PR may later become convinced and switch to authoring a PR.
  It opens the PR as usual (see `pr_creation.md`), and the review then proceeds against the PR.

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

> [!NOTE]
> Reviewers: Do not enforce the attribution rules.
> The rule prohibits writing a byline; nobody is obliged to remove one that landed.
> A GitHub workflow removes them presently, so a byline that gets through is best to disregard.

## Scope

- Reviewer and Author **both** must push back against scope-creep as a shared responsibility.
- Either agent (Author or Reviewer) may add a section to their PR comment regarding follow-up suggestions.
  These will be filed as issues for future consideration after this PR.
