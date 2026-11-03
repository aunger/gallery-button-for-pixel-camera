#!/usr/bin/env python3
"""hook_state.py: Disengage and engage the orchestrate plugin's hooks.

The plugin's hooks (check_dispatch.py and orchestrator_guard.py) enforce the
Orchestrator's rules. Sometimes a person needs them out of the way: to
hand-dispatch an agent with a note, to let an Orchestrator session do other
work, or to recover from a cycle that went wrong. This module keeps the state
those hooks consult first.

State is per session, in a file the plugin owns:

    <plugin root>/.state/disengaged-sessions

one session id per line. A hook whose payload carries a listed session_id
exits 0 before doing anything else. The file is not committed (the host
repository ignores .state/), and a session that never disengaged is never
listed, so a fresh session always has its hooks.

Only a person changes the state. This script runs as the plugin's
UserPromptExpansion hook (hooks/hooks.json), which Claude Code fires when a
person types a command and never when the model invokes one: on
/orch-hooks disengage, engage or status it applies the change to the session
in its payload and hands the resulting line to the skill as context. It has
no command line that changes anything, and protect_state.py refuses any tool
call that names the state file, so the model has no direct path to it either.
That last check reads the command text, so it is a guardrail, not a sandbox.

A second switch needs no skill: the environment variable
ORCHESTRATE_HOOKS_DISENGAGED, set to anything but an empty string, "0" or
"false", disengages the hooks for every session whose Claude Code process
has it. A command the model runs cannot set it for the hooks, which inherit
Claude Code's environment, not the shell's.

Standard library only. The hooks import hooks_disengaged() from here.
"""

import json
import os
import sys

ENV_VAR = "ORCHESTRATE_HOOKS_DISENGAGED"
STATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".state"
)
STATE_FILE_NAME = "disengaged-sessions"
STATE_FILE = os.path.join(STATE_DIR, STATE_FILE_NAME)

SKILL_NAME = "orch-hooks"
COMMANDS = ("disengage", "engage", "status")


def env_disengaged(environ=None):
    """Return True when the environment variable switches the hooks off."""
    value = (environ if environ is not None else os.environ).get(ENV_VAR, "")
    return value.strip().lower() not in ("", "0", "false")


def _state_file(state_file):
    """Resolve the state file at call time, so a patched STATE_FILE is honored."""
    return STATE_FILE if state_file is None else state_file


def listed_sessions(state_file=None):
    """Return the session ids the state file lists, in order, without duplicates."""
    try:
        with open(_state_file(state_file), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    seen = []
    for line in lines:
        line = line.strip()
        if line and line not in seen:
            seen.append(line)
    return seen


def write_sessions(sessions, state_file=None):
    state_file = _state_file(state_file)
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(state_file, "w", encoding="utf-8") as handle:
        handle.writelines(session + "\n" for session in sessions)


def hooks_disengaged(session_id, state_file=None, environ=None):
    """Return True when a hook should exit early for this session.

    Either the environment variable is set, or the session id is listed in
    the state file. A hook with no session id in its payload is governed by
    the environment variable alone.
    """
    if env_disengaged(environ):
        return True
    if not session_id:
        return False
    return session_id in listed_sessions(state_file)


def disengage(session_id, state_file=None):
    sessions = listed_sessions(state_file)
    if session_id not in sessions:
        sessions.append(session_id)
        write_sessions(sessions, state_file)
    return "orchestrate hooks disengaged for session %s" % session_id


def engage(session_id, state_file=None):
    sessions = listed_sessions(state_file)
    if session_id in sessions:
        sessions.remove(session_id)
        write_sessions(sessions, state_file)
    return "orchestrate hooks engaged for session %s" % session_id


def status(session_id, state_file=None, environ=None):
    if env_disengaged(environ):
        return "orchestrate hooks disengaged for every session by %s" % ENV_VAR
    if not session_id:
        return "orchestrate hooks engaged unless a session is listed in %s" % _state_file(
            state_file
        )
    if session_id in listed_sessions(state_file):
        return "orchestrate hooks disengaged for session %s" % session_id
    return "orchestrate hooks engaged for session %s" % session_id


def is_skill_command(command_name):
    """Return True for the command names /orch-hooks can arrive under.

    A plugin skill reports its full `plugin:skill` name, and a bare name
    invokes it too while nothing else claims it, so the last segment decides.
    """
    return isinstance(command_name, str) and command_name.split(":")[-1] == SKILL_NAME


def expansion_output(payload, state_file=None, environ=None):
    """Return the JSON object the UserPromptExpansion hook prints, or None to stay silent."""
    if not isinstance(payload, dict) or not is_skill_command(payload.get("command_name")):
        return None
    words = str(payload.get("command_args") or "").split()
    usage = "Usage: /%s %s" % (SKILL_NAME, " | ".join(COMMANDS))
    if len(words) != 1 or words[0] not in COMMANDS:
        return {"decision": "block", "reason": usage}
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return {
            "decision": "block",
            "reason": "The hook received no session id, so it changed nothing.",
        }
    command = words[0]
    if command == "disengage":
        line = disengage(session_id, state_file)
    elif command == "engage":
        line = engage(session_id, state_file)
    else:
        line = status(session_id, state_file, environ)
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptExpansion",
            "additionalContext": "orch-hooks result: " + line,
        }
    }


def main():
    try:
        payload = json.load(sys.stdin)
    except (OSError, ValueError) as error:
        sys.stderr.write("[orchestrate] hook_state.py could not read its input: %s\n" % error)
        return 0
    try:
        output = expansion_output(payload)
    except Exception as error:  # noqa: BLE001 - a broken hook must never wedge a session
        sys.stderr.write("[orchestrate] hook_state.py failed: %r\n" % (error,))
        return 0
    if output is not None:
        print(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
