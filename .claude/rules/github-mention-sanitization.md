---
paths:
  - "**/*"
---

# GitHub mentions posted by an agent are sanitized

## An agent cannot reliably post an `@` mention

Text an agent posts to GitHub is passed through mention-sanitization before it is stored.
That filter inserts `U+00B7` (middle dot) characters into anything shaped like an `@` mention, so `@example` is stored as a string that is not a mention.

This applies to issue bodies, issue comments, pull request bodies, and pull request comments.
It has been observed on comments authored as `claude[bot]` and on comments and pull request bodies authored as the repository owner through the GitHub MCP server, so it is not specific to one identity or one posting path.

The consequence worth remembering is not cosmetic.
Anything that *acts* on a mention will never see one:

- A bot command surface, such as a `@dependabot` command, receives nothing and does nothing.
- A request for a human's attention does not notify that person.

Nothing reports an error.
The stored text looks correct at a glance, because a middle dot is easy to miss, so the failure is silent at both ends.
PR #874 lost about eight hours to a `@dependabot` command that was never delivered.

## What to do instead

- Prefer a mechanism that does not need a mention at all. Re-running a workflow, or a tool call, is unaffected.
- When a mention is genuinely required, ask a human to post it, and say plainly why you cannot.
- Never re-post a failed mention verbatim expecting a different result. Each attempt is sanitized the same way.

## Checking whether this still holds

This behavior is external to the repository and can change without notice.
To check it, post the mention in any GitHub text field, read the stored text back through the API, and compare it byte for byte with what was sent.

Content committed through git is **not** affected, only text posted through the API.
That difference is itself the cheapest test: commit a line containing a mention, post the same line as a comment, and compare the two.
The committed copy is intact whenever the posted copy is not.
