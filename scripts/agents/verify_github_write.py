#!/usr/bin/env python3
"""verify_github_write.py: Read back a GitHub write and diff it against what was sent.

Text an agent posts to GitHub is sometimes different once it is stored, and
nothing reports the difference (issue #909).  Three alteration behaviors are
known and no two share a profile: middle dots injected into at-sign mentions,
angle-bracket constructs removed, and an attribution footer appended that was
never sent.  They disagree on direction, on write path, and on which construct
they touch, so no pattern rule generalizes across them.

A comparison of sent text against stored text is indifferent to all three.  It
needs no prediction about which path alters what, and it catches a fourth
behavior nobody has characterized yet.  That is the whole of the design.

This module is the checker behind the `PostToolUse` hook
`.claude/hooks/post-tool-use-github-readback.sh`, which is wired in
`.claude/settings.json` with the matcher `mcp__github__.*`.  It reads the hook
payload on stdin and:

  - does nothing at all when the call was not a text write, or when the stored
    text matches what was sent (no output, no context, no tokens);
  - reports the delta, once, when they differ;
  - reports that it *could not* verify, once per session per tool, when the
    write is real but unreachable (an unrecognized tool, a pending review
    comment that is not fetchable yet, an API error, a missing token).

The last one is the point of the whole exercise.  False assurance is the
expensive failure here: every path where the check cannot run has to say so,
because a checker that silently never runs converts absence of warnings into
false assurance.

Exit codes, which `.claude/hooks/post-tool-use-github-readback.sh` forwards:

    0   nothing to say
    2   a finding, on stderr, which Claude Code puts in front of the model
    1   a fault in the checker itself; the wrapper turns this into a warning

Standard library only, deliberately: a hook that fails on a missing dependency
is a hook that silently stops checking.

Usage (the hook does this; a human can too, to replay a payload):
    python3 scripts/agents/verify_github_write.py < payload.json

Environment variables:
    GITHUB_TOKEN   Optional.  Without it the read-back falls back to an
                   unauthenticated request, which is rate limited to 60 per
                   hour per address; exhausting that is reported as "could not
                   verify", never as "no alteration found".
"""

import dataclasses
import difflib
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ci", "prs-and-issues")
)

from github_headers import github_headers  # noqa: E402
from session_bylines import strip_bylines  # noqa: E402

API_ROOT = "https://api.github.com"

# Only tools from the GitHub MCP server are considered.
TOOL_PREFIX = "mcp__github__"

# The fields whose text is worth verifying.  A read tool carries neither, so
# testing for them is a serviceable write detector that does not depend on a
# hardcoded list of write tools being complete.
TEXT_FIELDS = ("title", "body")

# Bounds on what a single finding may print.  A full-body diff would be the
# context-hungry version of this and is not acceptable.
MAX_REGIONS_PER_FIELD = 5
MAX_CHARS_PER_FIELD = 800

# Above this much differing text, the diff is reported as one coarse region
# rather than opcode by opcode.  It bounds both the output and the time spent
# in difflib on a long body.
MAX_FINE_DIFF_CHARS = 10000

# One retry absorbs read-after-create lag, where the object exists but is not
# yet served by the read replica that answers the GET.
FETCH_TIMEOUT_SECONDS = 10
RETRY_DELAY_SECONDS = 1.5

MIDDLE_DOT = "·"

EXIT_CLEAN = 0
EXIT_INTERNAL_ERROR = 1
EXIT_FINDING = 2


# ---------------------------------------------------------------------------
# Locating the stored object
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Target:
    """Where to read back a written object, and how to name it in a report."""

    api_url: str
    description: str


class Unverifiable(Exception):
    """The call wrote text, but the stored text cannot be read back."""


