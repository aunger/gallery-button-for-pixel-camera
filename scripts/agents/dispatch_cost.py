#!/usr/bin/env python3
"""dispatch_cost.py: report a cycle's token spend from the harness transcripts.

The harness writes a per-call `usage` block for the orchestrating session and for
every sub-agent dispatch, but only for the life of the session. Measured while
investigating #1011, on 2026-09-03: the machine held exactly one project
directory, containing only the running session, and nothing from the 2026-09-01
cycle that #1011 is about. A cycle's cost can be measured while it runs and not
afterwards, which is the argument for a checked-in script rather than an ad-hoc
one: the data is gone by the time anyone thinks to ask.

Follows `scripts/agents/link_gh_issues.py` (issue #1000) and
`scripts/agents/update_gh_labels.sh` (issue #710): standard library only, no
`gh` CLI.

Layout read
-----------

    {root}/{project}/{session}.jsonl                       the orchestrator
    {root}/{project}/{session}/subagents/agent-{id}.jsonl  one per dispatch

`{root}` defaults to `~/.claude/projects` and is overridable with
`--projects-root`. Records that carry no `message.usage` (user turns, tool
attachments) are not API calls and are skipped.

One API call is one `message.id`
--------------------------------

A single assistant message spans several records, one per content block, and
each repeats its `usage`. Summing records inflated one dispatch from 20 calls to
53 in #1011, and every total with it, so records are grouped by `message.id` and
each group is counted once.

The input side of a group (`input_tokens`, `cache_creation_input_tokens`,
`cache_read_input_tokens`) is identical on every record of the group, and this
script fails rather than guess if it ever is not.

`output_tokens` is the exception: it grows across the group as the response
streams, so output is taken as the maximum over the group, never the first
record's value.

Output is a floor, and the script says by how much
--------------------------------------------------

The final `output_tokens` is written only on a record whose `stop_reason` is
set. Where a message has no such record the transcript keeps whatever partial
count the response had streamed so far, and no total for it exists on disk.
Measured over the 947 calls present on 2026-09-06: 320 recorded a final count
and averaged 557 output tokens, while the other 627 averaged 7.

Taking the first record's value instead of the maximum would have lost even the
320. Nothing here extrapolates over the rest: the report states how many calls
recorded a final count, so that output, and the units and shares computed from
it, are read as the lower bounds they are.

This is the same limit #1011 reported for output measured from text length,
reached from the other side.

Units, and why reads are reported separately
--------------------------------------------

Cache reads were 44% to 79% of measured spend across the nine dispatches in
#1011. A total that folds them into new tokens, or omits them, is not a cost.
The columns keep them apart, and `units` weights each component by its published
ratio to the base input price:

    uncached input   1.0x     cache read        0.1x
    5-minute write   1.25x    1-hour write      2.0x
    output           5.0x

These are ratios, not prices: the script has no price table and fetches nothing.
Each is overridable (`--read-multiplier` and friends) for a model whose ratios
differ. Writes are split by TTL from `usage.cache_creation`, because a 1-hour
write costs 1.6x what the same tokens cost at the 5-minute rate.

Full re-writes
--------------

A dispatch that goes idle past the cache TTL re-writes its whole accumulated
context at the write rate before doing any work. One such call in #1011 cost
about 109,000 units against about 11,600 for a warm one, and was over a tenth of
that dispatch's spend. It is invisible in any total that does not break out
cache writes per call, so a call that writes at least `--rewrite-fraction` of its
own context is flagged, and reported with the gap that preceded it.

That gap is the call's first record minus the previous call's last record. It
therefore spans the tools that ran in between plus the new call's own time to
first token, and is an upper bound on how long the cache entry sat unread.

Live sessions
-------------

Figures taken from a running session move as it runs. The snapshot time is
printed, and a session whose last record is within `--live-window` seconds of it
is marked `*`: its row is a lower bound, not a total.

Reading fails one transcript at a time
--------------------------------------

An unrecognised shape is named against the file and line that carries it, and
never absorbed into a zero. But it sets aside only its own transcript: the other
sessions are still reported, with the skip named in the report and on stderr.

That matters because the measurement cannot be retaken tomorrow. Aborting the
run would let the least interesting session on the machine veto the measurement
at the one moment it is possible, and a `{session}.jsonl` whose first assistant
turn has not landed yet is an ordinary few seconds in a session's life rather
than a corrupt file.

A final line that does not parse is treated the same way, one step smaller. A
transcript being appended to by a live session can be read mid-write, so the
last line is dropped with a note and the rest of that transcript is kept.

`--json` carries the skips under `unreadable`, for a caller that needs to know
the report is partial without reading stderr.

Usage
-----

    scripts/agents/dispatch_cost.py
    scripts/agents/dispatch_cost.py --calls
    scripts/agents/dispatch_cost.py --session 36ae78d3-dea1-56af-b5c6-5cc3d62970a2
    scripts/agents/dispatch_cost.py --json

Exit codes:
    0   a breakdown was printed, and any transcript set aside is named in it
    1   nothing could be read: no project, no transcript, or every transcript
        set aside (the reason is reported on stderr)
"""

