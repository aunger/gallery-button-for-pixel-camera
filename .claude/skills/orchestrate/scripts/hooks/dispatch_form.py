#!/usr/bin/env python3
"""dispatch_form.py: Validate the orchestrate plugin's dispatch form.

The form is the only text the Orchestrator sends a sub-agent; see
reference/dispatch-form.md for the rules and the reasoning. This module
holds the one validator both hooks use: check_dispatch.py (every Agent call
to a plugin agent) and orchestrator_guard.py (every SendMessage the main
session sends while /orchestrate is active).

Standard library only, so the hooks never fail on a missing dependency.
This module has no `main`; it is imported, not run directly.
"""

import re

# The plugin's agents, as the scoped `subagent_type` an Agent call names them by.
PLUGIN_NAME = "orchestrate"
AGENT_NAMES = ("programmer", "reviewer", "verification-planner", "verifier")
PLUGIN_AGENT_TYPES = frozenset("%s:%s" % (PLUGIN_NAME, name) for name in AGENT_NAMES)

# One (label, pattern) per line of the form, in order. A pattern matches the
# whole value after "Label: ".
_NUMBER_OR_NONE = r"#[0-9]+|None"
FORM_LINES = (
    ("Repository", r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+"),
    ("Issue", _NUMBER_OR_NONE),
    ("PR", _NUMBER_OR_NONE),
    ("Branch", r"None|[^\s]+"),
    ("Verification plan comment", r"[0-9]+|None"),
)

FORM_TEMPLATE = "\n".join(
    "%s: %s"
    % (
        label,
        {
            "Repository": "{owner}/{repo}",
            "Issue": "#{number} or None",
            "PR": "#{number} or None",
            "Branch": "{branch name} or None",
            "Verification plan comment": "{numeric comment id} or None",
        }[label],
    )
    for label, _ in FORM_LINES
)


def form_errors(text):
    """Return the ways `text` departs from the form; an empty list means it is the form.

    Leading and trailing blank lines are tolerated, as is trailing whitespace
    on a line. Nothing else is: not a sixth line, not a missing one, not a
    different order, not a value the line's pattern rejects.
    """
    if not isinstance(text, str):
        return ["the prompt is not text"]
    lines = [line.rstrip() for line in text.strip("\n").splitlines()]
    errors = []
    if len(lines) != len(FORM_LINES):
        errors.append(
            "the form has exactly %d lines; this prompt has %d" % (len(FORM_LINES), len(lines))
        )
    for index, (label, pattern) in enumerate(FORM_LINES):
        if index >= len(lines):
            errors.append("line %d is missing: %s: ..." % (index + 1, label))
            continue
        line = lines[index]
        prefix = label + ": "
        if not line.startswith(prefix):
            errors.append("line %d must begin with %r; got %r" % (index + 1, prefix, line))
            continue
        value = line[len(prefix) :]
        if not re.fullmatch(pattern, value):
            errors.append("line %d: %r is not a valid %s value" % (index + 1, value, label))
    for index in range(len(FORM_LINES), len(lines)):
        errors.append("line %d is not part of the form: %r" % (index + 1, lines[index]))
    return errors


def refusal(what, errors):
    """Return the message a hook prints when it refuses `what`."""
    return "\n".join(
        [
            "[orchestrate] refused: %s is not the dispatch form." % what,
            "A sub-agent of the orchestrate plugin receives the five-line form and nothing else;",
            "everything it needs beyond these facts is on GitHub, where it reads it itself.",
            "",
        ]
        + ["- " + error for error in errors]
        + ["", "The form:", FORM_TEMPLATE]
    )
