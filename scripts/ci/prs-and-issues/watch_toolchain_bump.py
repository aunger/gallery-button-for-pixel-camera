#!/usr/bin/env python3
"""Watch for the upstream movement that could make a toolchain bump available.

This is a watcher, not a dependency bumper.
`gradle/README.md` records that this repository has no automated dependency bumper by design.
Nothing here edits a version, and filing or commenting on an issue does not move the dependency
graph.  All this job does is notice that one narrow question has a new answer, and say so once.

The question is not "what versions exist".  It is:

    Is there now a stable KGP row whose fully-supported Gradle maximum is above what we run, and
    does the CodeQL Kotlin ceiling still cover that row's Kotlin version?

AGP and Gradle patch releases are noise unless that row moves.

Two facts are tracked, and a comment is posted only when the pair changes:

1.  The highest stable ``org.jetbrains.kotlin:kotlin-gradle-plugin`` version published to Maven
    Central.

    The KGP compatibility table on kotlinlang.org is deliberately NOT scraped.  It is the one
    input with no machine-readable form, and it is also the input that needs human judgment to
    apply: a fully-supported maximum versus a deliberately accepted overage, per the #774
    rationale recorded in `gradle/README.md`.  A new stable KGP release is the event that can
    move that row, so watching releases gets the same trigger without the brittle HTML
    dependency.  When this watcher fires, a human re-reads the table by hand.

2.  The CodeQL Kotlin upper bound, computed by executing the procedure `gradle/README.md`
    describes rather than restating it: read ``cliVersion`` from ``src/defaults.json`` in
    ``github/codeql-action`` at the ref ``.github/workflows/codeql.yml`` actually uses, then read
    the Kotlin row of ``docs/codeql/reusables/supported-versions-compilers.rst`` in
    ``github/codeql`` at tag ``codeql-cli/v<cliVersion>``.

Alongside those, OSV is queried for the Maven coordinates tied to the three toolchain pins this
repository holds, at the versions each pin resolves to.  Narrowness to the pinned toolchain is the
point.  Scanning every artifact in `gradle/verification-metadata.xml` measures the build graph
rather than the APK, and reports the same few dozen standing build-tool advisories forever, which
trains the reader to ignore the job.  An advisory against one of the pinned coordinates is a
different thing: it is directly actionable, and it is a reason to bump that overrides the standing
"no urgency" default.

A pin's own build-plugin coordinate is not always where its advisories are filed.
`org.jetbrains.kotlin:kotlin-gradle-plugin` has never itself carried an OSV advisory; Kotlin's
advisories are filed against `org.jetbrains.kotlin:kotlin-stdlib` instead, which Kotlin releases
at the same version as KGP, so both are queried for the KGP pin (#820).  No such companion
coordinate is known for AGP, so it stays a single coordinate.

State lives in this script's own most recent comment on the tracking issue, embedded in an HTML
comment.  There is no state file, so the workflow needs no ``contents: write``.

Fail-loud posture, matching the guard scripts in `scripts/test_verification_metadata.sh`.  A
document that is fetched but cannot be parsed (or has moved, so its URL 404s) is reported as an
upstream format change, never silently folded into a "no movement" result.  A network-level
failure is different: it is evidence of nothing, so the run logs it and posts nothing rather than
recording a bogus observation.

Usage:
    python3 scripts/ci/prs-and-issues/watch_toolchain_bump.py
    python3 scripts/ci/prs-and-issues/watch_toolchain_bump.py --dry-run

``--dry-run`` prints the observed state and the comment that would be posted, and touches no
GitHub state.  It needs no token.

Exit code is always 0; API and network failures are logged but do not fail the CI run, matching
`archive_stale_test_failures.py`.

Required environment variables:
    GITHUB_TOKEN        Personal access token or Actions secret with issues: write
    GITHUB_REPOSITORY   Owner/repo (e.g. "aunger/gallery-button-for-pixel-camera")

Optional environment variables:
    REPO_ROOT           Repository checkout to read the pinned versions from.
                        Defaults to the checkout this script lives in.
"""

import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

import defusedxml.ElementTree as ET
import requests

