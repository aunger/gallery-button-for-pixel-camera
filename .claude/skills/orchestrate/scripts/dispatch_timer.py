#!/usr/bin/env python3
"""dispatch_timer.py--Report sub-agent dispatch timing for the /orchestrate skill.

The Delegation rules in agents/dev_orchestration.md require the Orchestrator to
record `date -u` immediately before dispatching a sub-agent and immediately
after it returns, then report both times and the elapsed duration to the user.

This helper formats those two timestamps consistently and computes the elapsed
wall-clock duration, so the Orchestrator does not have to do arithmetic by hand
(which is error-prone across minute and hour boundaries).

Usage:
    dispatch_timer.py mark
        Print the current UTC time in the canonical format.

    dispatch_timer.py report --start <ts> --end <ts>
        Print a one-line timing report for a dispatch that started at <ts> and
        ended at <ts>. Timestamps must be in the canonical format produced by
        `mark` (an ISO-8601 UTC instant, e.g. 2026-06-01T15:24:07Z).

Exit status:
    0  success
    2  usage or parse error
"""

import argparse
import datetime as _dt
import sys

CANONICAL_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def now_utc():
    """Return the current time as a timezone-aware UTC datetime."""
    return _dt.datetime.now(_dt.timezone.utc)


def format_instant(instant):
    """Format a timezone-aware datetime in the canonical UTC format."""
    return instant.astimezone(_dt.timezone.utc).strftime(CANONICAL_FORMAT)


def parse_instant(text):
    """Parse a canonical-format timestamp into a UTC datetime.

    Raises ValueError on anything that does not match the canonical format.
    """
    parsed = _dt.datetime.strptime(text, CANONICAL_FORMAT)
    return parsed.replace(tzinfo=_dt.timezone.utc)


def format_elapsed(delta):
    """Format a timedelta as Hh Mm Ss, omitting leading zero units."""
    total = int(delta.total_seconds())
    if total < 0:
        raise ValueError("end is before start")
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if hours:
        parts.append("{}h".format(hours))
    if minutes or hours:
        parts.append("{}m".format(minutes))
    parts.append("{}s".format(seconds))
    return " ".join(parts)


def build_report(start_text, end_text):
    """Return the one-line timing report string for two canonical timestamps."""
    start = parse_instant(start_text)
    end = parse_instant(end_text)
    elapsed = format_elapsed(end - start)
    return "dispatch: start {} end {} elapsed {}".format(start_text, end_text, elapsed)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="dispatch_timer.py", add_help=True)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("mark", help="Print the current UTC time in canonical format.")

    report = sub.add_parser("report", help="Print a timing report for a dispatch.")
    report.add_argument("--start", required=True, help="Canonical start timestamp.")
    report.add_argument("--end", required=True, help="Canonical end timestamp.")

    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2

    if args.command == "mark":
        print(format_instant(now_utc()))
        return 0

    if args.command == "report":
        try:
            print(build_report(args.start, args.end))
        except ValueError as exc:
            print("dispatch_timer.py: {}".format(exc), file=sys.stderr)
            return 2
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
