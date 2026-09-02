#!/usr/bin/env python3
r"""link_gh_issues.py: set semantic links between GitHub issues via the REST API.

GitHub models "blocked by" and "blocking" natively, but no tool an agent has can
write them: the GitHub MCP server has `sub_issue_write` for hierarchy and nothing
for `/dependencies/blocked_by`. This script writes the native link, so it shows in
the issue sidebar and answers the `-is:blocked` search filters.

Follows `scripts/agents/update_gh_labels.sh` (issue #710) and
`scripts/agents/delete_gh_comment.sh` (issue #658): a plain REST call for
environments with no `gh` CLI, standard library only.

Relations
---------

Every relation is named from the point of view of the *subject* issue, the one
named in the positional arguments:

    --blocked-by REF    the subject cannot proceed until REF is done
    --blocks REF        REF cannot proceed until the subject is done
    --parent-of REF     REF becomes a sub-issue of the subject
    --child-of REF      the subject becomes a sub-issue of REF

GitHub serves one write direction per pair (`POST .../dependencies/blocked_by`
and `POST .../sub_issues`; there is no POST to `.../dependencies/blocking`), so
`--blocks` and `--child-of` are applied by writing to REF's endpoint with the
subject as the operand.

Both endpoint families take a database id rather than an issue number, so a REF
is resolved through a GET before the write.

An issue gets exactly one parent
--------------------------------

`--parent-of` and `--child-of` are the one place where an add can remove
something: giving an issue a second parent moves it, and the previous parent
loses the child. GitHub refuses the plain call with `422 Sub issue may only have
one parent` and accepts it only with `replace_parent`.

So the script reads the operand's current parent first, and refuses the move,
naming the issue about to lose the child, unless `--replace-parent` is given.

Pull requests are not linkable
------------------------------

GitHub takes issues only, on both sides of both link types; all four positions
answer 422. A pull request in any position is refused before the call. Link the
issue the pull request resolves instead.

Relations this script does not handle
------------------------------------

"Closes" (a pull request closing an issue) and "duplicate of" both exist, and
both have an API. Neither has the shape the four relations above share, so
neither is offered here.

Set "closes" with a closing keyword in the pull request description. Set
"duplicate of" by closing the issue with the duplicate state reason.

Usage
-----

    scripts/agents/link_gh_issues.py show <owner> <repo> <issue> [--json]

    scripts/agents/link_gh_issues.py add <owner> <repo> <issue> \
        [--blocked-by REF]... [--blocks REF]... \
        [--parent-of REF]... [--child-of REF]... \
        [--replace-parent] [--dry-run]

    scripts/agents/link_gh_issues.py remove <owner> <repo> <issue> \
        [--blocked-by REF]... ... [--dry-run]

REF may be given as `123`, `#123`, `owner/repo#123`, or a github.com issue URL.
A bare number means an issue in the same repository as the subject. Neither the
owner nor the repository may contain a `/`, and a URL on any other host is
refused rather than looked up on github.com.

Example, recording that issue #42 is waiting on #17 and #19 in one call:

    scripts/agents/link_gh_issues.py add aunger gallery-button-for-pixel-camera 42 \
        --blocked-by 17 --blocked-by 19

Adding a link that is already present, or removing one that is already absent, is
reported and treated as success: the goal state is what matters, matching
`scripts/agents/update_gh_labels.sh`.

Exit codes:
    0   every requested link reached its goal state
    1   at least one did not (the failure is reported on stderr)

Required environment variables:
    GITHUB_TOKEN   Token with `issues: write` on every repository written to.
                   Read access alone is enough for `show`.
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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects rather than follow them.

    GitHub redirects a renamed owner or repository. urllib follows that by
    rewriting a redirected POST into a GET, which would return the membership
    list and let a write report success having written nothing.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)

REQUEST_TIMEOUT_SECONDS = 15

# A 5xx or a secondary rate limit is worth one retry; a 4xx is a decision and
# is reported as-is. Only idempotent methods retry: a POST that reached GitHub
# and lost its response would be refused as a duplicate on the second attempt,
# reporting a link that was in fact created as a failure.
RETRY_DELAY_SECONDS = 2.0
# The one retry is worth taking only if it is soon. A wait longer than this is
# declined rather than slept through: sleeping minutes would hang the script,
# and waking early would land inside the same refusal.
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


def _header(headers: object, name: str) -> str | None:
    """One response header, or None. A hand-built error may carry no mapping at all."""
    return headers.get(name) if hasattr(headers, "get") else None


def _quota_spent(headers: object) -> bool:
    """Whether GitHub says the quota is gone, which is what makes its reset a wait."""
    return _header(headers, "X-RateLimit-Remaining") == "0"


def _retry_delay(headers: object) -> float | None:
    """How long to wait before the one retry, or None to report instead.

    GitHub says when to retry with `Retry-After` (delta seconds) on a 429 or a
    secondary limit, and with `X-RateLimit-Reset` (a Unix timestamp) on a
    primary one. A wait past the cap is not worth taking, since the retry would
    be refused again; the caller is told instead.

    `X-RateLimit-Reset` rides on every response, not only a refused one, and it
    sits as much as an hour out. Reading it on a 500 would decline the one
    retry a 5xx exists to get, so it counts only once the quota is spent.
    """
    candidates = [(_header(headers, "Retry-After"), False)]
    if _quota_spent(headers):
        candidates.append((_header(headers, "X-RateLimit-Reset"), True))
    for value, is_timestamp in candidates:
        try:
            seconds = float(value) - (time.time() if is_timestamp else 0.0)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if seconds > MAX_RETRY_DELAY_SECONDS:
            return None
        return max(RETRY_DELAY_SECONDS, seconds)
    return RETRY_DELAY_SECONDS


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
                str(detail)
                for detail in (
                    e.get("message") or e.get("code") for e in errors if isinstance(e, dict)
                )
                if detail
            ]
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
    elif status in (301, 302, 303, 307, 308):
        message += (
            "--the owner or repository was redirected, which usually means it was renamed. "
            "Nothing was written; re-run with the current owner and repository."
        )
    elif status == 401:
        message += "--check that GITHUB_TOKEN is set and valid."
    return f"HTTP {status}: {message}"


def _parse(raw: str, status: int) -> object:
    """Parse a success body, reporting a non-JSON one rather than raising past main."""
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise ApiError(
            status, f"HTTP {status}: response was not JSON: {raw.strip()[:120]}"
        ) from None


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
            with _OPENER.open(  # nosemgrep
                request, timeout=REQUEST_TIMEOUT_SECONDS
            ) as response:
                raw = response.read().decode("utf-8", "replace")
                return response.status, _parse(raw, response.status)
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8", "replace")
            try:
                payload = json.loads(raw) if raw.strip() else None
            except json.JSONDecodeError:
                payload = None
            rate_limited = error.code == 403 and (
                "secondary rate limit" in raw.lower() or _quota_spent(error.headers)
            )
            if (error.code in RETRYABLE_STATUSES or rate_limited) and attempt < attempts - 1:
                delay = _retry_delay(error.headers)
                if delay is not None:
                    time.sleep(delay)
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

    `missing_ok` covers the first page only. A later page that cannot be read
    is not "nothing is linked": the earlier pages proved otherwise, so
    returning what was collected would truncate the list without saying so.
    """
    items: list = []
    page = 1
    while True:
        status, payload = api("GET", f"{path}?per_page=100&page={page}", token)
        if status == 404 or not isinstance(payload, list):
            if page > 1 or not missing_ok:
                raise ApiError(
                    status,
                    f"HTTP {status}: {path} page {page} could not be read, "
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

# All four flags share one argparse destination so the report comes out in the
# order the flags were typed; `action="append"` would give one list per flag,
# recoverable only in RELATIONS order.
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


def parent_of(issue: Issue, token: str) -> tuple[int, str] | None:
    """The issue's one parent as (id, slug), or None if it has none.

    This gates a write, so the two ways of reading nothing are held apart.

    **A 404 is read as "no parent", deliberately.** This runs only after
    `get_issue` has fetched the operand, so by here the issue is known to exist
    and to be visible, and the remaining cause of a 404 is the one GitHub names
    in the body: `No parent issue found` (checked 2026-09-01 against a
    parentless issue, where a generic absence would have answered `Not Found`).
    The two causes are not confusable in this position, which is why this reads
    404 leniently where `paged` does not.

    **Anything else unreadable raises.** A payload that is not an issue object,
    or one missing the `id` or `number` this returns, is not evidence of
    anything and must not be reported as "no parent": the caller would then
    write without `replace_parent`, silently discarding a `--replace-parent` it
    was given, and get GitHub's bare 422 back instead of the line this guard
    exists to provide. That is the line `paged` draws for the membership
    pre-read, for the same reason.

    The slug is carried along because the caller has to name the parent it is
    about to displace, and re-fetching the issue to learn its number would be a
    second call for something this payload already holds.
    """
    path = f"/repos/{issue.owner}/{issue.repo}/issues/{issue.number}/parent"
    status, payload = api("GET", path, token)
    if status == 404:
        return None
    if not isinstance(payload, dict) or "id" not in payload or "number" not in payload:
        raise ApiError(
            status,
            f"HTTP {status}: {path} did not answer with an issue, so whether {issue.slug} "
            "already has a parent is unknown",
        )
    where = (payload.get("repository") or {}).get("full_name") or f"{issue.owner}/{issue.repo}"
    return int(payload["id"]), f"{where}#{payload['number']}"


def add_link(
    family: str, holder: Issue, other: Issue, token: str, replace_parent: bool = False
) -> None:
    """Create the link, writing to `holder`'s endpoint with `other` as operand.

    `api` hands 404 back rather than raising, because for the reads here it is
    an answer. For a write it never is: nothing was created, so reporting
    `Linked` off a 404 would be a plain false success. `update_gh_labels.sh`
    draws the same line, treating 404 on its add as an error.

    `replace_parent` is meaningful only for the sub-issue family, which is the
    only one with a one-per-issue rule to break; see `run_change`.
    """
    key = "issue_id" if family == "dependency" else "sub_issue_id"
    body: dict[str, object] = {key: other.id}
    if replace_parent and family == "sub-issue":
        body["replace_parent"] = True
    status, payload = api("POST", members_path(family, holder), token, body)
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
    # Read here rather than through `parent_of`, which returns the id and slug
    # a write needs while `--json` needs GitHub's payload. A parentless issue
    # answers 404, which is an answer; no write is gated on it.
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
        # Count what is about to be printed. An entry that is not an issue
        # object has no line to print, and counting it names more links than
        # the rows beneath the count.
        rows = [item for item in items if isinstance(item, dict)]
        if not rows:
            print(f"  {label}: none")
            continue
        print(f"  {label} ({len(rows)}):" if counted else f"  {label}:")
        for row in rows:
            print(issue_line(row))
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
    # The membership read is per (family, holder), not per link. The subject is
    # seeded so that a reference back to it costs no fetch before the self-link
    # guard below rejects it.
    resolved: dict[tuple[str, str, int], Issue] = {
        (subject.owner.lower(), subject.repo.lower(), subject.number): subject
    }
    members: dict[tuple[str, str, str, int], set[int]] = {}
    # Each issue's current parent as (id, slug), read lazily and only for
    # sub-issue adds. `None` is a real answer here ("no parent"), so membership
    # in the dict is what says whether it has been read.
    parents: dict[int, tuple[int, str] | None] = {}
    replace_parent = bool(getattr(args, "replace_parent", False))

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
            # Neither family takes a pull request in either position; GitHub
            # answers all four with a 422. Refusing here turns that into one
            # line naming the fallback. Only on an add: an issue converted to a
            # pull request after being linked leaves a link to delete, and the
            # advice below would make no sense for a removal.
            if adding and (holder.is_pull_request or operand.is_pull_request):
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

            # An issue gets exactly one parent, so an add that would give it a
            # second is a move: the previous parent loses the child. GitHub
            # refuses without `replace_parent`, so the choice is the caller's.
            # Naming the current parent is what GitHub's message omits.
            displaced = None
            if adding and relation.family == "sub-issue":
                if operand.id not in parents:
                    parents[operand.id] = parent_of(operand, token)
                current = parents[operand.id]
                if current is not None and current[0] != holder.id:
                    if not replace_parent:
                        print(
                            f"Error: {described}: {operand.slug} is already a sub-issue of "
                            f"{current[1]}, and GitHub gives an issue one parent, so this "
                            f"would move it out of {current[1]}. Pass --replace-parent to "
                            f"move it, or remove the existing parent link first.",
                            file=sys.stderr,
                        )
                        exit_code = EXIT_FAILED
                        continue
                    displaced = current

            moved = f" (replacing {displaced[1]} as its parent)" if displaced else ""
            # The caches follow the write, or a reference repeated in one call
            # is re-sent against a link that is now present (or gone) and the
            # refusal is reported as a failure after the goal state was reached.
            # A dry run updates them too, so its preview matches the real run.
            if args.dry_run:
                verb = "Would link" if adding else "Would unlink"
                print(f"{verb}: {described}{moved}.")
            elif adding:
                add_link(
                    relation.family, holder, operand, token, replace_parent=displaced is not None
                )
                print(f"Linked: {described}{moved}.")
            else:
                remove_link(relation.family, holder, operand, token)
                print(f"Unlinked: {described}.")

            if adding:
                if relation.family == "sub-issue":
                    # The operand has exactly one parent now, so any other
                    # cached sub-issue set that still lists it is stale.
                    for cached_key, ids in members.items():
                        if cached_key[0] == "sub-issue":
                            ids.discard(operand.id)
                    parents[operand.id] = (holder.id, holder.slug)
                members[member_key].add(operand.id)
            else:
                members[member_key].discard(operand.id)
                if relation.family == "sub-issue":
                    parents[operand.id] = None
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
            "means the subject's own repository. The 'closes' and 'duplicate of' relations "
            "are not handled here; see the module docstring."
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
        if name == "add":
            subparser.add_argument(
                "--replace-parent",
                action="store_true",
                help=(
                    "allow --parent-of / --child-of to move a sub-issue that already has a "
                    "different parent; without this such a move is refused"
                ),
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
