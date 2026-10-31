#!/usr/bin/env python3
"""orchestrator_guard.py: PreToolUse hook that keeps the Orchestrator in role.

Registered by the frontmatter of SKILL.md when /orchestrate is invoked, for
the rest of that session, on Edit, Write, NotebookEdit, Read and SendMessage.
It applies only to the main session, the one the Orchestrator runs in: a
payload that carries `agent_id` comes from a sub-agent and passes through,
since the Programmer edits and the Verifier reads whatever it needs.

In the main session it enforces three of the Orchestrator's non-negotiable
rules from SKILL.md:

- Edit, Write and NotebookEdit are refused: the Orchestrator edits no file.
- Read is refused except for a Markdown file, or a file under a `.claude`
  directory (the project configuration, this plugin's reference files): the
  Orchestrator reads project instructions, not source.
- SendMessage must carry exactly the dispatch form of
  reference/dispatch-form.md, because resuming a sub-agent is the only
  message the Orchestrator sends an agent.

A refusal exits 2 with the reason on stderr, which Claude Code puts in front
of the model. /orch_hooks disengage (hook_state.py) switches the guard off for
the session that runs it. A fault in this script exits 0 after a warning, so a
broken hook never blocks a session.

Standard library only.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dispatch_form import form_errors, refusal
from hook_state import hooks_disengaged

EDIT_TOOLS = frozenset({"Edit", "Write", "NotebookEdit"})

MODE_NOTE = (
    "This session is in Orchestrator mode (/orchestrate); its hooks stay registered until the "
    "session ends. The Orchestrator dispatches a Programmer for any change to the tree and a "
    "Reviewer for any judgement of it; it does neither itself. For other work, start a new "
    "session, or have the user run /orch_hooks disengage."
)


def read_allowed(path):
    """Return True when the Orchestrator may Read `path`."""
    if not isinstance(path, str) or not path:
        return False
    normalized = path.replace("\\", "/")
    if normalized.lower().endswith((".md", ".markdown")):
        return True
    return any(part == ".claude" for part in normalized.split("/"))


def decide(payload):
    """Return (exit_code, message) for a hook payload."""
    if not isinstance(payload, dict) or payload.get("agent_id"):
        return 0, ""
    if hooks_disengaged(payload.get("session_id")):
        return 0, ""
    tool = payload.get("tool_name")
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
            "Markdown and files under .claude/ only: project instructions, the project "
            "configuration, and the plugin's own files. " % path
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
    code, message = decide(payload)
    if message:
        sys.stderr.write(message + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
