# Instructions for agents

- The development cycle for a GitHub issue or PR (Orchestrator, Programmer, Reviewer, Verification Planner, Verifier) is the `orchestrate` plugin from [aunger/dot-and-tilde](https://github.com/aunger/dot-and-tilde); its `README.md` there describes it.
  The user starts it with `/orchestrate <issue-number>`.
  The plugins come from `.claude/install-plugins.sh`, which installs `orchestrate`, `github-supplement`, `bag-o-agents` and `agent-citizenship` at the dot-and-tilde commit `.claude/gb4pc-tools/.claude-plugin/marketplace.json` pins; the session-start hook runs it in cloud sessions.
  If `/orchestrate` is missing, run `.claude/install-plugins.sh`, then ask the user to run `/reload-skills`.
  If you are asked to address an issue or PR without a specific assigned role, say that the cycle is started with `/orchestrate` rather than entering a role yourself.
  Its project-specific settings (the repository, the named CI checks, the verification-plan marker, the CI Monitor's regexes) are in `.claude/orchestrate.json`; `scripts/test_orchestrate_config.py` checks that file.
- If creating a PR, read `./agents/pr_creation.md`.
- If editing code, read `./agents/code_edit.md`.
- If writing an issue description or a comment, read `./agents/writing.md`.
- If reading a PR's or commit's check-runs, run the orchestrate plugin's CI Monitor rather than listing them by hand: `python3 <path>/scripts/ci_monitor/ci_monitor.py --once --pr <N> --repo aunger/gallery-button-for-pixel-camera --config .claude/orchestrate.json` (or `--sha <SHA>`), where `<path>` is the `installPath` that `claude plugin list --json` gives for `orchestrate@gb4pc-tools`.
  See "One read, no polling" in the `README.md` beside it.
- If bumping the Gradle, AGP, KGP, or Compose-plugin version, or working on a Dependabot PR or the `gradle/verification-metadata.xml` regeneration workflows, read `./gradle/README.md`.

## Scratchpad files

Every agent in a session, including each worktree sub-agent, is given the same scratchpad directory.
A helper left at its top level can be overwritten or run by another agent, which then reports results from a tree it never touched.

- Keep scratch files in a subdirectory of the scratchpad named after your worktree directory (`agent-<id>`), or for the top-level agent, the checkout directory, never at its top level.
- A helper that reports a test result prints the worktree path and `HEAD` it ran against, and you check both before reporting.

The second point is what catches a crossed result; the first only makes one rarer.
github-supplement's scratchpad hook warns a worktree agent whose `Bash` command, or `Write`, `Edit` or `NotebookEdit` path, reaches the scratchpad outside its subdirectory.

## Prose style and typographical standards

The following rules apply to prose in documents, code comments, and text output.

### Word wrap in Markdown, etc

Split prose at sentence breaks.
This balances line-diff stability with moderate line lengths.

This rule applies only when word wrap is a matter of preference or style, such as in Markdown and other auto-flowed source text.

### Avoid angle quote, em-dash, and ellipsis characters

Prefer widely compatible characters, such as those in ASCII, when the option exists.

Since this rule refers only to prose, characters chosen for their specific meaning in code or markup (such as pairs of back-ticks) are exempt.

- Replace dashes with commas, semicolons, or parentheses when grammatically possible, and double-hyphens otherwise.
- Replace ellipses with triple periods.
- Replace angled, directional, or smart quotes (single quote or double quote) with straight quote characters.

`scripts/lint/check_prose_chars.py` enforces this over every tracked text file, from the pre-commit hook and from the Lint workflow's hygiene job.
Where code genuinely needs one of these characters, spell it as an escape such as `\u2014`.

### Do not surround dashes or double-hyphens with spaces

Omit the spaces--like this.

### Cite code with a path and line

Cite code as `path/to/File.kt:59`.
Where the surface renders links, link the citation to the file at that line.
