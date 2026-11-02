#!/usr/bin/env python3
"""protect_state.py: PreToolUse hook that keeps tool calls away from the hook state.

Wired in hooks/hooks.json on Bash, Monitor, Write, Edit and NotebookEdit, in
every session and for every agent. Only a person may disengage the plugin's
hooks, through /orch-hooks (see hook_state.py); this refuses a call whose
command or target path names the state file, so the model cannot do it by
writing the file.

It reads text, so a command that assembles the name at run time gets past it.
It is a guardrail against the obvious route, not a sandbox; in Orchestrator
mode orchestrator_guard.py's command allowlist closes the rest.

Exit codes:
    0  nothing to say
    2  refused; the reason is on stderr

A fault in this script exits 0 after a warning, so a broken hook never blocks
a session.

Standard library only.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hook_state import STATE_FILE_NAME

TEXT_FIELDS = ("command", "file_path", "notebook_path")


def decide(payload):
    """Return (exit_code, message) for a hook payload."""
    if not isinstance(payload, dict):
        return 0, ""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0, ""
    for field in TEXT_FIELDS:
        value = tool_input.get(field)
        if isinstance(value, str) and STATE_FILE_NAME in value:
            return 2, (
                "[orchestrate] refused: this call touches the orchestrate plugin's hook state "
                "(%s). Only the user changes it, by typing /orch-hooks disengage or "
                "/orch-hooks engage." % STATE_FILE_NAME
            )
    return 0, ""


def main():
    try:
        payload = json.load(sys.stdin)
    except (OSError, ValueError) as error:
        sys.stderr.write("[orchestrate] protect_state.py could not read its input: %s\n" % error)
        return 0
    try:
        code, message = decide(payload)
    except Exception as error:  # noqa: BLE001 - a broken hook must never wedge a session
        sys.stderr.write("[orchestrate] protect_state.py failed: %r\n" % (error,))
        return 0
    if message:
        sys.stderr.write(message + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
