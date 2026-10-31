#!/usr/bin/env python3
"""check_dispatch.py: PreToolUse hook on the Agent tool for the orchestrate plugin.

Wired in hooks/hooks.json. When an Agent call's `subagent_type` names one of
this plugin's agents, the `prompt` must be exactly the dispatch form of
reference/dispatch-form.md; anything else is refused with exit code 2 and the
reason on stderr, which Claude Code puts in front of the model. A call to any
other agent type passes through untouched.

This is the enforcement behind the Orchestrator's communication discipline:
the form makes over-sharing impossible rather than merely forbidden. It
applies whoever the caller is, because the form is the agents' interface.

Exit codes:
    0  nothing to say: not an Agent call to a plugin agent, the form, or the
       hooks are disengaged for this session (see hook_state.py)
    2  refused; the reason is on stderr

A fault in this script (unreadable input, a crash) exits 0 after a warning,
so a broken hook never blocks a session.

Standard library only.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dispatch_form import PLUGIN_AGENT_TYPES, form_errors, refusal
from hook_state import hooks_disengaged


def decide(payload):
    """Return (exit_code, message) for a hook payload."""
    if not isinstance(payload, dict) or payload.get("tool_name") != "Agent":
        return 0, ""
    if hooks_disengaged(payload.get("session_id")):
        return 0, ""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0, ""
    if tool_input.get("subagent_type") not in PLUGIN_AGENT_TYPES:
        return 0, ""
    errors = form_errors(tool_input.get("prompt"))
    if not errors:
        return 0, ""
    return 2, refusal("the prompt of this %s dispatch" % tool_input.get("subagent_type"), errors)


def main():
    try:
        payload = json.load(sys.stdin)
    except (OSError, ValueError) as error:
        sys.stderr.write("[orchestrate] check_dispatch.py could not read its input: %s\n" % error)
        return 0
    code, message = decide(payload)
    if message:
        sys.stderr.write(message + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
