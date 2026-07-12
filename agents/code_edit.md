# Code edits

## Prefer simple code

- Prefer implementations that simplify, rather than complexify existing code.
- Don't Repeat Yourself.

## Organize your changes

- You should split your changes into commits by concern.
  - Avoid monolithic commits.
  - **Do not** organize your commits by file rather than by concern (e.g. "all changes to Foo.kt in one commit, Bar.kt in another").
- When you refactor existing code, **always** commit the pure refactoring (which does not change behavior or output) separately. **Do not** combine that refactoring with a change in functionality or behavior.

## Dependency versions

- Every dependency version must be pinned (exact version, tag, or commit SHA; never a range or an unpinned "latest").
- Pin to a recent version, not an old one you happen to already know; check what is current before picking a version.
- Always pin the latest patch build of whichever minor/major version you choose.

## Test coverage

- New behavior must be accompanied by automated tests.
- You should not modify the requirements of existing tests, unless necessary. If your change loosened a test's retirements, you must declare each instance of this in your comment message and PR text, on its own line.
  - For example, *Modified test `pocket_square_is_a_square` to allow circles in addition to squares, because they are more round.*
- Existing tests must not be deleted or silently disabled to make a change compile.

## Finish well

- If you are a sub-agent and were given a branch or branch name, commit your changes before you return.
- Build locally and execute the unit tests. Don't make someone else do it for you or wait for CI.
