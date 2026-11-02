#!/usr/bin/env python3
"""orchestrator_guard.py: PreToolUse hook that keeps the Orchestrator in role.

Registered by the frontmatter of the Orchestrator skill
(skills/orchestrate/SKILL.md) when /orchestrate is invoked, for the rest of
that session, on Edit, Write, NotebookEdit, Read, Grep, Bash, Monitor and
SendMessage. It applies only to the main session, the one the Orchestrator
runs in: a payload that carries `agent_id` comes from a sub-agent and passes
through, since the Programmer edits and the Verifier reads whatever it needs.

In the main session it enforces the file and shell side of the Orchestrator's
non-negotiable rules from that skill:

- Edit, Write and NotebookEdit are refused: the Orchestrator edits no file.
- Read is refused except for a Markdown file, or a file under a `.claude`
  directory (the project configuration, this plugin's reference files); a
  path under `.claude/worktrees/`, where the sub-agents' checkouts live, is
  refused whatever it is. The Orchestrator reads project instructions, not
  source.
- Grep is refused unless it searches a path Read would allow, or only
  Markdown files (`glob` ending in `.md`), since its matches are file content.
  Glob, which returns names only, is not covered.
- Bash and Monitor run only the commands the skill tells the Orchestrator to
  run (see BASH_ALLOWED_FORMS): `date`, `sleep`, a few read-only or
  branch-creating `git` subcommands, and this plugin's own label, Monitor,
  config-check and cost scripts. A command carrying any shell operator,
  redirection or expansion is refused outright, so an allowed command cannot
  smuggle another one.
- SendMessage must carry exactly the dispatch form of
  reference/dispatch-form.md, because resuming a sub-agent is the only
  message the Orchestrator sends an agent.

Not covered: the GitHub MCP tools. The rule that the Orchestrator reads only
the titles, labels and states of the issue and the PR stays a rule there.

A refusal exits 2 with the reason on stderr, which Claude Code puts in front
of the model. /orch-hooks disengage switches the guard off for the session
that runs it. A fault in this script exits 0 after a warning, so a broken hook
never blocks a session.

Standard library only.
"""

import json
import os
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dispatch_form import form_errors, refusal
from hook_state import hooks_disengaged

EDIT_TOOLS = frozenset({"Edit", "Write", "NotebookEdit"})
SHELL_TOOLS = frozenset({"Bash", "Monitor"})

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The plugin scripts the Orchestrator runs, relative to the plugin root.
PLUGIN_SCRIPTS = frozenset(
    {
        "scripts/update_gh_labels.sh",
        "scripts/ci_monitor/ci_monitor.py",
        "scripts/check_config.py",
        "scripts/dispatch_cost.py",
    }
)
INTERPRETERS = frozenset({"python3", "python", "bash", "sh"})
GIT_SUBCOMMANDS = frozenset({"branch", "fetch", "status", "rev-parse", "ls-remote"})
# `git branch` may create a branch or list them, but not delete, move, copy or force.
GIT_BRANCH_REFUSED_FLAGS = frozenset(
    {"-d", "-D", "--delete", "-m", "-M", "--move", "-c", "-C", "--copy", "-f", "--force"}
)
# Characters that would let one command line run, redirect or expand into more.
SHELL_METACHARACTERS = frozenset(";&|<>`$\n\r()")

BASH_ALLOWED_FORMS = (
    "date [-u] [+FORMAT]",
    "sleep SECONDS",
    "git {branch,fetch,status,rev-parse,ls-remote} ... (git branch creates or lists only)",
    "[python3] <plugin root>/scripts/update_gh_labels.sh ...",
    "python3 <plugin root>/scripts/ci_monitor/ci_monitor.py ...",
    "python3 <plugin root>/scripts/check_config.py ...",
    "python3 <plugin root>/scripts/dispatch_cost.py ...",
)

MODE_NOTE = (
    "This session is in Orchestrator mode (/orchestrate); its hooks stay registered until the "
    "session ends. The Orchestrator dispatches a Programmer for any change to the tree and a "
    "Reviewer for any judgement of it; it does neither itself. For other work, start a new "
    "session, or have the user run /orch-hooks disengage."
)


def _in_worktree(parts):
    """Return True when a path's parts run through `.claude/worktrees/`."""
    return any(
        parts[index] == ".claude" and parts[index + 1] == "worktrees"
        for index in range(len(parts) - 1)
    )


