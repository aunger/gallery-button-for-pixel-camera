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

- New behavior must be accompanied by automated tests.
- You should not modify the requirements of existing tests, unless necessary. If your change loosened a test's retirements, you must declare each instance of this in your comment message and PR text, on its own line.
  - For example, *Modified test `pocket_square_is_a_square` to allow circles in addition to squares, because they are more round.*
- Existing tests must not be deleted or silently disabled to make a change compile.

## Delegating to nested sub-agents

When you dispatch a nested sub-agent via the Agent tool, follow these rules:

- **Use foreground (the default) when you need the results in the same turn.**
  Do not set `run_in_background: true` for any sub-agent whose findings you will act on before returning.
  The Agent tool blocks until the sub-agent completes, so the results are available immediately.
- **Do not exit before a foreground sub-agent completes.**
  A foreground sub-agent runs to completion during the same tool call; you receive its result as the return value.
  If the call returns, the sub-agent is done--you do not need to wait further.
  Exit only after you have consumed or recorded the result.
- **Use `run_in_background: true` only for fire-and-forget work** whose results are not needed in the current turn.
  If you are unsure whether you will need the results, use foreground.
- **Never rely on a background sub-agent's output in the same turn.**
  Background sub-agents surface as task-notification events after you exit.
  Their results are not available to you during the current turn.

## Finish well

- If you are a sub-agent and were given a branch or branch name, commit your changes before you return.
- Build locally and execute the unit tests. Don't make someone else do it for you or wait for CI.