# Bare intra-directory import, matching the rest of scripts/ci/**: these files are run as
# scripts, so their own directory is on sys.path.  The GitHub calls are reused from there rather
# than reimplemented.
from file_test_failure_issues import (
    IssueLookup,
    add_issue_comment,
    create_issue,
    github_headers,
    lookup_issue_by_title,
    reopen_issue,
)


# ---------------------------------------------------------------------------
# Tracking issue
# ---------------------------------------------------------------------------

TRACKING_ISSUE_TITLE = "Toolchain bump watch: KGP releases and the CodeQL Kotlin ceiling"

# The label the tracking issue carries, and the one the lookup identifies it by, so there is no
# way for the two to drift apart.  Deliberately not `orchestrate`: this issue is a noticeboard,
# not dispatchable work.  Treating an open issue as the holder of a "check whether a bump became
# available yet" duty is exactly the pattern #803 replaced.  It is asserted to exist by
# scripts/ci/test_label_existence_integration.py.
TRACKING_ISSUE_LABEL = "ci"

TRACKING_ISSUE_BODY = """\
# Toolchain bump watch

This issue is the noticeboard for
`scripts/ci/prs-and-issues/watch_toolchain_bump.py`, run weekly by
`.github/workflows/watch-toolchain-bump.yml`.

It is **not** dispatchable work, and the watcher is **not** a dependency bumper.
`gradle/README.md` records that this repository has no automated bumper by design.

The watcher tracks two facts and comments only when one of them changes:

1. The highest stable `org.jetbrains.kotlin:kotlin-gradle-plugin` version published to Maven
   Central.
2. The CodeQL Kotlin upper bound, computed by executing the procedure in `gradle/README.md`.

It also queries OSV for the Maven coordinates tied to the three toolchain pins this repository
holds, including a companion coordinate where a pin's own build-plugin coordinate is a null OSV
channel (Kotlin's advisories are filed against `org.jetbrains.kotlin:kotlin-stdlib`, not
`kotlin-gradle-plugin`).

The KGP compatibility table on kotlinlang.org is not scraped: it has no machine-readable form,
and applying it needs judgment.  A new stable KGP release is the event that can move the row, so
when this issue gets a comment, the next step is for a human to re-read that table by hand and
follow "Performing a toolchain bump" in `gradle/README.md`.

Each observation is added as a comment.  The most recent comment also carries the watcher's
state, so no state file is needed.

Do not close this issue to silence it; a closed issue is reopened on the next change.  Disable
the workflow instead.
"""

# Marker that identifies a state-bearing comment.  The state JSON sits inside an HTML comment so
# the rendered comment stays readable while the next run can still recover the previous
# observation from it.
STATE_MARKER = "toolchain-bump-watch-state"


# ---------------------------------------------------------------------------
# Upstream sources
# ---------------------------------------------------------------------------

KGP_MAVEN_METADATA_URL = (
    "https://repo1.maven.org/maven2/org/jetbrains/kotlin/kotlin-gradle-plugin/maven-metadata.xml"
)

CODEQL_DEFAULTS_URL = (
    "https://raw.githubusercontent.com/github/codeql-action/{ref}/src/defaults.json"
)

CODEQL_SUPPORTED_VERSIONS_URL = (
    "https://raw.githubusercontent.com/github/codeql/codeql-cli/v{cli_version}"
    "/docs/codeql/reusables/supported-versions-compilers.rst"
)

OSV_QUERY_URL = "https://api.osv.dev/v1/query"

