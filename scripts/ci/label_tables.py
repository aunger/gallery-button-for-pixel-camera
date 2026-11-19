#!/usr/bin/env python3
"""label_tables.py: The label tables this repository's workflows pass to vcs-automation.

The label workflows call aunger/vcs-automation's label actions and give each
its tables as `with:` inputs, in JSON. Several actions read the same table, so
the workflows hold more than one copy of it, and nothing but a test keeps the
copies in step. This module finds those steps and parses their tables:

- `label_steps()`: every step in the workflows that uses one of
  `LABEL_ACTIONS`, as a `LabelStep`.
- `steps_by_action()`: the one step per action, or `LabelTableError` when an
  action has none or more than one.
- `tables()`: each action's table inputs parsed from JSON, a missing optional
  input read as its default, `[]`, and a missing required one an error.
- `shared_table_problems()`: what differs between the copies of a shared
  table, as messages naming both workflows.

Label names are compared after `strip().lower()`, as the actions compare them.

Imported by bare module name, like workflow_files.py beside it.
This module has no `main`; it is imported, not run directly.
"""

import json
import re
from dataclasses import dataclass

from workflow_files import load_workflow, relative, workflow_paths

TITLE = "label-by-title"
FILES = "label-by-files"
ENFORCE = "enforce-mutually-exclusive-labels"
GATE = "check-blocking-labels"
SNOOZE = "snooze-issues"
WAKE = "wake-snoozed-issues"
PROPAGATE = "propagate-issue-labels"
RECONCILE = "reconcile-issue-labels"

# Each label action's inputs that hold a JSON table, mapped to whether the
# action requires it. Its other inputs (github-token, and check-blocking-labels'
# removed-labels, which carries the enforce step's output) are not tables.
_EXCLUSIVE = {"exclusive-sets": False, "exclusive-prefixes": False}
TABLE_INPUTS: dict[str, dict[str, bool]] = {
    TITLE: {"rules": True, "skip-on-pull-requests": False},
    FILES: {"rules": True},
    ENFORCE: dict(_EXCLUSIVE),
    GATE: {"blocking-labels": True},
    SNOOZE: {"snooze-labels": True},
    WAKE: {"snooze-labels": True, "remove-on-wake": False},
    PROPAGATE: {"excluded-labels": False, **_EXCLUSIVE},
    RECONCILE: {"excluded-labels": False, **_EXCLUSIVE},
}
LABEL_ACTIONS = tuple(TABLE_INPUTS)

# The default every optional table input has in its action.yml.
OPTIONAL_DEFAULT = "[]"

_USES = re.compile(r"aunger/vcs-automation/(?P<action>[A-Za-z0-9_.-]+)@(?P<ref>\S+)")


class LabelTableError(ValueError):
    """A label step is missing, repeated, or has a table that cannot be read."""


@dataclass(frozen=True)
class LabelStep:
    """One workflow step that uses a vcs-automation label action."""

    workflow: str
    job: str
    action: str
    ref: str
    inputs: dict[str, str]


def label_steps(paths: list[str] | None = None) -> list[LabelStep]:
    """Return every step in *paths* (default: every workflow) that uses a label action."""
    found = []
    for path in workflow_paths() if paths is None else paths:
        workflow = load_workflow(path)
        for job_id, job in (workflow.get("jobs") or {}).items():
            for step in (job or {}).get("steps") or []:
                match = _USES.fullmatch(str(step.get("uses", "")).strip())
                if not match or match.group("action") not in TABLE_INPUTS:
                    continue
                inputs = {
                    str(key): "" if value is None else str(value)
                    for key, value in (step.get("with") or {}).items()
                }
                found.append(
                    LabelStep(
                        relative(path), job_id, match.group("action"), match.group("ref"), inputs
                    )
                )
    return found


def steps_by_action(steps: list[LabelStep]) -> dict[str, LabelStep]:
    """Return {action: its one step}; raise LabelTableError unless each action has exactly one."""
    by_action: dict[str, list[LabelStep]] = {action: [] for action in LABEL_ACTIONS}
    for step in steps:
        by_action[step.action].append(step)
    problems = [
        "%s is used by %d steps (%s); expected one"
        % (action, len(found), ", ".join(step.workflow for step in found) or "none")
        for action, found in by_action.items()
        if len(found) != 1
    ]
    if problems:
        raise LabelTableError("; ".join(problems))
    return {action: found[0] for action, found in by_action.items()}


def table(step: LabelStep, name: str) -> object:
    """Return *step*'s table input *name* parsed from JSON.

    A missing optional input is its default, `[]`; a missing required one, or
    one that is not JSON, raises LabelTableError.
    """
    required = TABLE_INPUTS[step.action][name]
    text = step.inputs.get(name, "")
    if not text.strip():
        if required:
            raise LabelTableError(f"{step.workflow}: {step.action} needs {name}")
        text = OPTIONAL_DEFAULT
    try:
        return json.loads(text)
    except ValueError as error:
        raise LabelTableError(f"{step.workflow}: {name} is not JSON: {error}") from error


