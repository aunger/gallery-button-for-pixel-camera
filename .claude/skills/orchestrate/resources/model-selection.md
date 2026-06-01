# Resource: Model selection

A summary of the Model selection rules in `agents/dev_orchestration.md`. The linked section governs.

Use the first rule that applies, per role:

1. **User-specified:** the user named a model for this role. Use it.
2. **Label-based:** the work item carries a `c-a-<model>` label for the Author, or `c-r-<model>` for the Reviewer. Use that model.
3. **Default:** Sonnet.

## Label to model

| Label | Role | Model |
|-------|------|-------|
| `c-a-haiku` | Author | Haiku |
| `c-a-sonnet` | Author | Sonnet |
| `c-a-opus` | Author | Opus |
| `c-r-haiku` | Reviewer | Haiku |
| `c-r-sonnet` | Reviewer | Sonnet |
| `c-r-opus` | Reviewer | Opus |

## Reviewer floor

The Reviewer tier is at least the Author tier minus 1.
The full complexity rubric is in `agents/task_complexity.md` (marked draft; apply only when in use).