# The coordinates whose advisories would be directly actionable, keyed by the pin each is read
# from and queried at.  Gradle itself is not published to Maven Central as a single "gradle"
# artifact; org.gradle:gradle-core is the coordinate OSV advisories against the Gradle build tool
# are filed under.
#
# #820 widened this list beyond one coordinate per pin, since a pin's own build-plugin coordinate
# is not always where its advisories live.  org.jetbrains.kotlin:kotlin-gradle-plugin has never
# itself carried an OSV advisory (queried package-wide, no version filter, both here and by the
# #812 reviewer); Kotlin's advisories (for example GHSA-2qp4-g3q3-f92w, GHSA-cqj8-47ch-rvvq) are
# filed against org.jetbrains.kotlin:kotlin-stdlib instead.  Kotlin releases stdlib at the same
# version as KGP, so it is queried at the KGP pin alongside kotlin-gradle-plugin itself.
#
# No comparable companion coordinate was found for AGP: com.android.tools.build:gradle and every
# other com.android.tools(.build) artifact checked while widening this list (gradle-api, builder,
# aapt2, apksig, manifest-merger, sdk-common, bundletool, and more) have never carried an OSV
# advisory either, so the AGP channel stays a single coordinate until a real one turns up.
COORDINATES_BY_PIN: dict[str, list[str]] = {
    "gradle": ["org.gradle:gradle-core"],
    "agp": ["com.android.tools.build:gradle"],
    "kgp": [
        "org.jetbrains.kotlin:kotlin-gradle-plugin",
        "org.jetbrains.kotlin:kotlin-stdlib",
    ],
}

HUMAN_PIN_NAME: dict[str, str] = {
    "gradle": "Gradle",
    "agp": "AGP",
    "kgp": "KGP",
}


class FormatError(Exception):
    """A document was read but is not in the shape this script expects.

    Reported loudly on the tracking issue: a format change means the watcher can no longer answer
    its question, which must never be mistaken for "nothing moved".
    """


class TransientFetchError(Exception):
    """A document could not be fetched at all.

    Evidence of nothing, so the run logs it and reports nothing rather than recording an
    observation built from a hole.
    """


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def fetch_text(url: str) -> str:
    """Return the body of *url*.

    A 404 is a FormatError, not a TransientFetchError: these URLs are built from paths and tags
    this script expects upstream to keep, so a missing document means upstream moved it.
    """
    try:
        resp = requests.get(url, timeout=30)
    except Exception as exc:  # noqa: BLE001
        raise TransientFetchError(f"GET {url} failed: {exc}") from exc
    if resp.status_code == 404:
        raise FormatError(f"GET {url} returned 404; upstream moved or renamed this document")
    try:
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise TransientFetchError(f"GET {url} failed: {exc}") from exc
    return resp.text