@dataclass(frozen=True)
class Tables:
    """One action's parsed table inputs, and the workflow they came from."""

    workflow: str
    inputs: dict[str, object]


def tables(steps: dict[str, LabelStep]) -> dict[str, Tables]:
    """Return {action: its parsed tables} for the steps steps_by_action returned."""
    return {
        action: Tables(step.workflow, {name: table(step, name) for name in TABLE_INPUTS[action]})
        for action, step in steps.items()
    }


def names(value: object) -> frozenset[str]:
    """Return a JSON array of label names (or prefixes) as a normalized set."""
    return frozenset(str(item).strip().lower() for item in value)


def label_sets(value: object) -> frozenset[frozenset[str]]:
    """Return a JSON array of label sets as a normalized set of sets."""
    return frozenset(names(member) for member in value)


def snooze_days(value: object) -> dict[str, object]:
    """Return a snooze-labels object with its labels normalized."""
    return {str(label).strip().lower(): days for label, days in value.items()}


def any_file_labels(rules: object) -> frozenset[str]:
    """Return the labels label-by-files' *rules* decide with an any-file rule."""
    return names(label for label, rule in rules.items() if rule.get("mode") == "any-file")


def _differ(found: dict[str, Tables], name: str, actions: tuple[str, ...], normalize) -> list[str]:
    """Return a message for each action whose *name* table differs from the first action's."""
    first, *others = actions
    want = normalize(found[first].inputs[name])
    problems = []
    for action in others:
        got = normalize(found[action].inputs[name])
        if got != want:
            problems.append(
                f"{name} differs: {found[first].workflow} ({first}) gives {_show(want)}, "
                f"{found[action].workflow} ({action}) gives {_show(got)}"
            )
    return problems


def _show(value: object) -> str:
    """Return *value* sorted where it is a set, so a message reads the same each run."""
    if isinstance(value, frozenset):
        return repr(sorted(_show(item) if isinstance(item, frozenset) else item for item in value))
    return repr(value)


def shared_table_problems(found: dict[str, Tables]) -> list[str]:
    """Return a message for each way the copies of a shared table disagree.

    - The exclusive sets and prefixes are the same for enforcement, propagation
      and reconciliation.
    - Propagation and reconciliation exclude the same labels.
    - Snoozing and waking read the same snooze labels, which are exactly one of
      enforcement's sets, so an issue carries at most one.
    - The labels a woken issue loses are the ones propagation and
      reconciliation exclude, less label-by-files' any-file labels.
    - Those any-file labels are label-by-title's skip-on-pull-requests, and
      propagation and reconciliation exclude every one of them.
    """
    propagating = (PROPAGATE, RECONCILE)
    problems = []
    problems += _differ(found, "exclusive-sets", (ENFORCE, *propagating), label_sets)
    problems += _differ(found, "exclusive-prefixes", (ENFORCE, *propagating), names)
    problems += _differ(found, "excluded-labels", propagating, names)
    problems += _differ(found, "snooze-labels", (SNOOZE, WAKE), snooze_days)

    snooze = frozenset(snooze_days(found[SNOOZE].inputs["snooze-labels"]))
    if snooze not in label_sets(found[ENFORCE].inputs["exclusive-sets"]):
        problems.append(
            f"snooze-labels in {found[SNOOZE].workflow} ({_show(snooze)}) are not exactly "
            f"one of the exclusive-sets in {found[ENFORCE].workflow}"
        )

    any_file = any_file_labels(found[FILES].inputs["rules"])
    remove_on_wake = names(found[WAKE].inputs["remove-on-wake"])
    for action in propagating:
        excluded = names(found[action].inputs["excluded-labels"])
        if remove_on_wake != excluded - any_file:
            problems.append(
                f"remove-on-wake in {found[WAKE].workflow} ({_show(remove_on_wake)}) is not "
                f"the excluded-labels of {found[action].workflow} less the any-file labels "
                f"({_show(excluded - any_file)})"
            )
        if not any_file <= excluded:
            problems.append(
                f"excluded-labels in {found[action].workflow} lacks the any-file labels of "
                f"{found[FILES].workflow}: {_show(any_file - excluded)}"
            )

    skip = names(found[TITLE].inputs["skip-on-pull-requests"])
    if skip != any_file:
        problems.append(
            f"skip-on-pull-requests in {found[TITLE].workflow} ({_show(skip)}) is not the "
            f"any-file labels of {found[FILES].workflow} ({_show(any_file)})"
        )
    return problems
