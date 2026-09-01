#!/usr/bin/env python3
r"""link_gh_issues.py: set semantic links between GitHub issues via the REST API.

**Issue dependencies are the gap this fills.** GitHub models "blocked by" and
"blocking" natively, but no tool an agent has can write them: the GitHub MCP
server ships `sub_issue_write` for hierarchy and `issue_write` for an issue's
own fields, and nothing at all for `/dependencies/blocked_by`. So an agent that
wants to record "issue A cannot start until issue B lands" is left writing it
in prose in the issue body, where nothing can query it and nothing keeps it
true. This script writes the native link instead, which shows in the issue
sidebar, gates the `-is:blocked` search filters and Projects views, and
survives any later edit to the issue text.

The parent/child flags are here for symmetry, and are the lesser half: hierarchy
already has `mcp__github__sub_issue_write`. What they add over it is the same
thing `--blocked-by` needs anyway--both GitHub endpoints take a *database id*
and the MCP tool passes that requirement straight through to the caller, while
this script accepts the issue number a human actually has and resolves it.

Follows the pattern of `scripts/agents/update_gh_labels.sh` (issue #710) and
`scripts/agents/delete_gh_comment.sh` (issue #658): a plain REST call for
environments with no `gh` CLI. Standard library only, so the script cannot
fail on a missing dependency.

Relations
---------

Every relation is named from the point of view of the *subject* issue--the
one named in the positional arguments:

    --blocked-by REF    the subject cannot proceed until REF is done
    --blocks REF        REF cannot proceed until the subject is done
    --parent-of REF     REF becomes a sub-issue of the subject
    --child-of REF      the subject becomes a sub-issue of REF

`--blocks` and `--child-of` are the inverses of the other two. GitHub serves
only one write direction for each pair (`POST .../dependencies/blocked_by` and
`POST .../sub_issues`; there is no POST to `.../dependencies/blocking`), so
those two flags are applied by writing to REF's endpoint with the subject as
the operand. That inversion is the only reason both directions exist here: it
saves the caller from having to restate a dependency backwards to record it.

Pull requests are not linkable
------------------------------

GitHub takes issues only, on both sides of both link types. All four positions
answer 422 (verified 2026-09-01 against this repository): `Source issue may
only be an issue`, `Target issue may only be an issue`, `Parent may only be an
issue`, `Sub issue may only be an issue`. So a pull request in any position is
refused here before the call, with one line naming the fallback rather than a
GitHub validation error.

That fallback is the one `agents/verification_planning.md` already prescribes:
when a pull request number is not accepted, link the issue the pull request
resolves instead.

What this script deliberately does not do
-----------------------------------------

Two relations that sound like they belong here do not, for opposite reasons.

**"Fixes" (PR to issue) has no write API at all.** GitHub derives that link
from a closing keyword in the pull request description (`Fixes #123`) or from
the Development sidebar in the web UI; there is no REST endpoint and no GraphQL
mutation to create it, only the read-only `closed_by_pull_requests` summary on
the issue. Since a PR's description is already the agent's to write (see
`agents/pr_creation.md`), the supported way to record it is to put `Fixes #123`
in the description. Nothing here could make that more reliable, so nothing here
tries.

**"Duplicate of" is already covered.** It is a first-class field, not a link:
`mcp__github__issue_write` takes `duplicate_of` alongside
`state_reason: "duplicate"`. Duplicating that here would give the same relation
two spellings, so this script leaves it alone.

Usage
-----

    scripts/agents/link_gh_issues.py show <owner> <repo> <issue> [--json]

    scripts/agents/link_gh_issues.py add <owner> <repo> <issue> \
        [--blocked-by REF]... [--blocks REF]... \
        [--parent-of REF]... [--child-of REF]... [--dry-run]

    scripts/agents/link_gh_issues.py remove <owner> <repo> <issue> \
        [--blocked-by REF]... ... [--dry-run]

REF may be given as `123`, `#123`, `owner/repo#123`, or a github.com issue URL.
A bare number means an issue in the same repository as the subject. Neither the
owner nor the repository may contain a `/`, and a URL on any other host is
refused rather than looked up on github.com as if it were the same issue.

Example--record that issue #42 is waiting on #17 and #19, in one call:

    scripts/agents/link_gh_issues.py add aunger gallery-button-for-pixel-camera 42 \
        --blocked-by 17 --blocked-by 19

Adding a link that is already present, or removing one that is already absent,
is reported and treated as success: the goal state is what matters, matching
the idempotent behavior of `scripts/agents/update_gh_labels.sh`.

Exit codes:
    0   every requested link reached its goal state
    1   at least one did not (the failure is reported on stderr)

Required environment variables:
    GITHUB_TOKEN   Token with `issues: write` on every repository written to
                   (read alone is enough for `show`). All four relations were
                   verified writable against this repository on 2026-09-01.

                   In a Claude Code session this variable is required but
                   inert: `HTTPS_PROXY` routes `api.github.com` through a local
                   proxy that supplies its own credential, and a garbage value
                   behaves identically to the real one. Writes from this script
                   therefore land as `claude[bot]` rather than as the token's
                   nominal owner. Outside a session--a developer machine, or
                   CI--the value is what authenticates, which is why it is
                   still required. See `.claude/environment.md`,
                   "`GITHUB_TOKEN` is inert in a session".

                   Beware one wording when reading failures. GitHub answers
                   `403 Resource not accessible by integration` when the
                   relationship a request names does not exist--removing a
                   sub-issue from an issue that is not its parent, say--which
                   says nothing about permissions; the same call with a real
                   operand returns 200. GitHub uses "integration" for
                   fine-grained PATs too, so it is not evidence about the
                   credential either.
"""

