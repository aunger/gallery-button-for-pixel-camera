# Verification routing

The Orchestrator reaches this document from `ci-monitor-loop.md` once every named check is green.
Green named checks do not prove the PR is mergeable: a merge gate may still be red, and a draft cannot merge at all until someone marks it ready.
Neither bears on the requirements surfaced here, which are about what must be true before a merge, not about whether one is possible today.
The Orchestrator does not scan the issue or PR itself.

## Surface the before-merging requirements

```text
surfaceBeforeMergingRequirements:
  Dispatch a Verification Planner (`orchestrate:verification-planner`) with the dispatch form.
  The Planner assembles the before-merging list and files a tracking issue per item; it does not consult the user.
  It reports the id of the verification-plan comment it posted or updated, and its three lists.
  If the Planner reports its before-merging list is empty: this step is complete; apply this transition to
  BOTH the issue and the PR:

    | Add label |
    |---|
    | `verified` |

  Then goto "Concluding" in SKILL.md.

  Otherwise, relay the before-merging list to the user verbatim, and apply this transition to the PR:

    | Add label |
    |---|
    | `verification needed` |

  Dispatch a Verifier (`orchestrate:verifier`) with the dispatch form, `Verification plan comment` set to the
  id the Planner reported; it does not consult the user.
  Route on its terminal signal below.
```

## Route on the Verifier's signal

The Verifier emits exactly one of three signals.

- `Verification passed`: every before-merging item was confirmed automatically.
  The before-merging requirements process is complete.
  The PR is not mergeable yet: a merge gate stays red until "Concluding" removes `orchestrating`, and a draft PR also needs someone to mark it ready for review, which is the user's call.
  Apply this transition to the PR, then goto "Concluding" in SKILL.md:

  | Remove label          | Add label  |
  | --------------------- | ---------- |
  | `verification needed` | `verified` |

- `Verification revealed an error`: route the PR back to a new Author round (goto "Assigning a Programmer" in SKILL.md, with `Verification plan comment` filled in the form).
  The Verifier leaves its diagnosis as a PR comment, which the Author reads from GitHub; the Orchestrator does not relay it.
  Apply this transition to the PR first:

  | Remove label          | Add label           |
  | --------------------- | ------------------- |
  | `verification needed` | `changes requested` |

- `Verification incomplete`: no item failed, but one or more before-merging items could not be automated and remain open for a human to verify.
  Do not treat the PR as cleared and do not start a new Author round.
  Inform the user that manual verification is still required, and that the PR may be merged once every open before-merging tracking issue is resolved; if the PR is a draft, say that marking it ready for review is also needed and is the user's call.
  Apply this transition to the PR only if needed (the label is probably already applied), then goto "Concluding" in SKILL.md:

  | Add label             |
  | --------------------- |
  | `verification needed` |