def query_osv(coordinate: str, version: str) -> list[str]:
    """Return the sorted advisory IDs OSV reports for *coordinate* at *version*."""
    payload = {"package": {"name": coordinate, "ecosystem": "Maven"}, "version": version}
    try:
        resp = requests.post(OSV_QUERY_URL, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise TransientFetchError(f"OSV query for {coordinate}@{version} failed: {exc}") from exc
    if not isinstance(data, dict):
        raise FormatError(f"OSV returned a {type(data).__name__}, not an object")
    return sorted(vuln["id"] for vuln in data.get("vulns", []) if "id" in vuln)


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

_STABLE_VERSION_RE = re.compile(r"^\d+(?:\.\d+)*$")


def is_stable(version: str) -> bool:
    """Return True for a purely numeric dotted version (no -Beta1, -RC2, -test-deploy)."""
    return bool(_STABLE_VERSION_RE.match(version))


def version_key(version: str) -> tuple[int, ...]:
    """Return a sortable key for a numeric dotted version."""
    return tuple(int(part) for part in version.split("."))


def ceiling_inclusive_max(bound: str) -> str:
    """Expand a trailing-``x`` wildcard into the highest version the bound admits.

    CodeQL writes its Kotlin upper bound with a trailing-``x`` wildcard for the final digit, so
    ``2.4.1x`` means "any 2.4.1 followed by one more digit", that is, up to 2.4.19 and not as far
    as 2.4.20.  A bound with no wildcard is already its own maximum.
    """
    if bound.endswith("x"):
        return bound[:-1] + "9"
    return bound


def ceiling_covers(ceiling: str | None, version: str | None) -> bool | None:
    """Return whether *ceiling* admits *version*, or None when either is unknown."""
    if not ceiling or not version:
        return None
    inclusive_max = ceiling_inclusive_max(ceiling)
    if not is_stable(inclusive_max) or not is_stable(version):
        return None
    return version_key(version) <= version_key(inclusive_max)


# ---------------------------------------------------------------------------
# Reading this repository's pins
# ---------------------------------------------------------------------------

_GRADLE_DISTRIBUTION_RE = re.compile(r"gradle-(\d[\w.\-]*?)-bin\.zip")
_AGP_RE = re.compile(r'id\("com\.android\.application"\)\s+version\s+"([^"]+)"')
_KGP_RE = re.compile(r'classpath\("org\.jetbrains\.kotlin:kotlin-gradle-plugin:([^"]+)"\)')
_CODEQL_ACTION_REF_RE = re.compile(r"github/codeql-action/[\w-]+@([\w.\-/]+)")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FormatError(f"cannot read {path.name}: {exc}") from exc


def _search(pattern: re.Pattern[str], text: str, what: str, where: str) -> str:
    match = pattern.search(text)
    if match is None:
        raise FormatError(f"cannot find the {what} pin in {where}")
    return match.group(1)


def read_pinned_versions(repo_root: Path) -> dict[str, str]:
    """Return the Gradle, AGP, and KGP versions this checkout pins.

    Read from the tree rather than hardcoded, so a bump does not also require editing the
    watcher, and so the watcher cannot quietly compare against a stale pin.
    """
    wrapper = _read(repo_root / "gradle" / "wrapper" / "gradle-wrapper.properties")
    build = _read(repo_root / "build.gradle.kts")
    return {
        "gradle": _search(
            _GRADLE_DISTRIBUTION_RE, wrapper, "Gradle distribution", "gradle-wrapper.properties"
        ),
        "agp": _search(_AGP_RE, build, "AGP", "build.gradle.kts"),
        "kgp": _search(_KGP_RE, build, "KGP", "build.gradle.kts"),
    }


def read_codeql_action_ref(repo_root: Path) -> str:
    """Return the ref codeql.yml pins github/codeql-action to (e.g. "v4").

    gradle/README.md's procedure says to read defaults.json "at the ref codeql.yml actually
    uses", so the ref is read from the workflow instead of being duplicated here.
    """
    workflow = _read(repo_root / ".github" / "workflows" / "codeql.yml")
    refs = _CODEQL_ACTION_REF_RE.findall(workflow)
    if not refs:
        raise FormatError("codeql.yml references no github/codeql-action step")
    counted = Counter(refs)
    if len(counted) > 1:
        print(
            f"  Warning: codeql.yml pins github/codeql-action to more than one ref "
            f"({sorted(counted)}); using the most common.",
            file=sys.stderr,
        )
    return counted.most_common(1)[0][0]


# ---------------------------------------------------------------------------
# Reading the upstream facts
# ---------------------------------------------------------------------------


def parse_latest_stable_kgp(metadata_xml: str) -> str:
    """Return the highest stable version in a Maven ``maven-metadata.xml`` document.

    The document's own ``<latest>`` and ``<release>`` elements are not used: Kotlin sets both to
    the newest upload, which is routinely a Beta or RC (``2.4.20-Beta2`` at the time of writing).
    The version list is filtered here instead.
    """
    try:
        root = ET.fromstring(metadata_xml)
    except ET.ParseError as exc:
        raise FormatError(f"KGP maven-metadata.xml is not well-formed XML: {exc}") from exc
    versions = [el.text.strip() for el in root.findall("./versioning/versions/version") if el.text]
    if not versions:
        raise FormatError("KGP maven-metadata.xml lists no <version> elements")
    stable = [version for version in versions if is_stable(version)]
    if not stable:
        raise FormatError("KGP maven-metadata.xml lists no stable (non-prerelease) version")
    return max(stable, key=version_key)


def fetch_latest_stable_kgp() -> str:
    return parse_latest_stable_kgp(fetch_text(KGP_MAVEN_METADATA_URL))


def parse_codeql_cli_version(defaults_json: str) -> str:
    try:
        defaults = json.loads(defaults_json)
    except json.JSONDecodeError as exc:
        raise FormatError(f"codeql-action defaults.json is not valid JSON: {exc}") from exc
    cli_version = defaults.get("cliVersion") if isinstance(defaults, dict) else None
    if not isinstance(cli_version, str) or not cli_version:
        raise FormatError("codeql-action defaults.json has no cliVersion string")
    return cli_version


def fetch_codeql_cli_version(ref: str) -> str:
    return parse_codeql_cli_version(fetch_text(CODEQL_DEFAULTS_URL.format(ref=ref)))


# RST footnote reference, as in ``Kotlin [7]_`` or ``Swift [12]_ [13]_``.
_RST_FOOTNOTE_RE = re.compile(r"\s*\[\d+\]_")


def _strip_rst_markup(line: str) -> str:
    """Flatten the RST inline markup used in the supported-versions table.

    ``2.4.1\\ *x*`` is an escaped space joining an emphasised ``x`` to the version, and renders as
    ``2.4.1x``.

    Footnote references are dropped as well.  The Kotlin row carried one (``Kotlin [7]_``) while
    Kotlin support was in beta, and six of the thirteen rows still carry one today, so a caveat
    added back to Kotlin must not read as "the table has no Kotlin row".
    """
    line = _RST_FOOTNOTE_RE.sub("", line)
    return line.replace("\\ ", "").replace("\\", "").replace("*", "").replace("`", "")


_KOTLIN_LABEL = "Kotlin,"

# The upper end of a version range, e.g. the "2.4.1x" of "Kotlin 1.8.0 to 2.4.1x".  Deliberately
# does not require the cell to repeat the language name: most rows of this table do not
# ("2.6-5.9" for TypeScript, "up to 3.3" for Ruby), so the Kotlin row being normalized that way
# should not break the parse.
_VERSION_CEILING_RE = re.compile(r"\bto\s+(\d+(?:\.\d+)*x?)(?![\w.])")


def _first_csv_cell(text: str) -> str:
    """Return the first comma-separated cell of *text*, unquoted if it was quoted."""
    text = text.strip()
    if text.startswith('"'):
        closing = text.find('"', 1)
        return text[1:closing] if closing != -1 else text[1:]
    return text.split(",", 1)[0]


def parse_kotlin_ceiling(rst_text: str) -> str:
    """Return the Kotlin upper bound from the CodeQL supported-versions table.

    Raises FormatError rather than guessing, so an upstream reformat is reported as such instead
    of silently reading as "no movement".  The two tolerated variations above (a footnote on the
    label, and a Variants cell that does not repeat "Kotlin") are cosmetic shapes upstream has
    actually used; anything beyond them is a real reformat and is reported.
    """
    for raw_line in rst_text.splitlines():
        line = _strip_rst_markup(raw_line).strip()
        if not line.startswith(_KOTLIN_LABEL):
            continue
        variants = _first_csv_cell(line[len(_KOTLIN_LABEL) :])
        match = _VERSION_CEILING_RE.search(variants)
        if match is None:
            raise FormatError(
                "the Kotlin row of the CodeQL supported-versions table no longer states its "
                f'upper bound as "to <version>": {line!r}'
            )
        return match.group(1)
    raise FormatError("the CodeQL supported-versions table has no Kotlin row")


def fetch_codeql_kotlin_ceiling(cli_version: str) -> str:
    return parse_kotlin_ceiling(
        fetch_text(CODEQL_SUPPORTED_VERSIONS_URL.format(cli_version=cli_version))
    )


# ---------------------------------------------------------------------------
# Observed state
# ---------------------------------------------------------------------------

# Human-readable name for each tracked fact, in the order it is rendered.  Also the set of keys
# compared between runs: a comment is posted when any of them differs from the last observation.
FACT_LABELS: dict[str, str] = {
    "pinned": "versions pinned in this repository",
    "latest_stable_kgp": "highest stable KGP published",
    "codeql_action_ref": "codeql-action ref used by codeql.yml",
    "codeql_cli_version": "CodeQL CLI version",
    "codeql_kotlin_ceiling": "CodeQL Kotlin ceiling",
    "toolchain_advisories": "advisories against the pinned toolchain",
    "errors": "upstream readability",
}


def _collect(errors: list[str], func, *args):
    """Run *func*, folding a FormatError into *errors* and returning None."""
    try:
        return func(*args)
    except FormatError as exc:
        errors.append(str(exc))
        return None


def collect_state(repo_root: Path) -> dict:
    """Observe every tracked fact.

    Raises TransientFetchError if an upstream document could not be fetched at all; the caller
    reports nothing in that case.
    """
    errors: list[str] = []
    pinned = _collect(errors, read_pinned_versions, repo_root) or {}
    codeql_ref = _collect(errors, read_codeql_action_ref, repo_root)
    latest_stable_kgp = _collect(errors, fetch_latest_stable_kgp)

    cli_version = _collect(errors, fetch_codeql_cli_version, codeql_ref) if codeql_ref else None
    ceiling = _collect(errors, fetch_codeql_kotlin_ceiling, cli_version) if cli_version else None

    # A coordinate whose query failed is recorded as None, not as an empty list: "we could not
    # ask" must not render as "no advisories", which is what every other unreadable fact does.
    advisories: dict[str, list[str] | None] = {}
    for pin, coordinates in COORDINATES_BY_PIN.items():
        version = pinned.get(pin)
        if not version:
            continue
        for coordinate in coordinates:
            advisories[f"{coordinate}@{version}"] = _collect(errors, query_osv, coordinate, version)

    return {
        "pinned": pinned,
        "latest_stable_kgp": latest_stable_kgp,
        "codeql_action_ref": codeql_ref,
        "codeql_cli_version": cli_version,
        "codeql_kotlin_ceiling": ceiling,
        "toolchain_advisories": advisories,
        "errors": errors,
    }


def changed_facts(state: dict, prior: dict | None) -> list[str]:
    """Return the human-readable names of the facts that moved since *prior*."""
    if prior is None:
        return []
    return [label for key, label in FACT_LABELS.items() if prior.get(key) != state.get(key)]


# ---------------------------------------------------------------------------
# State round-trip through the tracking issue's comments
# ---------------------------------------------------------------------------


def render_state_block(state: dict) -> str:
    return f"<!-- {STATE_MARKER}\n{json.dumps(state, indent=2, sort_keys=True)}\n-->"


def parse_state_block(comment_body: str) -> dict | None:
    """Return the state recorded in *comment_body*, or None if it carries none."""
    match = re.search(rf"<!--\s*{STATE_MARKER}\s*(.*?)-->", comment_body or "", re.DOTALL)
    if match is None:
        return None
    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def latest_recorded_state(comments: list[dict]) -> dict | None:
    """Return the state from the most recent state-bearing comment, or None.

    Scanning from the end, and skipping comments with no marker, means a human replying on the
    tracking issue does not lose the watcher's place.
    """
    for comment in reversed(comments):
        state = parse_state_block(comment.get("body", ""))
        if state is not None:
            return state
    return None


# Page cap for the issue listing below. 20 pages of 100 is far more labelled issues than this
# repository will hold; exhausting it means the answer is unknown, not that the issue is absent.
_MAX_ISSUE_LIST_PAGES = 20


def confirm_tracking_issue_absent(token: str, repository: str) -> IssueLookup:
    """Re-check for the tracking issue against the issue list rather than the search index.

    GitHub's issue search is backed by an eventually-consistent index, so an issue created
    minutes ago can be missing from a search that otherwise succeeded.  Creating the tracking
    issue on that evidence would permanently duplicate the singleton the whole design rests on,
    and split the watcher's only state store across two comment feeds.

    GET /repos/{repo}/issues reads through instead of through the index, so it sees a
    just-created issue.  It is only worth its pagination cost on the one path that would
    otherwise take an irreversible action, so it is not used for the ordinary lookup.
    """
    url = f"https://api.github.com/repos/{repository}/issues"
    try:
        for page in range(1, _MAX_ISSUE_LIST_PAGES + 1):
            resp = requests.get(
                url,
                headers=github_headers(token),
                params={
                    "labels": TRACKING_ISSUE_LABEL,
                    "state": "all",
                    "per_page": 100,
                    "page": page,
                },
                timeout=30,
            )
            resp.raise_for_status()
            batch = resp.json()
            for issue in batch:
                # This endpoint returns pull requests as well as issues.
                if "pull_request" in issue:
                    continue
                if issue.get("title") == TRACKING_ISSUE_TITLE:
                    return IssueLookup(True, issue["number"], issue["state"])
            if len(batch) < 100:
                return IssueLookup(fetch_ok=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  Warning: listing {TRACKING_ISSUE_LABEL} issues failed: {exc}", file=sys.stderr)
        return IssueLookup(fetch_ok=False)

    print(
        f"  Warning: more than {_MAX_ISSUE_LIST_PAGES * 100} issues labelled "
        f"{TRACKING_ISSUE_LABEL}; cannot confirm the tracking issue is absent.",
        file=sys.stderr,
    )
    return IssueLookup(fetch_ok=False)


def find_tracking_issue(token: str, repository: str) -> IssueLookup:
    """Locate the tracking issue, keeping "absent" distinct from "could not tell".

    Only a confirmed absence may lead to creating one; see confirm_tracking_issue_absent.
    """
    lookup = lookup_issue_by_title(token, repository, TRACKING_ISSUE_TITLE, TRACKING_ISSUE_LABEL)
    if lookup.number is not None or not lookup.fetch_ok:
        return lookup
    return confirm_tracking_issue_absent(token, repository)


def fetch_issue_comments(token: str, repository: str, issue_number: int) -> list[dict]:
    """Return every comment on *issue_number*, oldest first."""
    comments: list[dict] = []
    url = f"https://api.github.com/repos/{repository}/issues/{issue_number}/comments"
    for page in range(1, 21):
        resp = requests.get(
            url,
            headers=github_headers(token),
            params={"per_page": 100, "page": page},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        comments.extend(batch)
        if len(batch) < 100:
            break
    return comments


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_UNKNOWN = "_not determined_"


def _show(value) -> str:
    if value is None or value == "":
        return _UNKNOWN
    return str(value)


def _advisory_cell(advisories: dict | None) -> str:
    if not advisories:
        return _UNKNOWN
    parts = []
    for coordinate, ids in sorted(advisories.items()):
        if ids is None:
            parts.append(f"{coordinate}: {_UNKNOWN}")
        elif ids:
            parts.append(f"{coordinate}: {', '.join(ids)}")
    return "; ".join(parts) if parts else "none"


def _pinned_cell(pinned: dict | None) -> str:
    if not pinned:
        return _UNKNOWN
    return ", ".join(
        f"{HUMAN_PIN_NAME.get(pin, pin)} {pinned[pin]}"
        for pin in COORDINATES_BY_PIN
        if pin in pinned
    )


def _fact_rows(state: dict, prior: dict | None) -> list[tuple[str, str, str]]:
    renderers = {
        "pinned": _pinned_cell,
        "toolchain_advisories": _advisory_cell,
        "errors": lambda value: "readable" if not value else f"{len(value)} problem(s)",
    }
    rows = []
    for key, label in FACT_LABELS.items():
        render = renderers.get(key, _show)
        now = render(state.get(key))
        was = render(prior.get(key)) if prior is not None else "_first observation_"
        rows.append((label, was, now))
    return rows


def _verdict_lines(state: dict) -> list[str]:
    """Return the interpretation of the observation, as Markdown lines."""
    lines: list[str] = []
    pinned_kgp = (state.get("pinned") or {}).get("kgp")
    latest = state.get("latest_stable_kgp")
    ceiling = state.get("codeql_kotlin_ceiling")

    if latest and pinned_kgp and is_stable(latest) and is_stable(pinned_kgp):
        if version_key(latest) > version_key(pinned_kgp):
            lines.append(f"A stable KGP above the pinned {pinned_kgp} is published: **{latest}**.")
        else:
            lines.append(f"No stable KGP above the pinned {pinned_kgp} is published yet.")

    covers = ceiling_covers(ceiling, latest)
    if covers is True:
        lines.append(
            f"The CodeQL Kotlin ceiling is `{ceiling}` (up to {ceiling_inclusive_max(ceiling)}), "
            f"which covers KGP {latest}."
        )
    elif covers is False:
        lines.append(
            f"The CodeQL Kotlin ceiling is `{ceiling}` (up to {ceiling_inclusive_max(ceiling)}), "
            f"which does **not** cover KGP {latest}; `analyze-kotlin` would fail on it."
        )

    hits = {coord: ids for coord, ids in (state.get("toolchain_advisories") or {}).items() if ids}
    if hits:
        lines.append(
            "OSV reports advisories against a pinned toolchain coordinate. Unlike the standing "
            "build-tool advisories in the wider dependency graph, this is directly actionable "
            "and overrides the usual no-urgency default:"
        )
        lines.extend(f"- `{coord}`: {', '.join(ids)}" for coord, ids in sorted(hits.items()))

    return lines


def render_comment(state: dict, prior: dict | None) -> str:
    """Build the Markdown comment recording *state*."""
    changed = changed_facts(state, prior)
    if prior is None:
        heading = "First observation recorded"
    elif changed:
        heading = "Changed: " + "; ".join(changed)
    else:
        # main() renders only when the states differ, so an empty change list means the recorded
        # state carries a key this version no longer tracks. Say that, rather than "Changed: ".
        heading = "Recorded state refreshed; no tracked fact moved"

    rows = "\n".join(f"| {label} | {was} | {now} |" for label, was, now in _fact_rows(state, prior))

    sections = [
        f"### Toolchain watch -- {heading}",
        "",
        "| Fact | Previously | Now |",
        "|------|------------|-----|",
        rows,
        "",
    ]

    errors = state.get("errors") or []
    if errors:
        sections += [
            "> [!WARNING]",
            "> An input could not be read, so this is **not** a report of no movement.",
            "> Whatever it says above about the affected fact is unknown, not unchanged.",
            "",
            *(f"- {error}" for error in errors),
            "",
        ]

    verdict = _verdict_lines(state)
    if verdict:
        sections += [*verdict, ""]

    sections += [
        "**Next step**: re-read the KGP compatibility table on "
        "[Configure a Gradle project](https://kotlinlang.org/docs/gradle-configure-project.html) "
        'by hand, then follow "Performing a toolchain bump" in `gradle/README.md`. '
        "This watcher deliberately does not scrape that table, and it does not bump anything.",
        "",
        render_state_block(state),
    ]
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _default_repo_root() -> Path:
    # scripts/ci/prs-and-issues/<this file>
    return Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    dry_run = "--dry-run" in argv

    repo_root = Path(os.environ.get("REPO_ROOT") or _default_repo_root())

    token = os.environ.get("GITHUB_TOKEN", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if not dry_run:
        if not token:
            print("Warning: GITHUB_TOKEN not set; skipping the toolchain watch.", file=sys.stderr)
            return 0
        if not repository:
            print(
                "Warning: GITHUB_REPOSITORY not set; skipping the toolchain watch.",
                file=sys.stderr,
            )
            return 0

    try:
        state = collect_state(repo_root)
    except TransientFetchError as exc:
        print(f"Note: {exc}; reporting nothing this run.", file=sys.stderr)
        return 0

    if dry_run:
        print(json.dumps(state, indent=2, sort_keys=True))
        print()
        print(render_comment(state, None))
        return 0

    lookup = find_tracking_issue(token, repository)
    if not lookup.fetch_ok:
        print(
            "Warning: could not determine whether the tracking issue exists; reporting nothing "
            "this run rather than risking a second one.",
            file=sys.stderr,
        )
        return 0

    if lookup.number is None:
        issue_number = create_issue(
            token,
            repository,
            TRACKING_ISSUE_TITLE,
            TRACKING_ISSUE_BODY,
            labels=[TRACKING_ISSUE_LABEL],
        )
        if issue_number is None:
            print("Warning: could not create the tracking issue.", file=sys.stderr)
            return 0
        print(f"Created tracking issue #{issue_number}.", file=sys.stderr)
        prior = None
    else:
        issue_number = lookup.number
        try:
            prior = latest_recorded_state(fetch_issue_comments(token, repository, issue_number))
        except Exception as exc:  # noqa: BLE001
            print(
                f"Warning: could not read prior state from issue #{issue_number}: {exc}; "
                "reporting nothing this run rather than risking a duplicate.",
                file=sys.stderr,
            )
            return 0
        if prior == state:
            print(f"No change since the last observation on #{issue_number}.", file=sys.stderr)
            return 0
        if lookup.state == "closed":
            reopen_issue(token, repository, issue_number)

    if add_issue_comment(token, repository, issue_number, render_comment(state, prior)):
        print(f"Reported an observation on #{issue_number}.", file=sys.stderr)
    else:
        print(
            f"Warning: could not post the observation on #{issue_number}; nothing was recorded, "
            "so the next run reports it again.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