import argparse
import dataclasses
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ci", "prs-and-issues")
)

from github_headers import github_headers  # noqa: E402

API_ROOT = "https://api.github.com"

REQUEST_TIMEOUT_SECONDS = 15

# A call that lost to a 5xx or a secondary rate limit is worth one retry; one
# that lost to a 4xx is a decision, not a hiccup, and is reported as-is.
#
# Only for a method that is safe to send twice, though. A POST that reached
# GitHub and lost its response would, on retry, be refused as a
# duplicate--turning a link that was in fact created into a reported failure.
# GET and DELETE are idempotent here, so they retry; POST gets one attempt.
RETRY_DELAY_SECONDS = 2.0
# A `Retry-After` longer than this is truncated rather than slept through, so a
# header asking for minutes cannot hang the script with nothing on stdout. The
# shortened retry will usually be refused again, and that refusal is reported
# normally, which is the outcome a caller can act on.
MAX_RETRY_DELAY_SECONDS = 60.0
RETRYABLE_STATUSES = (429, 500, 502, 503, 504)
IDEMPOTENT_METHODS = ("GET", "DELETE")

EXIT_OK = 0
EXIT_FAILED = 1


# ---------------------------------------------------------------------------
# Issue references
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Issue:
    """An issue located on GitHub, carrying the id the link endpoints want.

    The write endpoints take a *database id*, never an issue number, so every
    reference has to be resolved through a GET before it can be linked. The
    number is kept alongside it purely so reports can name the issue the way a
    human wrote it.
    """

    owner: str
    repo: str
    number: int
    id: int
    title: str
    is_pull_request: bool

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}#{self.number}"


# `owner/repo#123`, `#123`, `123`, or a github.com issue/PR URL. The host is
# pinned because API_ROOT is: a GitLab or Enterprise URL parsed as a bare
# owner/repo would be looked up on github.com, against a different issue.
_REF_URL = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+)"
    r"/(?:issues|pull)/(?P<number>\d+)"
)
# Neither group may hold a `/`: `a/b/c#1` is not `a` + `b/c`, it is unparseable.
_REF_QUALIFIED = re.compile(r"^(?P<owner>[^/#\s]+)/(?P<repo>[^/#\s]+)#(?P<number>\d+)$")
_REF_BARE = re.compile(r"^#?(?P<number>\d+)$")


def parse_ref(ref: str, default_owner: str, default_repo: str) -> tuple[str, str, int]:
    """Resolve a user-written issue reference to (owner, repo, number).

    A bare number inherits the subject's repository, which is what makes the
    common same-repo call short.
    """
    ref = ref.strip()
    for pattern in (_REF_URL, _REF_QUALIFIED):
        match = pattern.match(ref)
        if match:
            return match["owner"], match["repo"], int(match["number"])
    match = _REF_BARE.match(ref)
    if match:
        return default_owner, default_repo, int(match["number"])
    raise ValueError(
        f"cannot parse issue reference {ref!r}; expected 123, #123, owner/repo#123, or an issue URL"
    )


