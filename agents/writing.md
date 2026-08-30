# Writing issues, PR descriptions, and comments

This document governs how prose written into GitHub reads.
For a PR's title, description, and test plan, `pr_creation.md` governs what it must contain.

## Lead with evidence

State the finding, then the evidence for it: the command, its output, the `file:line`, the commit the check ran against.
A claim the reader cannot check is worth less than one they can.

Say which claims were verified and which were not.
An implementer needs to know where the ground stops.

## Write for the implementer, not about the author

Cut anything that does not change what the reader does.
No account of how the finding was made, no narration of the search, no remarks on the writing itself.

Never carry a correction's history into the text.
When a statement was wrong, replace it with the correct one and stop there.
Phrases like "rather than merely discouraged", "easy to misread", or "it turns out" are residue from an earlier draft.
Delete them.

## Record decisions so they read as decisions

Name the option chosen and the options rejected, each with its grounds.
An unexplained constraint is indistinguishable from an oversight a year later.

Keep a decision and its implementation in separate issues.
State what is in scope, and state what is deliberately not.

## Make relations explicit

Cross-reference a blocking relation from both sides.
Name what an issue unblocks, and the order the unblocked work should land in.

Leave genuine unknowns as open questions addressed to whoever picks the issue up.
Do not resolve them by guess.

## Mechanics

One sentence per line.
No em dashes.
Cite code as `path/to/File.kt:59`.
Anchor a verification claim to the commit it ran against.