def _response_object(tool_response: object) -> dict:
    """Return the written object out of an MCP tool result, or an empty dict.

    MCP results reach a hook in more than one shape: the object itself, a
    JSON-encoded string, or a list of content blocks each carrying `text`.
    Rather than depend on which, this walks whatever arrived and returns the
    first mapping that looks like a GitHub object.  Guessing the shape wrong
    would mean creates are never verified, and the point of this module is
    that an unverified write is reported rather than assumed clean.
    """
    seen_lists = 0
    queue: list[object] = [tool_response]
    while queue and seen_lists < 100:
        item = queue.pop(0)
        if isinstance(item, str):
            stripped = item.strip()
            if stripped.startswith(("{", "[")):
                try:
                    queue.append(json.loads(stripped))
                except ValueError:
                    continue
            continue
        if isinstance(item, list):
            seen_lists += 1
            queue.extend(item)
            continue
        if isinstance(item, dict):
            if any(key in item for key in ("number", "id", "html_url")):
                return item
            queue.extend(item.values())
    return {}


# A created object's number is not always a field of the result.  The GitHub
# MCP server answers `create_pull_request` with an `id` and a `url` and no
# `number` at all, where the `id` is the pull request's database id rather than
# the number the REST API addresses it by, so the number has to come out of the
# URL.  Observed, not assumed: this is what the tool returned when this
# checker's own pull request was opened.
_URL_NUMBER_RE = re.compile(r"/(?:pull|pulls|issues)/(\d+)")


def _number_from_response(response: dict) -> object:
    """Return the issue or pull request number carried by an MCP result, if any."""
    number = response.get("number")
    if number:
        return number
    for key in ("html_url", "url"):
        value = response.get(key)
        if isinstance(value, str):
            match = _URL_NUMBER_RE.search(value)
            if match:
                return match.group(1)
    return None


def _require(value: object, what: str) -> str:
    """Return *value* as a string, or raise Unverifiable naming what is missing."""
    if value in (None, ""):
        raise Unverifiable(f"the {what} of the written object is not in the tool input or result")
    return str(value)


def _repo(tool_input: dict) -> str:
    owner = _require(tool_input.get("owner"), "owner")
    repo = _require(tool_input.get("repo"), "repo")
    return f"{owner}/{repo}"


def _locate_issue_write(tool_input: dict, response: dict) -> Target:
    repo = _repo(tool_input)
    number = tool_input.get("issue_number") or _number_from_response(response)
    number = _require(number, "issue number")
    return Target(f"{API_ROOT}/repos/{repo}/issues/{number}", f"issue {repo}#{number}")


def _locate_issue_comment(tool_input: dict, response: dict) -> Target:
    repo = _repo(tool_input)
    comment_id = _require(response.get("id"), "comment id")
    return Target(
        f"{API_ROOT}/repos/{repo}/issues/comments/{comment_id}",
        f"comment {comment_id} on {repo}#{tool_input.get('issue_number')}",
    )


def _locate_create_pull_request(tool_input: dict, response: dict) -> Target:
    repo = _repo(tool_input)
    number = _require(_number_from_response(response), "pull request number")
    return Target(f"{API_ROOT}/repos/{repo}/pulls/{number}", f"pull request {repo}#{number}")


def _locate_update_pull_request(tool_input: dict, response: dict) -> Target:
    repo = _repo(tool_input)
    number = tool_input.get("pullNumber") or _number_from_response(response)
    number = _require(number, "pull request number")
    return Target(f"{API_ROOT}/repos/{repo}/pulls/{number}", f"pull request {repo}#{number}")


def _locate_review_write(tool_input: dict, response: dict) -> Target:
    """Locate a review body, which is fetchable only once the review is submitted.

    `pull_request_review_write` with method "create" and no `event` opens a
    *pending* review, and GitHub stores nothing fetchable for it.  With an
    `event`, the same method submits immediately.  Either way the body is
    verified on the call that submits it, and not before.
    """
    method = tool_input.get("method")
    pending = method == "create" and not tool_input.get("event")
    if pending or method not in ("create", "submit_pending"):
        raise Unverifiable(
            f"a review written with method '{method}' and no submit event is pending, and "
            "stores nothing fetchable until it is submitted"
        )
    repo = _repo(tool_input)
    number = _require(tool_input.get("pullNumber"), "pull request number")
    review_id = _require(response.get("id"), "review id")
    return Target(
        f"{API_ROOT}/repos/{repo}/pulls/{number}/reviews/{review_id}",
        f"review {review_id} on pull request {repo}#{number}",
    )