# ---------------------------------------------------------------------------
# REST plumbing
# ---------------------------------------------------------------------------


class ApiError(Exception):
    """A GitHub response that the caller has to report rather than absorb."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _retry_delay(headers: object) -> float:
    """How long to wait before the one retry, honoring GitHub's own instruction.

    GitHub sends `Retry-After` (in seconds) with a 429 and with a secondary
    rate limit. Retrying on a flat delay while it is asking for longer is how
    the next refusal is earned, so the header wins when it is present and
    longer than the default.
    """
    value = headers.get("Retry-After") if hasattr(headers, "get") else None
    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return RETRY_DELAY_SECONDS
    return max(RETRY_DELAY_SECONDS, min(seconds, MAX_RETRY_DELAY_SECONDS))


def _describe_failure(status: int, payload: object, raw: str, fallback: str = "") -> str:
    """Turn an error response into one line that says what to do about it.

    `fallback` names the failure when the response carried no body to quote;
    without one the line would render the bare `HTTP {status}` default and read
    as "HTTP 404: HTTP 404".
    """
    message = ""
    if isinstance(payload, dict):
        message = str(payload.get("message") or "")
        errors = payload.get("errors")
        if isinstance(errors, list):
            details = [
                str(e.get("message") or e.get("code")) for e in errors if isinstance(e, dict)
            ]
            details = [d for d in details if d]
            if details:
                message = f"{message} ({'; '.join(details)})" if message else "; ".join(details)
    if not message:
        message = raw.strip()[:200] or fallback or f"HTTP {status}"

    # GitHub returns this one wording for two unrelated causes and distinguishes
    # neither. Naming the likelier one first matters: reading it as a permission
    # verdict sends the reader off to mint a token they already have.
    if status in (401, 403) and "not accessible by integration" in message.lower():
        message += (
            "--this usually means the link or issue the request names does not exist, "
            "not that the token lacks permission: GitHub answers 403 (not 404) when asked "
            "to remove a link that is not there. Check the link exists, then check that "
            "GITHUB_TOKEN has 'issues: write' here."
        )
    elif status == 401:
        message += "--check that GITHUB_TOKEN is set and valid."
    return f"HTTP {status}: {message}"


def api(method: str, path: str, token: str, body: dict | None = None) -> tuple[int, object]:
    """Call the GitHub REST API and return (status, parsed body).

    Raises ApiError on any status the caller did not ask to see. 404 is
    returned rather than raised, because for these endpoints it is a legitimate
    answer ("no parent", "that link is already gone") as often as it is a fault.
    """
    url = f"{API_ROOT}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = github_headers(token)
    if data is not None:
        headers["Content-Type"] = "application/json"

    attempts = 2 if method in IDEMPOTENT_METHODS else 1
    for attempt in range(attempts):
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            # URL is built from the API_ROOT constant; the file:// risk does not apply.
            with urllib.request.urlopen(  # nosemgrep
                request, timeout=REQUEST_TIMEOUT_SECONDS
            ) as response:
                raw = response.read().decode("utf-8", "replace")
                return response.status, (json.loads(raw) if raw.strip() else None)
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8", "replace")
            try:
                payload = json.loads(raw) if raw.strip() else None
            except json.JSONDecodeError:
                payload = None
            secondary_limit = error.code == 403 and "secondary rate limit" in raw.lower()
            if (error.code in RETRYABLE_STATUSES or secondary_limit) and attempt < attempts - 1:
                time.sleep(_retry_delay(error.headers))
                continue
            if error.code == 404:
                return 404, payload
            raise ApiError(error.code, _describe_failure(error.code, payload, raw)) from error
        except urllib.error.URLError as error:
            if attempt < attempts - 1:
                time.sleep(RETRY_DELAY_SECONDS)
                continue
            raise ApiError(0, f"could not reach {API_ROOT}: {error.reason}") from error
    raise AssertionError("unreachable")


def get_issue(owner: str, repo: str, number: int, token: str) -> Issue:
    """Look up one issue, mainly to learn the database id the writes need."""
    status, payload = api("GET", f"/repos/{owner}/{repo}/issues/{number}", token)
    if status == 404 or not isinstance(payload, dict):
        raise ApiError(404, f"{owner}/{repo}#{number} not found (or the token cannot see it)")
    if "number" not in payload or "id" not in payload:
        # Without the database id there is nothing to write, so say that rather
        # than raising KeyError past main()'s ApiError handler as a traceback.
        raise ApiError(0, f"{owner}/{repo}#{number}: response carried no issue id; cannot link it")
    return Issue(
        owner=owner,
        repo=repo,
        number=int(payload["number"]),
        id=int(payload["id"]),
        title=str(payload.get("title") or ""),
        is_pull_request="pull_request" in payload,
    )


def paged(path: str, token: str, missing_ok: bool = True) -> list:
    """Read every page of a list endpoint.

    `missing_ok` is what separates the two readers. For `show`, a 404 is an
    answer ("nothing is linked") and an empty list is right. For the pre-read
    that decides whether a link is already there, it is not: reading "no links"
    off an endpoint that was never reached would report `Already unlinked` for
    a link that may well exist, and call that success.

    A 404 is not the only way to read nothing, so a payload that is not a list
    is refused on the same terms. Otherwise a 200 carrying an error object
    would land in the same silent empty list `missing_ok` exists to prevent.
    """
    items: list = []
    page = 1
    while True:
        joiner = "&" if "?" in path else "?"
        status, payload = api("GET", f"{path}{joiner}per_page=100&page={page}", token)
        if status == 404:
            if not missing_ok:
                raise ApiError(404, f"HTTP 404: {path} could not be read, so the links are unknown")
            break
        if not isinstance(payload, list):
            if not missing_ok:
                raise ApiError(
                    status,
                    f"HTTP {status}: {path} answered with no list of links, "
                    "so the links are unknown",
                )
            break
        items.extend(payload)
        if len(payload) < 100:
            break
        page += 1
    return items


# ---------------------------------------------------------------------------
# The relations
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Relation:
    """One user-facing relation, and how it maps onto an endpoint.

    GitHub writes each pair of relations from one side only, so `inverted`
    records whether the endpoint is called on the subject or on the other
    issue. Everything else about a relation follows from `family`, which keeps
    the four flags from needing four hand-written code paths.
    """

    flag: str
    family: str  # "dependency" or "sub-issue"
    inverted: bool
    # Rendered as "<subject> <phrase> <other>" in reports.
    phrase: str


RELATIONS = (
    Relation("--blocked-by", "dependency", False, "is blocked by"),
    Relation("--blocks", "dependency", True, "blocks"),
    Relation("--parent-of", "sub-issue", False, "is the parent of"),
    Relation("--child-of", "sub-issue", True, "is a sub-issue of"),
)

RELATION_BY_FLAG = {relation.flag: relation for relation in RELATIONS}

# All four flags share one argparse destination, so that the report comes out
# in the order the flags were typed. `action="append"` would keep a list per
# flag, and the only order recoverable from four separate lists is the order of
# RELATIONS, which is not the order of the call.
LINKS_DEST = "links"


class RelationAction(argparse.Action):
    """Record `(relation, ref)` in call order, across all four relation flags."""

    def __call__(self, parser, namespace, values, option_string=None):
        links = getattr(namespace, LINKS_DEST, None)
        if links is None:
            links = []
            setattr(namespace, LINKS_DEST, links)
        links.append((RELATION_BY_FLAG[option_string], values))


def members_path(family: str, holder: Issue) -> str:
    """The endpoint listing what is already linked to `holder` in `family`."""
    base = f"/repos/{holder.owner}/{holder.repo}/issues/{holder.number}"
    return f"{base}/dependencies/blocked_by" if family == "dependency" else f"{base}/sub_issues"


def add_link(family: str, holder: Issue, other: Issue, token: str) -> None:
    """Create the link, writing to `holder`'s endpoint with `other` as operand.

    `api` hands 404 back rather than raising, because for the reads here it is
    an answer. For a write it never is: nothing was created, so reporting
    `Linked` off a 404 would be a plain false success. `update_gh_labels.sh`
    draws the same line, treating 404 on its add as an error.
    """
    key = "issue_id" if family == "dependency" else "sub_issue_id"
    status, payload = api("POST", members_path(family, holder), token, {key: other.id})
    if status == 404:
        raise ApiError(
            404,
            _describe_failure(404, payload, "", fallback="the endpoint answered 404")
            + f"--nothing was written; check that {holder.slug} exists and is visible.",
        )


def remove_link(family: str, holder: Issue, other: Issue, token: str) -> None:
    """Delete the link. The two families disagree on where the operand goes.

    A 404 is left alone here, unlike in `add_link`: the goal state is the link
    being absent, and a delete that 404s has reached it. That is how
    `update_gh_labels.sh` reads 404 on its own remove.
    """
    base = f"/repos/{holder.owner}/{holder.repo}/issues/{holder.number}"
    if family == "dependency":
        api("DELETE", f"{base}/dependencies/blocked_by/{other.id}", token)
    else:
        api("DELETE", f"{base}/sub_issue", token, {"sub_issue_id": other.id})


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def issue_line(item: dict) -> str:
    """One line describing a linked issue, as `show` prints it."""
    number = item.get("number")
    title = (item.get("title") or "").strip()
    state = item.get("state") or "?"
    repo = item.get("repository") or {}
    where = repo.get("full_name")
    ref = f"{where}#{number}" if where else f"#{number}"
    if len(title) > 72:
        title = title[:69] + "..."
    return f"    {ref}  [{state}]  {title}"


def run_show(args, token: str) -> int:
    subject = get_issue(args.owner, args.repo, args.issue, token)
    base = f"/repos/{subject.owner}/{subject.repo}/issues/{subject.number}"

    blocked_by = paged(f"{base}/dependencies/blocked_by", token)
    blocking = paged(f"{base}/dependencies/blocking", token)
    sub_issues = paged(f"{base}/sub_issues", token)
    # A parentless issue answers 404 "No parent issue found"; that is an answer.
    status, parent = api("GET", f"{base}/parent", token)
    parent = parent if status != 404 and isinstance(parent, dict) else None

    if args.json:
        print(
            json.dumps(
                {
                    "issue": subject.slug,
                    "title": subject.title,
                    "blocked_by": blocked_by,
                    "blocking": blocking,
                    "parent": parent,
                    "sub_issues": sub_issues,
                },
                indent=2,
            )
        )
        return EXIT_OK

    print(f"{subject.slug}: {subject.title}")
    # An issue has at most one parent, so counting that row would print
    # "Parent (1)" and invite the reader to wonder when it says 2.
    for label, items, counted in (
        ("Blocked by", blocked_by, True),
        ("Blocking", blocking, True),
        ("Parent", [parent] if parent else [], False),
        ("Sub-issues", sub_issues, True),
    ):
        if not items:
            print(f"  {label}: none")
            continue
        print(f"  {label} ({len(items)}):" if counted else f"  {label}:")
        for item in items:
            if isinstance(item, dict):
                print(issue_line(item))
    return EXIT_OK


def run_change(args, token: str, adding: bool) -> int:
    """Apply every requested link (or unlink), reporting one line each."""
    requested = getattr(args, LINKS_DEST, None) or []
    if not requested:
        flags = ", ".join(rel.flag for rel in RELATIONS)
        print(f"Error: at least one of {flags} is required.", file=sys.stderr)
        return EXIT_FAILED

    subject = get_issue(args.owner, args.repo, args.issue, token)
    exit_code = EXIT_OK
    # Resolving the same reference twice in one call is pure waste, and the
    # membership read is per (family, holder), not per link. The subject is
    # seeded because a reference back to it is exactly what the self-link guard
    # below is there to catch, and re-fetching it to find that out is a wasted
    # call.
    resolved: dict[tuple[str, str, int], Issue] = {
        (subject.owner.lower(), subject.repo.lower(), subject.number): subject
    }
    members: dict[tuple[str, str, str, int], set[int]] = {}

    for relation, ref in requested:
        try:
            owner, repo, number = parse_ref(ref, subject.owner, subject.repo)
        except ValueError as error:
            print(f"Error: {error}", file=sys.stderr)
            exit_code = EXIT_FAILED
            continue

        try:
            key = (owner.lower(), repo.lower(), number)
            if key not in resolved:
                resolved[key] = get_issue(owner, repo, number, token)
            other = resolved[key]

            # Which issue the endpoint is called on, and which is the operand.
            holder, operand = (other, subject) if relation.inverted else (subject, other)
            # Every phrase is written from the subject's side, so the report
            # reads the same way the caller's flag did, inverted or not.
            described = f"{subject.slug} {relation.phrase} {other.slug}"

            if holder.id == operand.id:
                print(f"Error: cannot link {subject.slug} to itself.", file=sys.stderr)
                exit_code = EXIT_FAILED
                continue
            # Neither family takes a pull request, in either position. GitHub
            # refuses all four with a 422 (verified 2026-09-01): "Source issue
            # may only be an issue", "Target issue may only be an issue",
            # "Parent may only be an issue", "Sub issue may only be an issue".
            # Refusing here turns that into one legible line naming the
            # fallback, which is what `agents/verification_planning.md` already
            # tells an agent to do when a PR number is not accepted.
            if holder.is_pull_request or operand.is_pull_request:
                pull = holder if holder.is_pull_request else operand
                print(
                    f"Error: {described}: {pull.slug} is a pull request, and GitHub links "
                    "of this kind join issues only. Link the issue the pull request "
                    "resolves instead; to record that a pull request closes an issue, put "
                    "`Fixes #N` in its description.",
                    file=sys.stderr,
                )
                exit_code = EXIT_FAILED
                continue

            member_key = (relation.family, holder.owner.lower(), holder.repo.lower(), holder.number)
            if member_key not in members:
                members[member_key] = {
                    item.get("id")
                    for item in paged(
                        members_path(relation.family, holder), token, missing_ok=False
                    )
                    if isinstance(item, dict)
                }
            present = operand.id in members[member_key]

            # The goal state, not the call, is what counts as success--so a
            # link already in place and one already gone are both reported and
            # left alone, exactly as update_gh_labels.sh treats a label.
            if adding and present:
                print(f"Already linked: {described}.")
                continue
            if not adding and not present:
                print(f"Already unlinked: {described}.")
                continue
            # The cache has to follow the write, or a reference repeated in
            # one call would be re-sent against a link that is now there (or
            # gone): a 422 on add, and on remove the very 403 this script
            # exists to keep callers away from--both reported as failures after
            # the goal state had in fact been reached. A dry run updates it
            # too, so that its preview is of the real run and not of a first
            # step repeated.
            if args.dry_run:
                verb = "Would link" if adding else "Would unlink"
                print(f"{verb}: {described}.")
            elif adding:
                add_link(relation.family, holder, operand, token)
                print(f"Linked: {described}.")
            else:
                remove_link(relation.family, holder, operand, token)
                print(f"Unlinked: {described}.")

            if adding:
                members[member_key].add(operand.id)
            else:
                members[member_key].discard(operand.id)
        except ApiError as error:
            print(f"Error: {ref}: {error.message}", file=sys.stderr)
            exit_code = EXIT_FAILED

    return exit_code


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="link_gh_issues.py",
        description="Set semantic links (dependencies, sub-issues) between GitHub issues.",
        epilog=(
            "A REF is 123, #123, owner/repo#123, or a github.com issue URL; a bare number "
            "means the "
            "subject's own repository. The PR-to-issue 'fixes' link has no API--write "
            "`Fixes #123` in the pull request description instead."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_subject(subparser):
        subparser.add_argument("owner")
        subparser.add_argument("repo")
        subparser.add_argument("issue", type=int, help="the subject issue number")

    show = subparsers.add_parser("show", help="print every link on an issue")
    add_subject(show)
    show.add_argument("--json", action="store_true", help="print the raw API payloads")

    for name, help_text in (
        ("add", "create links"),
        ("remove", "delete links"),
    ):
        subparser = subparsers.add_parser(name, help=help_text)
        add_subject(subparser)
        for relation in RELATIONS:
            subparser.add_argument(
                relation.flag,
                dest=LINKS_DEST,
                action=RelationAction,
                default=None,
                metavar="REF",
                help=f"the subject {relation.phrase} REF",
            )
        subparser.add_argument(
            "--dry-run",
            action="store_true",
            help="report what would change without writing anything",
        )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("Error: GITHUB_TOKEN env var is not set.", file=sys.stderr)
        return EXIT_FAILED

    try:
        if args.command == "show":
            return run_show(args, token)
        return run_change(args, token, adding=args.command == "add")
    except ApiError as error:
        print(f"Error: {error.message}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
