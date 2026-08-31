# Writing Prose for issue descriptions and comments

Issues and comments are read by whoever picks the work up.
Write them for that reader.

A PR description is a different artifact and is not covered here.
After merge it is this repository's record of the change, so the narrative these rules cut is part of its value.
`pr_creation.md` governs PR descriptions.

## Lead with evidence

State the finding, then the evidence for it: the command, its output, the code citation, the commit the check ran against.
A claim the reader cannot check is worth less than one they can.

Say which claims were verified and which were not.
An implementer needs to know where the ground stops.

## Write for the audience

Cut anything that does not change what the reader does.
No account of how the finding was made, no narration of the search, no remarks on the writing itself.

Never carry a correction's history into the text.
When a statement was wrong, replace it with the correct one and stop there.

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

See `.claude/rules/prose-style.md`.