def _locate_review_comment_reply(tool_input: dict, response: dict) -> Target:
    repo = _repo(tool_input)
    comment_id = _require(response.get("id"), "review comment id")
    return Target(
        f"{API_ROOT}/repos/{repo}/pulls/comments/{comment_id}",
        f"review comment {comment_id} on {repo}",
    )


def _locate_pending_review_comment(tool_input: dict, response: dict) -> Target:
    del tool_input, response
    raise Unverifiable(
        "a comment on a pending review stores nothing fetchable until the review is "
        "submitted, so its text cannot be read back at write time"
    )


# Tool name (without the mcp__github__ prefix) to the function that says where
# the stored object lives.  A tool that carries text and is not in this table
# is reported as an unverified write rather than skipped silently.
LOCATORS = {
    "issue_write": _locate_issue_write,
    "add_issue_comment": _locate_issue_comment,
    "create_pull_request": _locate_create_pull_request,
    "update_pull_request": _locate_update_pull_request,
    "pull_request_review_write": _locate_review_write,
    "add_reply_to_pull_request_comment": _locate_review_comment_reply,
    "add_comment_to_pending_review": _locate_pending_review_comment,
}


def sent_texts(tool_input: dict) -> dict:
    """Return the non-empty text fields being written, keyed by field name."""
    return {
        field: tool_input[field]
        for field in TEXT_FIELDS
        if isinstance(tool_input.get(field), str) and tool_input[field] != ""
    }


def is_text_write(tool_name: str, tool_input: object) -> bool:
    """Return True when this call is a GitHub MCP call that carries text."""
    if not tool_name.startswith(TOOL_PREFIX):
        return False
    if not isinstance(tool_input, dict):
        return False
    return bool(sent_texts(tool_input))


def locate(tool_name: str, tool_input: dict, tool_response: object) -> Target:
    """Return where to read the written object back from.

    Raises:
        Unverifiable: when the object cannot be addressed, with the reason.
    """
    short_name = tool_name[len(TOOL_PREFIX) :]
    locator = LOCATORS.get(short_name)
    if locator is None:
        raise Unverifiable(
            f"'{tool_name}' writes text but is not in this checker's table of "
            "read-back endpoints, so nothing was compared"
        )
    return locator(tool_input, _response_object(tool_response))


# ---------------------------------------------------------------------------
# Fetching and normalizing
# ---------------------------------------------------------------------------


