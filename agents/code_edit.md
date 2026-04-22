# Code edits

## Prefer simple code

- Prefer implementations that simplify, rather than complexify existing code.
- DRY (Don't Repeat Yourself).

## Prefer simple, discrete edits

- Split changes into commits by concern. A monolithic PR is a smell, as is one whose commits are organized by file rather than by concern (e.g. "all changes to Foo.kt in one commit, all changes to Bar.kt in another").
- When existing code is refactored, always commit the pure refactoring (which does not change behavior or output) separately. **Do not** combine that refactoring with a change in functionality or behavior.

## Test coverage

- New behaviour must be accompanied by tests. Existing tests must not be deleted or silently disabled to make a change compile.
