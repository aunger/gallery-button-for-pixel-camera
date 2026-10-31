#!/usr/bin/env python3
"""hook_state.py: Disengage and re-engage the orchestrate plugin's hooks.

The plugin's two hooks (check_dispatch.py and orchestrator_guard.py) enforce
the Orchestrator's rules. Sometimes a person needs them out of the way: to
hand-dispatch an agent with a note, to let an Orchestrator session do other
work, or to recover from a cycle that went wrong. This script keeps the
state those hooks consult first, and the /orch_hooks skill drives it.

State is per session, in a file the plugin owns:

    <plugin root>/.state/disengaged-sessions

one session id per line. A hook whose payload carries a listed session_id
exits 0 before doing anything else. The file is not committed (the host
repository ignores .state/), and a session that never disengaged is never
listed, so a fresh session always has its hooks.

A second switch needs no skill: the environment variable
ORCHESTRATE_HOOKS_DISENGAGED, set to anything but an empty string, "0" or
"false", disengages the hooks for every session that sees it.

Usage:
    hook_state.py disengage --session SESSION_ID
    hook_state.py reengage --session SESSION_ID
    hook_state.py status [--session SESSION_ID]

Each command prints one line saying what the state now is.

Standard library only. The two hooks import hooks_disengaged() from here.
"""

import argparse
import os
import sys

ENV_VAR = "ORCHESTRATE_HOOKS_DISENGAGED"
STATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".state"
)
STATE_FILE = os.path.join(STATE_DIR, "disengaged-sessions")


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


def reengage(session_id, state_file=None):
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


def main(argv):
    parser = argparse.ArgumentParser(
        prog="hook_state.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("command", choices=("disengage", "reengage", "status"))
    parser.add_argument("--session", metavar="SESSION_ID", default="")
    parser.add_argument("--state-file", metavar="PATH", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv[1:])
    if args.command != "status" and not args.session:
        parser.error("%s needs --session SESSION_ID" % args.command)
    if args.command == "disengage":
        print(disengage(args.session, args.state_file))
    elif args.command == "reengage":
        print(reengage(args.session, args.state_file))
    else:
        print(status(args.session, args.state_file))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