def read_allowed(path):
    """Return True when the Orchestrator may Read `path`."""
    if not isinstance(path, str) or not path:
        return False
    parts = path.replace("\\", "/").split("/")
    if _in_worktree(parts):
        return False
    if parts[-1].lower().endswith((".md", ".markdown")):
        return True
    return ".claude" in parts


def grep_allowed(tool_input):
    """Return True when the Orchestrator may run this Grep."""
    path = tool_input.get("path")
    if isinstance(path, str) and path:
        if _in_worktree(path.replace("\\", "/").split("/")):
            return False
        if read_allowed(path):
            return True
    glob = tool_input.get("glob")
    return isinstance(glob, str) and glob.lower().endswith((".md", ".markdown"))


def _plugin_script(word, cwd):
    """Return the plugin-relative path `word` names, or None if it names no plugin script."""
    path = word if os.path.isabs(word) else os.path.join(cwd or os.getcwd(), word)
    try:
        relative = os.path.relpath(os.path.realpath(path), os.path.realpath(PLUGIN_ROOT))
    except ValueError:
        return None
    relative = relative.replace("\\", "/")
    return relative if relative in PLUGIN_SCRIPTS else None


def command_allowed(command, cwd=None):
    """Return True when the Orchestrator may run `command` through Bash or Monitor."""
    if not isinstance(command, str) or not command.strip():
        return False
    if any(char in SHELL_METACHARACTERS for char in command):
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    program, args = words[0], words[1:]
    if program == "date":
        return all(arg in ("-u", "--utc") or arg.startswith("+") for arg in args)
    if program == "sleep":
        return len(args) == 1 and args[0].isdigit()
    if program == "git":
        if not args or args[0] not in GIT_SUBCOMMANDS:
            return False
        if args[0] == "branch":
            return not any(arg in GIT_BRANCH_REFUSED_FLAGS for arg in args[1:])
        return True
    if program in INTERPRETERS:
        return bool(args) and _plugin_script(args[0], cwd) is not None
    return _plugin_script(program, cwd) is not None


def decide(payload):
    """Return (exit_code, message) for a hook payload."""
    if not isinstance(payload, dict) or payload.get("agent_id"):
        return 0, ""
    if hooks_disengaged(payload.get("session_id")):
        return 0, ""
    tool = payload.get("tool_name")
    if not isinstance(tool, str):
        return 0, ""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    if tool in EDIT_TOOLS:
        return (
            2,
            "[orchestrate] refused: the Orchestrator does not edit or write files. " + MODE_NOTE,
        )
    if tool == "Read":
        path = tool_input.get("file_path")
        if read_allowed(path):
            return 0, ""
        return 2, (
            "[orchestrate] refused: the Orchestrator does not read source files (%r). It reads "
            "Markdown and files under .claude/ only (never under .claude/worktrees/, where the "
            "sub-agents' checkouts are): project instructions, the project configuration, and the "
            "plugin's own files. " % path
        ) + MODE_NOTE
    if tool == "Grep":
        if grep_allowed(tool_input):
            return 0, ""
        return 2, (
            "[orchestrate] refused: Grep returns file content, and the Orchestrator reads only "
            "Markdown and files under .claude/ (outside .claude/worktrees/). Give a `path` it may "
            "read, or a `glob` ending in .md. "
        ) + MODE_NOTE
    if tool in SHELL_TOOLS:
        command = tool_input.get("command")
        if command is None and tool == "Monitor":
            return 0, ""
        if command_allowed(command, payload.get("cwd")):
            return 0, ""
        return 2, (
            "[orchestrate] refused: in Orchestrator mode %s runs only these commands, one per call, "
            "with no shell operators, redirections or expansions:\n%s\n"
            % (tool, "\n".join("  " + form for form in BASH_ALLOWED_FORMS))
        ) + MODE_NOTE
    if tool == "SendMessage":
        errors = form_errors(tool_input.get("message"))
        if not errors:
            return 0, ""
        return 2, refusal("this SendMessage", errors)
    return 0, ""


def main():
    try:
        payload = json.load(sys.stdin)
    except (OSError, ValueError) as error:
        sys.stderr.write(
            "[orchestrate] orchestrator_guard.py could not read its input: %s\n" % error
        )
        return 0
    try:
        code, message = decide(payload)
    except Exception as error:  # noqa: BLE001 - a broken hook must never wedge a session
        sys.stderr.write("[orchestrate] orchestrator_guard.py failed: %r\n" % (error,))
        return 0
    if message:
        sys.stderr.write(message + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
