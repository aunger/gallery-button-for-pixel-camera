# Code edits

## Prefer simple code

- Prefer implementations that simplify, rather than complexify existing code.
- Don't Repeat Yourself.

## Organize your changes

- You should split your changes into commits by concern.
    - Avoid monolithic commits.
    - **Do not** organize your commits by file rather than by concern (e.g. "all changes to Foo.kt in one commit, Bar.kt in another").
- When you refactor existing code, **always** commit the pure refactoring (which does not change behavior or output) separately. **Do not** combine that refactoring with a change in functionality or behavior.

## Test coverage

- New behavior must be accompanied by tests.
- Existing tests must not be deleted or silently disabled to make a change compile.

## Finish well

- If you are a sub-agent and were given a branch or branch name, commit your changes before you return.
- Build locally and execute the unit tests. Don't make someone else do it for you or wait for CI.