import argparse
import dataclasses
import glob
import json
import os
import sys
from datetime import datetime, timezone

READ_MULTIPLIER = 0.1
WRITE_5M_MULTIPLIER = 1.25
WRITE_1H_MULTIPLIER = 2.0
OUTPUT_MULTIPLIER = 5.0

REWRITE_FRACTION = 0.5
LIVE_WINDOW_SECONDS = 300.0

DEFAULT_PROJECTS_ROOT = os.path.join("~", ".claude", "projects")


class TranscriptError(Exception):
    """A transcript did not have a shape this script knows how to read."""


def fail(path: str, lineno: int | None, message: str) -> None:
    """Raise with the file and line that could not be read.

    Every unrecognised shape comes through here, so that a harness change is
    reported against the record that changed rather than absorbed into a zero.
    """
    where = path if lineno is None else "%s:%d" % (path, lineno)
    raise TranscriptError("%s: %s" % (where, message))


@dataclasses.dataclass(frozen=True)
class Multipliers:
    """Price of each token class as a ratio of the base input price."""

    read: float = READ_MULTIPLIER
    write_5m: float = WRITE_5M_MULTIPLIER
    write_1h: float = WRITE_1H_MULTIPLIER
    output: float = OUTPUT_MULTIPLIER


@dataclasses.dataclass
class Call:
    """One API call: the records of one `message.id`, collapsed."""

    index: int
    message_id: str
    started: datetime
    ended: datetime
    input_tokens: int
    write_5m: int
    write_1h: int
    read: int
    output: int
    final_output: bool
    gap_before: float | None = None

    @property
    def writes(self) -> int:
        return self.write_5m + self.write_1h

    @property
    def context(self) -> int:
        """The prompt this call sat on: everything charged on the input side."""
        return self.input_tokens + self.writes + self.read

    def units(self, multipliers: Multipliers) -> float:
        return (
            self.input_tokens
            + self.read * multipliers.read
            + self.write_5m * multipliers.write_5m
            + self.write_1h * multipliers.write_1h
            + self.output * multipliers.output
        )

    def is_rewrite(self, fraction: float) -> bool:
        """True when this call re-wrote at least `fraction` of its own context.

        A warm call appends: it writes the few thousand tokens added since the
        last one and reads the rest. A call whose cache entry expired writes
        nearly all of it back.
        """
        return self.context > 0 and self.writes >= fraction * self.context


@dataclasses.dataclass
class Session:
    """One transcript: the orchestrating session, or one dispatch."""

    label: str
    role: str
    path: str
    calls: list
    notes: list = dataclasses.field(default_factory=list)

    @property
    def last_activity(self) -> datetime:
        return self.calls[-1].ended

    @property
    def context(self) -> int:
        """Context the last call sat on."""
        return self.calls[-1].context

    def total(self, attribute: str) -> int:
        return sum(getattr(call, attribute) for call in self.calls)

    def units(self, multipliers: Multipliers) -> float:
        return sum(call.units(multipliers) for call in self.calls)

    def rewrites(self, fraction: float) -> list:
        return [call for call in self.calls if call.is_rewrite(fraction)]

    def is_live(self, snapshot: datetime, window: float) -> bool:
        return (snapshot - self.last_activity).total_seconds() <= window