def fetch_stored(api_url: str, opener=urllib.request.urlopen) -> dict:
    """GET *api_url* and return the decoded object.

    One retry on 404 absorbs read-after-create lag.

    Raises:
        Unverifiable: on any failure, so the caller reports "could not verify"
            rather than treating an unreachable object as unaltered.
    """
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers = github_headers(token)
    headers["User-Agent"] = "gb4pc-verify-github-write"

    for attempt in (0, 1):
        request = urllib.request.Request(api_url, headers=headers)
        try:
            with opener(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and attempt == 0:
                time.sleep(RETRY_DELAY_SECONDS)
                continue
            unauthenticated = "" if token else " (no GITHUB_TOKEN, so the read was unauthenticated)"
            raise Unverifiable(f"GET {api_url} returned HTTP {exc.code}{unauthenticated}") from exc
        except Exception as exc:  # noqa: BLE001
            raise Unverifiable(f"GET {api_url} failed: {type(exc).__name__}: {exc}") from exc
    raise Unverifiable(f"GET {api_url} returned HTTP 404 twice")


def normalize(text: str) -> str:
    """Return *text* with benign systematic differences removed.

    Three of them, and each one matters more than it looks.  A difference the
    hook reports but cannot act on fires on every single write, and a checker
    that cries wolf every time is a checker nobody reads, which is the same end
    state as having none.

      - CRLF becomes LF.
      - Trailing newlines are dropped from both sides rather than predicted on
        one.  GitHub is known to append one to a body that does not end in a
        newline; dropping them symmetrically holds whether or not it still
        does, which a prediction would not.
      - Claude session bylines are removed, from both sides.  The repository
        already strips those within seconds of the write, by the
        strip-session-bylines workflow, so reporting one would be reporting a
        difference that repairs itself.  Removing them from the sent text too
        means an agent that mistakenly sent a byline gets no phantom "text was
        removed" finding for it.
    """
    return strip_bylines(text.replace("\r\n", "\n")).rstrip("\n")


# ---------------------------------------------------------------------------
# Diffing, escaping, and classifying
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Region:
    """One contiguous difference between sent and stored text."""

    position: int
    sent: str
    stored: str

    @property
    def classification(self) -> str:
        """Name the alteration behavior this region looks like."""
        if MIDDLE_DOT in self.stored and MIDDLE_DOT not in self.sent:
            return "mention dotting"
        if self.sent and not self.stored:
            return "removal"
        if self.stored and not self.sent:
            return "addition"
        return "other"


def _trim_common(sent: str, stored: str) -> tuple[int, str, str]:
    """Return the common prefix length and the differing middles.

    Most alterations touch a few characters of a long body, so trimming the
    matching ends first keeps difflib's work proportional to the damage rather
    than to the size of the artifact.
    """
    head = 0
    limit = min(len(sent), len(stored))
    while head < limit and sent[head] == stored[head]:
        head += 1
    tail = 0
    while tail < limit - head and sent[-1 - tail] == stored[-1 - tail]:
        tail += 1
    return head, sent[head : len(sent) - tail], stored[head : len(stored) - tail]


def diff_regions(sent: str, stored: str) -> tuple[list, int]:
    """Return up to MAX_REGIONS_PER_FIELD differing regions, and how many were omitted."""
    if sent == stored:
        return [], 0

    offset, sent_middle, stored_middle = _trim_common(sent, stored)

    if max(len(sent_middle), len(stored_middle)) > MAX_FINE_DIFF_CHARS:
        # Too much differs to itemize usefully, and itemizing it would cost more
        # than it tells anyone.
        return [Region(offset, sent_middle, stored_middle)], 0

    matcher = difflib.SequenceMatcher(None, sent_middle, stored_middle, autojunk=False)
    regions = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        regions.append(Region(offset + i1, sent_middle[i1:i2], stored_middle[j1:j2]))

    omitted = max(0, len(regions) - MAX_REGIONS_PER_FIELD)
    return regions[:MAX_REGIONS_PER_FIELD], omitted


def escape(text: str) -> str:
    """Return *text* with every character a reader could miss made visible.

    A middle dot reported literally is a difference the reader cannot see, and
    the whole failure this checker exists for is one that survives review by
    looking right.  Escaping also makes the delta safe for the agent to quote
    into a GitHub comment, since nothing in it is left in a form the write path
    would alter again.
    """
    out = []
    for char in text:
        if char == "\n":
            out.append("\\n")
        elif char == "\t":
            out.append("\\t")
        elif char == "\\":
            out.append("\\\\")
        elif " " <= char <= "~":
            out.append(char)
        else:
            name = unicodedata.name(char, "")
            escaped = "\\u%04x" % ord(char) if ord(char) <= 0xFFFF else "\\U%08x" % ord(char)
            out.append(f"{escaped}[{name}]" if name else escaped)
    return "".join(out)


def _clip(text: str, budget: int) -> str:
    """Return *text* escaped, clipped to *budget* characters, saying how much was cut."""
    escaped = escape(text)
    if len(escaped) <= budget:
        return escaped
    return escaped[:budget] + f"... ({len(escaped) - budget} more characters)"


def render_field(field: str, regions: list, omitted: int) -> str:
    """Return the human-readable delta for one field, within the output bounds."""
    lines = [f'  field "{field}": {len(regions) + omitted} changed region(s)']
    budget = MAX_CHARS_PER_FIELD
    for region in regions:
        share = max(60, budget // max(1, len(regions)))
        lines.append(
            f"    at character {region.position} [{region.classification}]"
            f"\n      sent:   {_clip(region.sent, share)}"
            f"\n      stored: {_clip(region.stored, share)}"
        )
    if omitted:
        lines.append(f"    ... and {omitted} further changed region(s) not shown")
    return "\n".join(lines)


ADVICE = {
    "mention dotting": (
        "A dotted mention notifies nobody and reaches no bot command surface, so a "
        "dependabot rebase command sent this way did nothing and reported no error. "
        "The behavior is external to this repository and it is not constant: writes have "
        "been observed storing a mention intact, so neither outcome can be assumed. What "
        "you cannot do is assume this one worked, because it did not. Prefer a mechanism "
        "that needs no mention, such as re-running the workflow or a tool call. If the "
        "mention is genuinely required, one retry is reasonable, and this checker will "
        "tell you whether it arrived; if it is dotted again, ask a human to post it and "
        "say why you cannot."
    ),
    "removal": (
        "Text you sent is absent from the stored object. Angle-bracket constructs are "
        "known to be dropped on some write paths, and back-ticks do not protect them. "
        "The stored text still reads as plausible prose, which is why this has survived "
        "review before. Rewrite the passage to name the construct in words instead of "
        "showing it, then edit the stored object."
    ),
    "addition": (
        "Text you did not send is in the stored object. Read it before deciding what to "
        "do: an appended attribution footer is prohibited by AGENTS.md and must be "
        "removed by editing the object."
    ),
    "other": (
        "The stored text differs from what was sent in a way this checker has not seen "
        "before. Read the delta and decide whether the stored artifact still says what "
        "you meant."
    ),
}


def build_report(
    tool_name: str, target: Target, deltas: dict, repeated: bool, html_url: str = ""
) -> str:
    """Return the full finding message for a write whose stored text differs."""
    classes = []
    for regions, _ in deltas.values():
        for region in regions:
            if region.classification not in classes:
                classes.append(region.classification)

    lines = [
        f"GitHub write altered in storage: {tool_name} wrote {target.description}, "
        "and the stored text is not the text that was sent.",
        f"  object: {target.api_url}",
    ]
    if html_url:
        lines.append(f"  as stored: {html_url}")
    for field, (regions, omitted) in sorted(deltas.items()):
        lines.append(render_field(field, regions, omitted))
    for name in classes:
        lines.append(f"  {name}: {ADVICE[name]}")
    if repeated:
        lines.append(
            "  This object has been reported for the same alteration before in this "
            "session. Editing it again will not help: stop re-posting this construct and "
            "change approach."
        )
    else:
        lines.append(
            "  The stored object is wrong as it stands. Edit it, or say plainly that you "
            "could not post what you meant to."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The session log
# ---------------------------------------------------------------------------

MAX_LOG_LINE_CHARS = 2000
MAX_LOG_BYTES = 256 * 1024


def log_path(session_id: str) -> str:
    """Return the session-scoped log file path for *session_id*."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")[:64]
    return os.path.join(tempfile.gettempdir(), f"gb4pc-github-writes-{safe}.jsonl")


def read_log(session_id: str) -> list:
    """Return the entries logged so far this session, oldest first."""
    entries = []
    try:
        with open(log_path(session_id), encoding="utf-8") as handle:
            for line in handle:
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return entries


def append_log(session_id: str, entry: dict) -> None:
    """Append one capped line to the session log.

    This is what makes "did the check actually run" answerable, which matters
    more here than usual: a checker that silently never runs converts absence
    of warnings into false assurance.
    """
    path = log_path(session_id)
    try:
        if os.path.exists(path) and os.path.getsize(path) > MAX_LOG_BYTES:
            return
        line = json.dumps(entry, ensure_ascii=True)[:MAX_LOG_LINE_CHARS]
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        return


def already_reported(entries: list, status: str, key: str) -> bool:
    """Return True when this session already reported *status* for *key*."""
    return any(entry.get("status") == status and entry.get("key") == key for entry in entries)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def check(payload: dict) -> tuple[int, str]:
    """Run the check for one hook payload.  Returns (exit code, message)."""
    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input")
    session_id = payload.get("session_id") or ""

    if not is_text_write(tool_name, tool_input):
        return EXIT_CLEAN, ""

    entries = read_log(session_id)
    texts = sent_texts(tool_input)

    try:
        target = locate(tool_name, tool_input, payload.get("tool_response"))
        stored = fetch_stored(target.api_url)
    except Unverifiable as exc:
        # Once per session per tool: the point is that the gap is visible, not
        # that it is repeated on every call.
        if already_reported(entries, "unverified", tool_name):
            append_log(
                session_id, {"tool": tool_name, "status": "unverified-repeat", "key": tool_name}
            )
            return EXIT_CLEAN, ""
        append_log(session_id, {"tool": tool_name, "status": "unverified", "key": tool_name})
        return EXIT_FINDING, (
            f"GitHub write NOT verified: {tool_name} wrote text and the stored text could "
            f"not be read back, because {exc}.\n"
            "  This is not a clean result. Text posted by an agent is sometimes altered in "
            "storage, silently (issue #909), and this write was not checked. Read it back "
            "yourself if it carries an at-sign mention, an angle-bracket construct, or "
            "anything a reader would act on.\n"
            f"  Reported once per session for {tool_name}."
        )

    deltas = {}
    missing = []
    for field, sent in texts.items():
        value = stored.get(field)
        if not isinstance(value, str):
            missing.append(field)
            continue
        regions, omitted = diff_regions(normalize(sent), normalize(value))
        if regions:
            deltas[field] = (regions, omitted)

    if deltas:
        classes = sorted(
            {region.classification for regions, _ in deltas.values() for region in regions}
        )
        key = target.api_url + "|" + ",".join(classes)
        repeated = already_reported(entries, "finding", key)
        append_log(
            session_id,
            {
                "tool": tool_name,
                "status": "finding",
                "key": key,
                "url": target.api_url,
                "classes": classes,
            },
        )
        html_url = stored.get("html_url") if isinstance(stored.get("html_url"), str) else ""
        return EXIT_FINDING, build_report(tool_name, target, deltas, repeated, html_url)

    if missing and len(missing) == len(texts):
        key = f"{tool_name}:fields"
        if already_reported(entries, "unverified", key):
            return EXIT_CLEAN, ""
        append_log(session_id, {"tool": tool_name, "status": "unverified", "key": key})
        return EXIT_FINDING, (
            f"GitHub write NOT verified: {tool_name} sent {', '.join(sorted(missing))}, and "
            f"the object at {target.api_url} carries no such field, so nothing was compared."
        )

    append_log(
        session_id,
        {"tool": tool_name, "status": "clean", "key": target.api_url, "fields": sorted(texts)},
    )
    return EXIT_CLEAN, ""


def main(argv: list[str] | None = None) -> int:
    del argv  # The payload arrives on stdin; there are no arguments.
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError as exc:
        print(f"verify_github_write: unreadable hook payload: {exc}", file=sys.stderr)
        return EXIT_INTERNAL_ERROR
    if not isinstance(payload, dict):
        print("verify_github_write: hook payload was not an object", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    try:
        status, message = check(payload)
    except Exception as exc:  # noqa: BLE001
        # A fault in the checker is the wrapper's business, not the model's:
        # exit 1 and let it print a warning rather than spending context on a
        # traceback the agent cannot act on.
        print(f"verify_github_write: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    if message:
        print(message, file=sys.stderr)
    return status


if __name__ == "__main__":
    sys.exit(main())
