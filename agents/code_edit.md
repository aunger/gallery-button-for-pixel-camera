# Code edits

## Prefer simple code

- Prefer implementations that simplify, rather than complexify existing code.
- Don't Repeat Yourself.

## Organize your changes

- You should split your changes into commits by concern.
  - Avoid monolithic commits.
  - **Do not** organize your commits by file rather than by concern (e.g. "all changes
    to Foo.kt in one commit, Bar.kt in another").
- When you refactor existing code, **always** commit the pure refactoring (which does
  not change behavior or output) separately.
  **Do not** combine that refactoring with a change in functionality or behavior.

## Dependency versions

- This section applies to third-party dependencies: anything not published directly by
  GitHub itself (the `actions/` and `github/` GitHub Actions namespaces are first-party
  and exempt). It covers third-party GitHub Actions (for example
  `android-actions/setup-android`, `softprops/action-gh-release`) and pre-commit hook
  repos (for example `astral-sh/ruff-pre-commit`), regardless of how well-known or
  trusted the publisher is.
- Every such dependency version must be pinned to a commit SHA (never a range, a tag, or
  an unpinned "latest"). A tag is not a valid pin; unlike a commit SHA, a tag can be
  moved to point at a different commit after the fact, so it is not truly immutable.
- When you newly introduce or update a pin, pin to a recent version, not an old one you
  happen to already know.
  Check what is current before picking a version, and pin the latest patch build of
  whichever minor/major version you choose.
  This does not obligate you to bump every existing pin in a file just because you
  touched it; only the ones you are adding or intentionally updating.
- Converting an existing pin from a tag to the equivalent commit SHA, without changing
  which version it points to, is not itself an "update" for this purpose.
  Only pin an unfamiliar or newer version when you are intentionally changing which
  version is used.

## Test coverage

- New behavior must be accompanied by automated tests.
- You should not modify the requirements of existing tests, unless necessary.
  If your change loosened a test's retirements, you must declare each instance of this
  in your comment message and PR text, on its own line.
  - For example, *Modified test `pocket_square_is_a_square` to allow circles in addition
    to squares, because they are more round.*
- Existing tests must not be deleted or silently disabled to make a change compile.

## Finish well

- If you are a sub-agent and were given a branch or branch name, commit your changes
  before you return.
- Build locally and execute the unit tests.
  Don't make someone else do it for you or wait for CI.
