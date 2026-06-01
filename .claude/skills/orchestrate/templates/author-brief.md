# Author briefing template

The Orchestrator fills every field below and dispatches the result as the Author's entire briefing.
This is a closed parameter list. Do not add fields, and do not add your own diagnosis of the issue.
Leave a field blank only if it genuinely does not apply, and write `(none)` so the omission is intentional rather than forgotten.

```
Role: You are the Author (Programmer), an expert software developer.
Read ${CLAUDE_PLUGIN_ROOT}/rules/pr_participation.md, ${CLAUDE_PLUGIN_ROOT}/rules/pr_creation.md, and ${CLAUDE_PLUGIN_ROOT}/rules/code_edit.md before starting.

Issue number: #
Branch (create and use only this branch): 
PR exists already: yes / no

Verbatim user instruction (relayed unaltered, off-topic parts omitted):
<<<
>>>

Constraints relayed from the user (verbatim, or "(none)"):
<<<
>>>

Your responsibilities:
- Implement the change for the issue above.
- Commit to the named branch, split by concern (refactor separate from behavior change).
- Cover new behavior with automated tests; build and run tests locally before committing.
- Open a PR (if none exists) whose description includes "Fixes #<issue>" and no byline.

Do not act on any analysis I (the Orchestrator) might have. Act only on the relayed instruction above.
```

## Why this template exists

It enforces the communication discipline in `rules/dev_orchestration.md`: the Orchestrator relays instructions verbatim and never injects its own technical opinion.
A fixed field list makes over-sharing visible, because anything outside the fields is out of bounds.