def parse_timestamp(value, path: str, lineno: int) -> datetime:
    if not isinstance(value, str) or not value:
        fail(path, lineno, "record has no `timestamp` string")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        fail(path, lineno, "`timestamp` is not an ISO 8601 instant: %r" % value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def read_int(mapping: dict, key: str, path: str, lineno: int, owner: str) -> int:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        fail(path, lineno, "%s has no integer `%s` (found %r)" % (owner, key, value))
    return value


def read_usage(usage: dict, path: str, lineno: int, assume_5m_writes: bool) -> tuple:
    """Pull the five token counts out of one `message.usage` block."""
    input_tokens = read_int(usage, "input_tokens", path, lineno, "usage")
    writes = read_int(usage, "cache_creation_input_tokens", path, lineno, "usage")
    read = read_int(usage, "cache_read_input_tokens", path, lineno, "usage")
    output = read_int(usage, "output_tokens", path, lineno, "usage")

    breakdown = usage.get("cache_creation")
    if isinstance(breakdown, dict):
        write_5m = read_int(breakdown, "ephemeral_5m_input_tokens", path, lineno, "cache_creation")
        write_1h = read_int(breakdown, "ephemeral_1h_input_tokens", path, lineno, "cache_creation")
        if write_5m + write_1h != writes:
            fail(
                path,
                lineno,
                "`cache_creation` sums to %d but `cache_creation_input_tokens` is %d"
                % (write_5m + write_1h, writes),
            )
    elif breakdown is not None:
        # `--assume-5m-writes` is not the remedy here: it covers an absent
        # breakdown, so naming it would send the reader in a circle.
        fail(path, lineno, "`cache_creation` is not an object (found %r)" % (breakdown,))
    elif writes == 0 or assume_5m_writes:
        # Nothing to split when nothing was written: zero tokens cost zero at
        # either TTL, so no assumption is being made and none can be wrong.
        write_5m, write_1h = writes, 0
    else:
        fail(
            path,
            lineno,
            "usage has no `cache_creation` breakdown by TTL for its %d written tokens; a "
            "1-hour write costs 1.6x a 5-minute one, so pass --assume-5m-writes to price "
            "every write at the 5-minute rate" % writes,
        )
    return input_tokens, write_5m, write_1h, read, output


def parse_line(path: str, lineno: int, line: str) -> dict:
    try:
        record = json.loads(line)
    except ValueError as error:
        fail(path, lineno, "line is not JSON: %s" % error)
    if not isinstance(record, dict):
        fail(path, lineno, "line is not a JSON object")
    return record


def iter_records(path: str, notes: list | None = None):
    """Yield `(lineno, record)` for each line of a transcript.

    The last line is held back and parsed only once the file has ended, because
    a transcript being appended to by a live session can be read mid-write. An
    unparsable final line is that incomplete append, so it is noted and dropped
    rather than failing the whole transcript; a malformed line anywhere else is
    a real corruption and fails.
    """
    held = None
    with open(path, encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            if held is not None:
                yield held[0], parse_line(path, held[0], held[1])
            held = (lineno, line)
    if held is None:
        return
    try:
        record = parse_line(path, held[0], held[1])
    except TranscriptError:
        if notes is None:
            raise
        notes.append("line %d was still being written and was skipped" % held[0])
        return
    yield held[0], record


def load_calls(path: str, assume_5m_writes: bool = False) -> tuple:
    """Collapse a transcript's records into one `Call` per `message.id`.

    Returns the calls and any notes about what could not be read but did not
    invalidate the rest.
    """
    groups = {}
    order = []
    notes = []
    for lineno, record in iter_records(path, notes):
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        usage = message.get("usage")
        if usage is None:
            continue
        if not isinstance(usage, dict):
            fail(path, lineno, "`message.usage` is not an object (found %r)" % (usage,))

        message_id = message.get("id")
        if not isinstance(message_id, str) or not message_id:
            fail(path, lineno, "record carries `usage` but no `message.id` to group it by")

        timestamp = parse_timestamp(record.get("timestamp"), path, lineno)
        input_tokens, write_5m, write_1h, read, output = read_usage(
            usage, path, lineno, assume_5m_writes
        )
        input_side = (input_tokens, write_5m, write_1h, read)

        # The final output count lands only on the record that completed the
        # message; elsewhere the transcript keeps a partial streaming count.
        final_output = message.get("stop_reason") is not None

        group = groups.get(message_id)
        if group is None:
            groups[message_id] = {
                "input_side": input_side,
                "started": timestamp,
                "ended": timestamp,
                "output": output,
                "final_output": final_output,
                "line": lineno,
            }
            order.append(message_id)
            continue
        if group["input_side"] != input_side:
            fail(
                path,
                lineno,
                "message %s reports input %r here and %r on line %d; the records of one "
                "message must agree on the input side"
                % (message_id, input_side, group["input_side"], group["line"]),
            )
        group["started"] = min(group["started"], timestamp)
        group["ended"] = max(group["ended"], timestamp)
        group["output"] = max(group["output"], output)
        group["final_output"] = group["final_output"] or final_output

    if not order:
        fail(path, None, "no record carries `message.usage`, so no API call could be read")

    seen_at = {message_id: position for position, message_id in enumerate(order)}
    order.sort(key=lambda message_id: (groups[message_id]["started"], seen_at[message_id]))
    calls = []
    for index, message_id in enumerate(order, 1):
        group = groups[message_id]
        input_tokens, write_5m, write_1h, read = group["input_side"]
        call = Call(
            index=index,
            message_id=message_id,
            started=group["started"],
            ended=group["ended"],
            input_tokens=input_tokens,
            write_5m=write_5m,
            write_1h=write_1h,
            read=read,
            output=group["output"],
            final_output=group["final_output"],
        )
        if calls:
            call.gap_before = (call.started - calls[-1].ended).total_seconds()
        calls.append(call)
    return calls, notes


def find_project(projects_root: str, project: str | None) -> str:
    root = os.path.expanduser(projects_root)
    if not os.path.isdir(root):
        raise TranscriptError("no projects directory at %s" % root)
    if project:
        chosen = os.path.join(root, project)
        if not os.path.isdir(chosen):
            raise TranscriptError("no project directory at %s" % chosen)
        return chosen
    candidates = sorted(
        entry.path for entry in os.scandir(root) if entry.is_dir() and _has_transcript(entry.path)
    )
    if not candidates:
        raise TranscriptError("no project under %s holds a transcript" % root)
    if len(candidates) > 1:
        raise TranscriptError(
            "%s holds %d projects; name one with --project: %s"
            % (root, len(candidates), ", ".join(os.path.basename(p) for p in candidates))
        )
    return candidates[0]


def _has_transcript(directory: str) -> bool:
    """True if the project holds an orchestrator transcript or any dispatch."""
    return bool(
        glob.glob(os.path.join(directory, "*.jsonl"))
        or glob.glob(os.path.join(directory, "*", "subagents", "agent-*.jsonl"))
    )


def _dispatch_paths(project_dir: str, session: str) -> list:
    return sorted(glob.glob(os.path.join(project_dir, session, "subagents", "agent-*.jsonl")))


def find_sessions(project_dir: str, session_id: str | None = None) -> tuple:
    """List each session's orchestrator transcript, then its dispatches.

    A session directory can outlive or precede its orchestrator transcript, and
    can hold harness scratch (`tool-results/`) rather than dispatches. So the
    sessions are the `*.jsonl` files, plus any directory that holds dispatches
    without one: those dispatches are real spend and are reported without an
    orchestrator row. A directory holding no dispatches is ignored.
    """
    transcripts = sorted(glob.glob(os.path.join(project_dir, "*.jsonl")))
    named = [os.path.basename(path)[: -len(".jsonl")] for path in transcripts]
    orphans = [
        entry.name
        for entry in sorted(os.scandir(project_dir), key=lambda entry: entry.name)
        if entry.is_dir() and entry.name not in named and _dispatch_paths(project_dir, entry.name)
    ]

    sessions = []
    missing = []
    for session in sorted(named + orphans):
        if session_id and session != session_id:
            continue
        if session in named:
            sessions.append(
                ("orchestrator", session, os.path.join(project_dir, session + ".jsonl"))
            )
        else:
            missing.append(session)
        for path in _dispatch_paths(project_dir, session):
            agent = os.path.basename(path)[len("agent-") : -len(".jsonl")]
            sessions.append(("dispatch", agent, path))

    if not sessions:
        where = "session %s in %s" % (session_id, project_dir) if session_id else project_dir
        raise TranscriptError("no transcript found for %s" % where)
    return sessions, missing


def load_sessions(project_dir: str, session_id: str | None, assume_5m_writes: bool) -> tuple:
    """Load every transcript, degrading one at a time rather than all at once.

    A transcript that cannot be read is named and set aside, and the rest are
    still reported. The measurement this script exists for cannot be retaken
    later, so the least interesting session on the machine must not be able to
    veto it: a `{session}.jsonl` whose first assistant turn has not landed yet
    is an ordinary few seconds in a session's life, not a corrupt file.
    """
    found, missing = find_sessions(project_dir, session_id)
    loaded = []
    unreadable = []
    for role, label, path in found:
        try:
            calls, notes = load_calls(path, assume_5m_writes)
        except TranscriptError as error:
            unreadable.append({"label": label, "role": role, "path": path, "reason": str(error)})
            continue
        loaded.append(Session(label, role, path, calls, notes))
    return loaded, missing, unreadable


def session_summary(
    session: Session,
    multipliers: Multipliers,
    fraction: float,
    snapshot: datetime,
    window: float,
) -> dict:
    tokens = {
        "input": session.total("input_tokens"),
        "cache_read": session.total("read"),
        "cache_write_5m": session.total("write_5m"),
        "cache_write_1h": session.total("write_1h"),
        "output": session.total("output"),
    }
    units = {
        "input": float(tokens["input"]),
        "cache_read": tokens["cache_read"] * multipliers.read,
        "cache_write": (
            tokens["cache_write_5m"] * multipliers.write_5m
            + tokens["cache_write_1h"] * multipliers.write_1h
        ),
        "output": tokens["output"] * multipliers.output,
    }
    units["total"] = sum(units.values())
    return {
        "label": session.label,
        "role": session.role,
        "path": session.path,
        "live": session.is_live(snapshot, window),
        "last_activity": session.last_activity.isoformat().replace("+00:00", "Z"),
        "calls": len(session.calls),
        "calls_with_final_output": sum(1 for call in session.calls if call.final_output),
        "notes": list(session.notes),
        "context": session.context,
        "tokens": tokens,
        "units": units,
        "shares": {
            key: (units[key] / units["total"] if units["total"] else 0.0)
            for key in ("input", "cache_read", "cache_write", "output")
        },
        "rewrites": [
            {
                "call": call.index,
                "gap_before_s": call.gap_before,
                "context": call.context,
                "cache_write": call.writes,
                "cache_read": call.read,
                "units": call.units(multipliers),
            }
            for call in session.rewrites(fraction)
        ],
    }


def call_rows(session: Session, multipliers: Multipliers, fraction: float) -> list:
    return [
        {
            "call": call.index,
            "gap_before_s": call.gap_before,
            "context": call.context,
            "input": call.input_tokens,
            "cache_write": call.writes,
            "cache_write_1h": call.write_1h,
            "cache_read": call.read,
            "output": call.output,
            "final_output": call.final_output,
            "units": call.units(multipliers),
            "rewrite": call.is_rewrite(fraction),
        }
        for call in session.calls
    ]


def thousands(value: float) -> str:
    return "{:,}".format(int(round(value)))


def percent(value: float) -> str:
    return "%d%%" % round(value * 100)


def render_table(headers: list, rows: list, aligns: str) -> list:
    """Render one text table, sizing each column to its widest cell."""
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines = []
    for cells in [headers] + rows:
        rendered = [
            cell.ljust(widths[i]) if aligns[i] == "l" else cell.rjust(widths[i])
            for i, cell in enumerate(cells)
        ]
        lines.append("  ".join(rendered).rstrip())
    return lines


def render_report(
    summaries: list,
    multipliers: Multipliers,
    fraction: float,
    snapshot: datetime,
    window: float,
    project_dir: str,
    detail: list | None,
    missing: list = (),
    unreadable: list = (),
) -> list:
    live = [summary["label"] for summary in summaries if summary["live"]]
    lines = [
        "Project %s" % project_dir,
        "Snapshot %s" % snapshot.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "Units are base-input-equivalents: input 1x, cache read %gx, 5-minute write %gx, "
        "1-hour write %gx, output %gx."
        % (multipliers.read, multipliers.write_5m, multipliers.write_1h, multipliers.output),
    ]
    lines.append("`ctx` is the context the session's last call sat on, not a total.")
    if missing:
        lines.append(
            "No orchestrator transcript on disk for %s, so only its dispatches are counted."
            % ", ".join(missing)
        )
    if live:
        lines.append(
            "Sessions marked * wrote a record within %gs of the snapshot and were still "
            "live, so their rows are lower bounds: %s." % (window, ", ".join(live))
        )
    else:
        lines.append("No session wrote a record within %gs of the snapshot." % window)

    if unreadable:
        lines.append("")
        lines.append(
            "%d transcript%s could not be read and %s excluded from every figure below:"
            % (
                len(unreadable),
                "" if len(unreadable) == 1 else "s",
                "is" if len(unreadable) == 1 else "are",
            )
        )
        for entry in unreadable:
            lines.append("  %s (%s): %s" % (entry["label"], entry["role"], entry["reason"]))
    for summary in summaries:
        for note in summary["notes"]:
            lines.append("  %s (%s): %s" % (summary["label"], summary["role"], note))
    lines.append("")

    headers = [
        "session",
        "role",
        "calls",
        "ctx",
        "input",
        "reads",
        "writes",
        "output",
        "units",
        "read%",
        "write%",
        "out%",
    ]
    rows = []
    for summary in summaries:
        rows.append(
            [
                summary["label"] + ("*" if summary["live"] else ""),
                summary["role"],
                thousands(summary["calls"]),
                thousands(summary["context"]),
                thousands(summary["tokens"]["input"]),
                thousands(summary["tokens"]["cache_read"]),
                thousands(
                    summary["tokens"]["cache_write_5m"] + summary["tokens"]["cache_write_1h"]
                ),
                thousands(summary["tokens"]["output"]),
                thousands(summary["units"]["total"]),
                percent(summary["shares"]["cache_read"]),
                percent(summary["shares"]["cache_write"]),
                percent(summary["shares"]["output"]),
            ]
        )
    rows.append(_totals_row(summaries))
    lines.extend(render_table(headers, rows, "llrrrrrrrrrr"))

    calls = sum(summary["calls"] for summary in summaries)
    final = sum(summary["calls_with_final_output"] for summary in summaries)
    lines.append("")
    lines.append(
        "Output is a floor: %s of %s calls recorded a final `output_tokens`. On the rest "
        "the transcript holds only the count the response had streamed when it was last "
        "written, so output, units and out%% under-report." % (thousands(final), thousands(calls))
    )

    rewriting = [summary for summary in summaries if summary["rewrites"]]
    lines.append("")
    if rewriting:
        lines.append(
            "Full re-writes (a call writing at least %g%% of its own context) and the gap "
            "before each, an upper bound on how long its cache entry sat unread:" % (fraction * 100)
        )
        rewrite_rows = []
        for summary in rewriting:
            for rewrite in summary["rewrites"]:
                rewrite_rows.append(
                    [
                        summary["label"],
                        thousands(rewrite["call"]),
                        "-"
                        if rewrite["gap_before_s"] is None
                        else "%.0f" % rewrite["gap_before_s"],
                        thousands(rewrite["context"]),
                        thousands(rewrite["cache_write"]),
                        thousands(rewrite["cache_read"]),
                        thousands(rewrite["units"]),
                    ]
                )
        lines.extend(
            render_table(
                ["session", "call", "gap_s", "ctx", "write", "read", "units"],
                rewrite_rows,
                "lrrrrrr",
            )
        )
    else:
        lines.append("No call wrote at least %g%% of its own context." % (fraction * 100))

    if detail:
        for summary, session in detail:
            lines.append("")
            lines.append("%s (%s), per call:" % (summary["label"], summary["role"]))
            rows = []
            for row in session:
                notes = []
                if row["rewrite"]:
                    notes.append("re-write")
                if not row["final_output"]:
                    notes.append("output partial")
                rows.append(
                    [
                        thousands(row["call"]),
                        "-" if row["gap_before_s"] is None else "%.0f" % row["gap_before_s"],
                        thousands(row["context"]),
                        thousands(row["input"]),
                        thousands(row["cache_write"]),
                        thousands(row["cache_read"]),
                        thousands(row["output"]),
                        thousands(row["units"]),
                        ", ".join(notes),
                    ]
                )
            lines.extend(
                render_table(
                    ["call", "gap_s", "ctx", "input", "write", "read", "output", "units", "notes"],
                    rows,
                    "rrrrrrrrl",
                )
            )
    return lines


def _totals_row(summaries: list) -> list:
    def total(*keys: str) -> float:
        running = 0
        for summary in summaries:
            value = summary
            for key in keys:
                value = value[key]
            running += value
        return running

    units = total("units", "total")
    reads = total("units", "cache_read")
    writes = total("units", "cache_write")
    output = total("units", "output")
    return [
        "TOTAL",
        "%d sessions" % len(summaries),
        thousands(total("calls")),
        "-",
        thousands(total("tokens", "input")),
        thousands(total("tokens", "cache_read")),
        thousands(total("tokens", "cache_write_5m") + total("tokens", "cache_write_1h")),
        thousands(total("tokens", "output")),
        thousands(units),
        percent(reads / units if units else 0),
        percent(writes / units if units else 0),
        percent(output / units if units else 0),
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report per-dispatch token spend from the harness transcripts.",
        epilog="Transcripts live only for the life of the session, so run this while the "
        "cycle being measured is still on disk.",
    )
    parser.add_argument(
        "--projects-root",
        default=DEFAULT_PROJECTS_ROOT,
        help="directory holding one subdirectory per project (default: %(default)s)",
    )
    parser.add_argument(
        "--project",
        help="project directory name, when the root holds several. A project name is the "
        "project's path with the separators replaced, so it starts with `-` and has to be "
        "given as --project=NAME rather than --project NAME",
    )
    parser.add_argument("--session", help="report only this session and its dispatches")
    parser.add_argument(
        "--calls", action="store_true", help="also print the per-call breakdown of every session"
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of tables")
    parser.add_argument(
        "--live-window",
        type=float,
        default=LIVE_WINDOW_SECONDS,
        help="seconds since a session's last record within which it counts as live "
        "(default: %(default)s)",
    )
    parser.add_argument(
        "--rewrite-fraction",
        type=float,
        default=REWRITE_FRACTION,
        help="fraction of its own context a call must write to be flagged a full re-write "
        "(default: %(default)s)",
    )
    parser.add_argument(
        "--assume-5m-writes",
        action="store_true",
        help="price cache writes at the 5-minute rate when a record that wrote tokens "
        "carries no `cache_creation` breakdown by TTL, instead of setting that transcript "
        "aside. A record that wrote nothing needs no breakdown and never asks for this",
    )
    prices = parser.add_argument_group(
        "multipliers", "ratios to the base input price; this script has no price table"
    )
    prices.add_argument("--read-multiplier", type=float, default=READ_MULTIPLIER)
    prices.add_argument("--write-5m-multiplier", type=float, default=WRITE_5M_MULTIPLIER)
    prices.add_argument("--write-1h-multiplier", type=float, default=WRITE_1H_MULTIPLIER)
    prices.add_argument("--output-multiplier", type=float, default=OUTPUT_MULTIPLIER)
    return parser


def main(argv: list | None = None) -> int:
    args = build_parser().parse_args(argv)
    multipliers = Multipliers(
        read=args.read_multiplier,
        write_5m=args.write_5m_multiplier,
        write_1h=args.write_1h_multiplier,
        output=args.output_multiplier,
    )
    snapshot = datetime.now(timezone.utc)
    try:
        project_dir = find_project(args.projects_root, args.project)
        sessions, missing, unreadable = load_sessions(
            project_dir, args.session, args.assume_5m_writes
        )
    except TranscriptError as error:
        print("dispatch_cost: %s" % error, file=sys.stderr)
        return 1

    # Every unreadable transcript is named on stderr whether or not a report
    # reaches stdout, so a skip is never silent.
    for entry in unreadable:
        print("dispatch_cost: skipped %s" % entry["reason"], file=sys.stderr)
    if not sessions:
        print("dispatch_cost: no transcript could be read", file=sys.stderr)
        return 1

    summaries = [
        session_summary(session, multipliers, args.rewrite_fraction, snapshot, args.live_window)
        for session in sessions
    ]
    detail = None
    if args.calls:
        detail = [
            (summary, call_rows(session, multipliers, args.rewrite_fraction))
            for summary, session in zip(summaries, sessions)
        ]

    if args.json:
        payload = {
            "snapshot": snapshot.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "project": project_dir,
            "multipliers": dataclasses.asdict(multipliers),
            "rewrite_fraction": args.rewrite_fraction,
            "live_window_seconds": args.live_window,
            "missing_orchestrator_transcripts": missing,
            "unreadable": unreadable,
            "sessions": summaries,
        }
        if detail:
            for summary, rows in detail:
                summary["call_detail"] = rows
        print(json.dumps(payload, indent=2))
        return 0

    for line in render_report(
        summaries,
        multipliers,
        args.rewrite_fraction,
        snapshot,
        args.live_window,
        project_dir,
        detail,
        missing,
        unreadable,
    ):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
