#!/usr/bin/env python3
"""Report how a pull request moves the resolved dependency graph (issue #1284).

A build file names some versions; Gradle resolves others.
Nothing else in this repository reports the gap, so a Dependabot pull request
whose diff bumps one coordinate can move a whole library family that neither
its title nor its diff mentions.
This module reads Gradle's `dependencies` report for the pull request's base
and head and turns the two into four findings, one per check:

- `dep-moves`: every library whose resolved version differs between base and
  head, a library that appears or disappears included.
  A move is *hidden* when its coordinate is on no changed line of a build file
  and it is not a member of a BOM or platform whose own declaration line
  changed.
- `dep-pin-drift`: every coordinate declared with an explicit version that
  resolves to a higher one.
- `dep-classpath-mismatch`: per variant, every library that resolves to
  different versions on the compile and the runtime classpath.
- `dep-compose-alignment`: every Compose artifact whose resolved version
  differs from the version the Compose BOM maps for it.

A finding is a report, never a failure.
Failing to look is a failure: a report that is missing, carries a `FAILED`
node, or has a line this parser does not recognize raises `ReportError`, and
the CLI exits non-zero.

The workflow that runs this is `.github/workflows/dependency-version-checks.yml`,
whose header covers the trigger, the two-workflow split and the commit status.
`post_dependency_checks.py` beside this file is the write half.
It renders comments and labels with the functions here, from findings it
validates with `load_findings`, so an artifact from a pull request's run is
read as data and never as markup.

Commands:

    dependency_checks.py configurations
        Print "<project> <configuration> <report file name>" per configuration
        read; collect_dependency_reports.sh runs Gradle for each.
    dependency_checks.py analyze BASE_DIR HEAD_DIR DIFF BASE_SHA HEAD_SHA OUT
        Compare the reports in BASE_DIR and HEAD_DIR, using the build-file
        diff in DIFF, and write the findings as JSON to OUT.
    dependency_checks.py skipped OUT
        Write the findings of a pull request that changed no build file.
    dependency_checks.py summaries FINDINGS OUT_DIR
        Validate FINDINGS and write each check's full report, as Markdown, to
        OUT_DIR/<check>.md, for that check's job to print in its summary.

Standard library only, so a workflow job can run it on the runner's own Python.
"""

import json
import re
import sys
from dataclasses import dataclass
from functools import cmp_to_key

# ---------------------------------------------------------------------------
# What is read
# ---------------------------------------------------------------------------

# The configurations every check reads, as (project path, configuration).
#
# The two shipped variants' compile and runtime classpaths are what
# dep-classpath-mismatch compares, and releaseRuntimeClasspath is what ships.
# The unit-test and androidTest runtime classpaths carry the declared test
# libraries, which are most of what Dependabot's test-dependencies group bumps.
# e2e-mock-gallery declares test libraries of its own; e2e-mock-camera declares
# none.
#
# The buildscript classpath (AGP, KGP) is not read: it builds the app rather
# than shipping in it, and gradle/README.md covers those pins.
#
# Both sides are read with the merge's copy of this list, so every entry has to
# exist on both. A pull request that removes one of these projects or variants
# has to drop it here too, or Gradle cannot report it on the head side; one that
# adds an entry for a project or variant it also introduces fails on the base
# side, which lacks it. Either way the checks fail on that pull request, so add
# an entry in a pull request after the one that introduces what it names.
CONFIGURATIONS: tuple[tuple[str, str], ...] = (
    (":app", "debugCompileClasspath"),
    (":app", "debugRuntimeClasspath"),
    (":app", "releaseCompileClasspath"),
    (":app", "releaseRuntimeClasspath"),
    (":app", "debugUnitTestRuntimeClasspath"),
    (":app", "debugAndroidTestRuntimeClasspath"),
    (":e2e-mock-gallery", "debugUnitTestRuntimeClasspath"),
)

# The variants dep-classpath-mismatch compares, as (project path, variant).
# Each names a "<variant>CompileClasspath" and "<variant>RuntimeClasspath" pair
# in CONFIGURATIONS.
CLASSPATH_VARIANTS: tuple[tuple[str, str], ...] = (
    (":app", "debug"),
    (":app", "release"),
)

COMPOSE_BOM = "androidx.compose:compose-bom"


def configuration_id(project: str, configuration: str) -> str:
    """Return a configuration's Gradle path, such as ":app:debugCompileClasspath"."""
    return f"{project}:{configuration}"


def report_file_name(project: str, configuration: str) -> str:
    """Return the file a configuration's report is saved under."""
    return f"{project.strip(':').replace(':', '-')}-{configuration}.txt"


# ---------------------------------------------------------------------------
# Checks and labels
# ---------------------------------------------------------------------------

DEP_CHANGE = "dep-moves"
DEP_PIN_DRIFT = "dep-pin-drift"
DEP_CLASSPATH_MISMATCH = "dep-classpath-mismatch"
DEP_COMPOSE_ALIGNMENT = "dep-compose-alignment"

CHECKS: tuple[str, ...] = (
    DEP_CHANGE,
    DEP_PIN_DRIFT,
    DEP_CLASSPATH_MISMATCH,
    DEP_COMPOSE_ALIGNMENT,
)

# Not Dependabot's own "dependencies" label, which it applies to every pull
# request it opens; this one says the resolved graph moved.
LABEL_CHANGE = "dep: change"
LABEL_HIDDEN = "dep: hidden change"
LABEL_PIN_DRIFT = "dep: pin drift"
LABEL_CLASSPATH_MISMATCH = "dep: classpath mismatch"
LABEL_COMPOSE_MISALIGNED = "dep: compose misaligned"

# Every label each check may apply, and so the only labels it may remove.
CHECK_LABELS: dict[str, tuple[str, ...]] = {
    DEP_CHANGE: (LABEL_CHANGE, LABEL_HIDDEN),
    DEP_PIN_DRIFT: (LABEL_PIN_DRIFT,),
    DEP_CLASSPATH_MISMATCH: (LABEL_CLASSPATH_MISMATCH,),
    DEP_COMPOSE_ALIGNMENT: (LABEL_COMPOSE_MISALIGNED,),
}

ALL_LABELS: frozenset[str] = frozenset(
    label for labels in CHECK_LABELS.values() for label in labels
)

# The key each check's rows are stored under in the findings.
ROWS_KEY: dict[str, str] = {
    DEP_CHANGE: "moves",
    DEP_PIN_DRIFT: "drifts",
    DEP_CLASSPATH_MISMATCH: "mismatches",
    DEP_COMPOSE_ALIGNMENT: "misaligned",
}

STATUS_CHECKED = "checked"
STATUS_SKIPPED = "skipped"

SCHEMA_VERSION = 1


class ReportError(Exception):
    """A dependency report could not be read, so the checks could not look."""


class FindingsError(Exception):
    """A findings file is not one this module wrote."""


# ---------------------------------------------------------------------------
# Version ordering
# ---------------------------------------------------------------------------


def _version_parts(version: str) -> list[int | str]:
    return [
        int(part) if part.isdigit() else part for part in re.findall(r"\d+|[a-z]+", version.lower())
    ]


def compare_versions(a: str, b: str) -> int:
    """Return -1, 0 or 1 as version *a* is lower than, equal to or higher than *b*.

    Close to Gradle's own ordering for the forms Maven repositories carry:
    numeric parts compare as numbers, a numeric part outranks a qualifier, and
    a qualifier where the shorter version has ended is a pre-release, so
    "1.0.0-alpha01" < "1.0.0" < "1.0.0.1".
    Qualifiers compare alphabetically ("alpha" < "beta" < "rc"), which Gradle
    refines with a few special names that no coordinate here uses.
    """
    pa, pb = _version_parts(a), _version_parts(b)
    for x, y in zip(pa, pb):
        if x == y:
            continue
        if isinstance(x, int) != isinstance(y, int):
            return 1 if isinstance(x, int) else -1
        return 1 if x > y else -1  # type: ignore[operator]
    if len(pa) == len(pb):
        return 0
    longer, sign = (pa, 1) if len(pa) > len(pb) else (pb, -1)
    next_part = longer[min(len(pa), len(pb))]
    return sign if isinstance(next_part, int) else -sign


# ---------------------------------------------------------------------------
# Parsing Gradle's dependencies report
# ---------------------------------------------------------------------------


@dataclass
class Node:
    """One line of a dependency tree."""

    library: str  # "group:artifact"
    requested: str | None  # the version asked for, None when it names none
    selected: str | None  # the version after "->", None when there is no arrow
    constraint: bool  # "(c)": a constraint rather than a dependency
    children: list["Node"]

    @property
    def version(self) -> str | None:
        """Return the version this node resolved to."""
        return self.selected or self.requested


# One node line. Each level of nesting is five characters, "|    " or "     ".
_NODE_RE = re.compile(r"^(?P<indent>(?:[| ]    )*)[+\\]--- (?P<body>.*?)\s*$")

_BODY_RE = re.compile(
    r"^(?P<group>[^\s:]+):(?P<artifact>[^\s:]+)"
    r"(?::(?P<requested>\{[^}]*\}|[^\s{}]+))?"
    r"(?: -> (?P<selected>\S+))?"
    r"(?P<markers>(?: \([*cn]\))*)$"
)

_PROJECT_BODY_RE = re.compile(r"^project \S+(?: \(\*\))?$")


def parse_report(text: str, configuration: str) -> list[Node]:
    """Return the root nodes of *configuration*'s tree in a `dependencies` report.

    Raises ReportError when the report has no section for the configuration,
    names a dependency Gradle failed to resolve, or has a tree line this parser
    does not recognize.
    """
    lines = text.splitlines()
    header = re.compile(rf"^{re.escape(configuration)}(?: - .*)?$")
    try:
        start = next(i for i, line in enumerate(lines) if header.match(line))
    except StopIteration:
        raise ReportError(f"the report has no section for {configuration}") from None

    roots: list[Node] = []
    stack: list[Node] = []  # stack[d] is the latest node at depth d
    for line in lines[start + 1 :]:
        if not line.strip() or line.strip() == "No dependencies":
            break
        match = _NODE_RE.match(line)
        if not match:
            raise ReportError(f"unrecognized line in {configuration}: {line!r}")
        body = match.group("body")
        depth = len(match.group("indent")) // 5
        if body.endswith(" FAILED"):
            raise ReportError(f"Gradle failed to resolve {body[: -len(' FAILED')]}")
        if _PROJECT_BODY_RE.match(body):
            node = Node(body, None, None, False, [])
        else:
            parsed = _BODY_RE.match(body)
            if not parsed:
                raise ReportError(f"unrecognized dependency in {configuration}: {body!r}")
            markers = parsed.group("markers")
            if "(n)" in markers:
                # Declared but not resolved; it says nothing about a version.
                node = None
            else:
                node = Node(
                    library=f"{parsed.group('group')}:{parsed.group('artifact')}",
                    requested=parsed.group("requested"),
                    selected=parsed.group("selected"),
                    constraint="(c)" in markers,
                    children=[],
                )
        if depth > len(stack):
            raise ReportError(f"a line in {configuration} skips a level: {line!r}")
        del stack[depth:]
        placeholder = node or Node("", None, None, True, [])
        if depth == 0:
            if node:
                roots.append(node)
        elif node:
            stack[depth - 1].children.append(node)
        stack.append(placeholder)
    return roots


def _walk(nodes: list[Node]):
    for node in nodes:
        yield node
        yield from _walk(node.children)


def resolved_versions(roots: list[Node]) -> dict[str, str]:
    """Return each library on the classpath, mapped to the version it resolved to."""
    versions: dict[str, str] = {}
    for node in _walk(roots):
        # A project dependency carries no version, so it is skipped with the rest.
        if node.constraint or node.version is None:
            continue
        versions.setdefault(node.library, node.version)
    return versions


def constraint_members(roots: list[Node], library: str) -> dict[str, str]:
    """Return the constraints *library* contributes, mapped to the version each asks for.

    Gradle expands a subtree once and abbreviates every later occurrence with
    "(*)", so this reads the first occurrence that has children.
    For a BOM, these are its members in the graph and the versions it maps.
    """
    for node in _walk(roots):
        if node.library == library and node.children:
            return {
                child.library: child.requested
                for child in node.children
                if child.constraint and child.requested
            }
    return {}


def load_reports(directory: str) -> dict[str, list[Node]]:
    """Return every configuration's tree from the reports saved in *directory*."""
    trees: dict[str, list[Node]] = {}
    for project, configuration in CONFIGURATIONS:
        path = f"{directory}/{report_file_name(project, configuration)}"
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError as exc:
            raise ReportError(f"cannot read the report {path}: {exc}") from None
        trees[configuration_id(project, configuration)] = parse_report(text, configuration)
    return trees


# ---------------------------------------------------------------------------
# Changed build-file lines
# ---------------------------------------------------------------------------

# A quoted "group:artifact" or "group:artifact:version" literal.
_COORDINATE_RE = re.compile(r'"([A-Za-z][\w.-]*):([\w.-]+)(?::[^"\s]*)?"')

_PLATFORM_RE = re.compile(r'\b(?:enforcedPlatform|platform)\s*\(\s*"([A-Za-z][\w.-]*):([\w.-]+)')


def changed_lines(diff_text: str) -> list[str]:
    """Return the added and removed lines of a unified diff, markers stripped."""
    return [
        line[1:]
        for line in diff_text.splitlines()
        if line[:1] in ("+", "-") and not line.startswith(("+++", "---"))
    ]


def coordinates_on(lines: list[str]) -> set[str]:
    """Return every "group:artifact" a quoted coordinate on *lines* names."""
    return {f"{g}:{a}" for line in lines for g, a in _COORDINATE_RE.findall(line)}


def platforms_on(lines: list[str]) -> set[str]:
    """Return every "group:artifact" declared as a platform on *lines*."""
    return {f"{g}:{a}" for line in lines for g, a in _PLATFORM_RE.findall(line)}


# ---------------------------------------------------------------------------
# The four checks
# ---------------------------------------------------------------------------


def find_moves(
    base: dict[str, list[Node]], head: dict[str, list[Node]], diff_text: str
) -> list[dict]:
    """Return every library whose resolved version differs between *base* and *head*."""
    lines = changed_lines(diff_text)
    explained = coordinates_on(lines)
    for platform in platforms_on(lines):
        for trees in (base, head):
            for roots in trees.values():
                explained |= constraint_members(roots, platform).keys()

    changes: dict[str, dict] = {}
    for config in head:
        before = resolved_versions(base[config])
        after = resolved_versions(head[config])
        for library in sorted(before.keys() | after.keys()):
            if before.get(library) == after.get(library):
                continue
            row = changes.setdefault(library, {"base": set(), "head": set(), "configurations": []})
            if library in before:
                row["base"].add(before[library])
            if library in after:
                row["head"].add(after[library])
            row["configurations"].append(config)

    return [
        {
            "library": library,
            "base": _sorted_versions(row["base"]),
            "head": _sorted_versions(row["head"]),
            "configurations": row["configurations"],
            "hidden": library not in explained,
        }
        for library, row in sorted(changes.items())
    ]


def find_pin_drift(head: dict[str, list[Node]]) -> list[dict]:
    """Return every coordinate declared with an explicit version that resolves higher."""
    drifts: dict[tuple[str, str, str], list[str]] = {}
    for config, roots in head.items():
        for node in roots:
            if node.constraint or not node.requested or not node.selected:
                continue
            if node.requested.startswith("{"):
                continue  # a rich version ("{strictly ...}") pins no single floor
            if compare_versions(node.selected, node.requested) > 0:
                key = (node.library, node.requested, node.selected)
                drifts.setdefault(key, []).append(config)
    return [
        {"library": lib, "declared": declared, "resolved": resolved, "configurations": configs}
        for (lib, declared, resolved), configs in sorted(drifts.items())
    ]


def find_classpath_mismatches(head: dict[str, list[Node]]) -> list[dict]:
    """Return, per variant, every library the compile and runtime classpaths disagree on."""
    rows = []
    for project, variant in CLASSPATH_VARIANTS:
        compile_versions = resolved_versions(
            head[configuration_id(project, f"{variant}CompileClasspath")]
        )
        runtime_versions = resolved_versions(
            head[configuration_id(project, f"{variant}RuntimeClasspath")]
        )
        for library in sorted(compile_versions.keys() & runtime_versions.keys()):
            if compile_versions[library] != runtime_versions[library]:
                rows.append(
                    {
                        "variant": configuration_id(project, variant),
                        "library": library,
                        "compile": compile_versions[library],
                        "runtime": runtime_versions[library],
                    }
                )
    return rows


def find_compose_misalignment(head: dict[str, list[Node]]) -> list[dict]:
    """Return every Compose artifact whose resolved version is not the one the BOM maps."""
    misaligned: dict[tuple[str, str, str], list[str]] = {}
    for config, roots in head.items():
        resolved = resolved_versions(roots)
        for library, mapped in constraint_members(roots, COMPOSE_BOM).items():
            if library in resolved and resolved[library] != mapped:
                key = (library, mapped, resolved[library])
                misaligned.setdefault(key, []).append(config)
    return [
        {"library": lib, "bom": bom, "resolved": resolved, "configurations": configs}
        for (lib, bom, resolved), configs in sorted(misaligned.items())
    ]


def _sorted_versions(versions: set[str]) -> list[str]:
    return sorted(versions, key=cmp_to_key(compare_versions))


def analyze(
    base: dict[str, list[Node]],
    head: dict[str, list[Node]],
    diff_text: str,
    base_sha: str,
    head_sha: str,
) -> dict:
    """Return the findings of all four checks."""
    return {
        "schema": SCHEMA_VERSION,
        "status": STATUS_CHECKED,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "checks": {
            DEP_CHANGE: {"moves": find_moves(base, head, diff_text)},
            DEP_PIN_DRIFT: {"drifts": find_pin_drift(head)},
            DEP_CLASSPATH_MISMATCH: {"mismatches": find_classpath_mismatches(head)},
            DEP_COMPOSE_ALIGNMENT: {"misaligned": find_compose_misalignment(head)},
        },
    }


def skipped_findings() -> dict:
    """Return the findings of a pull request that changed no build file."""
    return {"schema": SCHEMA_VERSION, "status": STATUS_SKIPPED}


# ---------------------------------------------------------------------------
# Validating findings
# ---------------------------------------------------------------------------

# Findings cross from a pull request's run, which ran the pull request's own
# build, into a job holding write scopes. Every value is held to a pattern with
# no Markdown or HTML metacharacter in it, so a crafted file cannot put
# anything into a comment beyond rows of coordinates and versions.
_LIBRARY = re.compile(r"^[A-Za-z0-9_.-]{1,200}:[A-Za-z0-9_.-]{1,200}$")
_VERSION = re.compile(r"^[A-Za-z0-9_.+-]{1,100}$")
_CONFIG = re.compile(r"^:[A-Za-z0-9_.:-]{1,200}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")

_MAX_ROWS = 5000

_ROW_FIELDS: dict[str, dict[str, object]] = {
    DEP_CHANGE: {
        "library": _LIBRARY,
        "base": [_VERSION],
        "head": [_VERSION],
        "configurations": [_CONFIG],
        "hidden": bool,
    },
    DEP_PIN_DRIFT: {
        "library": _LIBRARY,
        "declared": _VERSION,
        "resolved": _VERSION,
        "configurations": [_CONFIG],
    },
    DEP_CLASSPATH_MISMATCH: {
        "variant": _CONFIG,
        "library": _LIBRARY,
        "compile": _VERSION,
        "runtime": _VERSION,
    },
    DEP_COMPOSE_ALIGNMENT: {
        "library": _LIBRARY,
        "bom": _VERSION,
        "resolved": _VERSION,
        "configurations": [_CONFIG],
    },
}


def _check_value(value: object, rule: object, where: str) -> None:
    if rule is bool:
        if not isinstance(value, bool):
            raise FindingsError(f"{where} is not a boolean")
    elif isinstance(rule, list):
        if not isinstance(value, list) or len(value) > _MAX_ROWS:
            raise FindingsError(f"{where} is not a list")
        for i, item in enumerate(value):
            _check_value(item, rule[0], f"{where}[{i}]")
    elif not isinstance(value, str) or not rule.fullmatch(value):  # type: ignore[union-attr]
        raise FindingsError(f"{where} is not a value this module writes: {value!r}")


def validate_findings(findings: object) -> dict:
    """Return *findings* if it has exactly the shape `analyze` writes; else raise FindingsError."""
    if not isinstance(findings, dict) or findings.get("schema") != SCHEMA_VERSION:
        raise FindingsError(f"not a schema {SCHEMA_VERSION} findings object")
    status = findings.get("status")
    if status == STATUS_SKIPPED:
        if set(findings) != {"schema", "status"}:
            raise FindingsError("skipped findings carry unexpected keys")
        return findings
    if status != STATUS_CHECKED:
        raise FindingsError(f"unknown status {status!r}")
    if set(findings) != {"schema", "status", "base_sha", "head_sha", "checks"}:
        raise FindingsError("checked findings do not have the expected keys")
    for key in ("base_sha", "head_sha"):
        _check_value(findings[key], _SHA, key)
    checks = findings["checks"]
    if not isinstance(checks, dict) or set(checks) != set(CHECKS):
        raise FindingsError("findings do not cover exactly the four checks")
    for check in CHECKS:
        section = checks[check]
        if not isinstance(section, dict) or set(section) != {ROWS_KEY[check]}:
            raise FindingsError(f"{check} does not have the expected keys")
        rows = section[ROWS_KEY[check]]
        if not isinstance(rows, list) or len(rows) > _MAX_ROWS:
            raise FindingsError(f"{check} rows are not a list")
        fields = _ROW_FIELDS[check]
        for i, row in enumerate(rows):
            where = f"{check}[{i}]"
            if not isinstance(row, dict) or set(row) != set(fields):
                raise FindingsError(f"{where} does not have the expected fields")
            for field, rule in fields.items():
                _check_value(row[field], rule, f"{where}.{field}")
    return findings


def load_findings(path: str) -> dict:
    """Read and validate a findings file; raise FindingsError if it is not one."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise FindingsError(f"cannot read {path}: {exc}") from None
    return validate_findings(data)


# ---------------------------------------------------------------------------
# Labels and comments
# ---------------------------------------------------------------------------


def labels_for(check: str, findings: dict) -> list[str]:
    """Return the labels *check*'s findings call for, in CHECK_LABELS order."""
    if findings["status"] != STATUS_CHECKED:
        return []
    rows = findings["checks"][check][ROWS_KEY[check]]
    if not rows:
        return []
    if check == DEP_CHANGE:
        wanted = {LABEL_CHANGE} | ({LABEL_HIDDEN} if any(r["hidden"] for r in rows) else set())
        return [label for label in CHECK_LABELS[check] if label in wanted]
    return list(CHECK_LABELS[check])


def comment_marker(check: str) -> str:
    """Return the first line that identifies *check*'s comment."""
    return f"<!-- dependency-version-check: {check} -->"


# The hidden line in a check's comment that records the labels the check
# owns: the ones it added and still holds. A label already on the pull request
# when the check first called for it was added by someone else, such as a
# person applying it by hand, and is never recorded, so the check never
# removes it. post_dependency_checks.owned_labels decides the set.
_LABELS_LINE_PREFIX = "<!-- labels-added-by-this-check: "
_LABELS_LINE_RE = re.compile(r"^<!-- labels-added-by-this-check: (\[.*\]) -->$", re.M)


def recorded_labels(body: str) -> list[str]:
    """Return the labels a previous comment records this check as owning, or []."""
    match = _LABELS_LINE_RE.search(body)
    if not match:
        return []
    try:
        labels = json.loads(match.group(1))
    except ValueError:
        return []
    if not isinstance(labels, list):
        return []
    return [label for label in labels if isinstance(label, str)]


_TITLES = {
    DEP_CHANGE: "Resolved versions this pull request moves",
    DEP_PIN_DRIFT: "Declared versions that resolve higher",
    DEP_CLASSPATH_MISMATCH: "Libraries the compile and runtime classpaths disagree on",
    DEP_COMPOSE_ALIGNMENT: "Compose artifacts the Compose BOM does not map to their resolved version",
}

_NONE_FOUND = {
    DEP_CHANGE: "No library resolves to a different version at the head than at the base.",
    DEP_PIN_DRIFT: "Every version declared explicitly is the version that resolves.",
    DEP_CLASSPATH_MISMATCH: "Each variant's compile and runtime classpaths agree.",
    DEP_COMPOSE_ALIGNMENT: "Every Compose artifact resolves to the version the Compose BOM maps.",
}

_EXPLANATION = {
    DEP_CHANGE: (
        "Every library whose resolved version differs between the base and the head, "
        "including one that appears or disappears.\n"
        "A move is **hidden** when its coordinate is on no changed line of a build file "
        "and it is not a member of a BOM or platform whose own line changed."
    ),
    DEP_PIN_DRIFT: (
        "Every coordinate declared with an explicit version that resolves to a higher one, "
        "at the head."
    ),
    DEP_CLASSPATH_MISMATCH: (
        "Every library that resolves to different versions on a variant's compile and "
        "runtime classpaths, at the head.\n"
        "The runtime classpath is what ships."
    ),
    DEP_COMPOSE_ALIGNMENT: (
        "Every Compose artifact whose resolved version differs from the version the "
        "Compose BOM maps for it, at the head."
    ),
}

# GitHub refuses a comment longer than 65536 characters.
MAX_COMMENT_LENGTH = 60000


def _versions(versions: list[str]) -> str:
    return ", ".join(f"`{v}`" for v in versions) if versions else "absent"


def _configs(configs: list[str]) -> str:
    return "<br>".join(f"`{c}`" for c in configs)


def _table(check: str, rows: list[dict]) -> tuple[str, list[str]]:
    if check == DEP_CHANGE:
        header = (
            "| Library | Base | Head | Hidden | Configurations |\n| --- | --- | --- | --- | --- |"
        )
        lines = [
            f"| `{r['library']}` | {_versions(r['base'])} | {_versions(r['head'])} | "
            f"{'**yes**' if r['hidden'] else 'no'} | {_configs(r['configurations'])} |"
            for r in rows
        ]
    elif check == DEP_PIN_DRIFT:
        header = "| Library | Declared | Resolved | Configurations |\n| --- | --- | --- | --- |"
        lines = [
            f"| `{r['library']}` | `{r['declared']}` | `{r['resolved']}` | "
            f"{_configs(r['configurations'])} |"
            for r in rows
        ]
    elif check == DEP_CLASSPATH_MISMATCH:
        header = "| Variant | Library | Compile | Runtime |\n| --- | --- | --- | --- |"
        lines = [
            f"| `{r['variant']}` | `{r['library']}` | `{r['compile']}` | `{r['runtime']}` |"
            for r in rows
        ]
    else:
        header = "| Library | BOM maps | Resolved | Configurations |\n| --- | --- | --- | --- |"
        lines = [
            f"| `{r['library']}` | `{r['bom']}` | `{r['resolved']}` | "
            f"{_configs(r['configurations'])} |"
            for r in rows
        ]
    return header, lines


def render_comment(
    check: str,
    findings: dict,
    owned: list[str] | None = None,
    max_length: int | None = MAX_COMMENT_LENGTH,
) -> str:
    """Return *check*'s report for *findings*.

    *owned* is the record of the labels this check holds once the comment is
    written; a comment carries it on a hidden line, and a job summary, which
    passes None, carries none.
    Rows past *max_length* characters are cut, with a note saying how many;
    pass None for a job summary, which has no such cap.
    """
    labels = labels_for(check, findings)
    parts = [comment_marker(check)]
    if owned is not None:
        parts.append(f"{_LABELS_LINE_PREFIX}{json.dumps(owned)} -->")
    parts += [f"### `{check}`: {_TITLES[check]}", ""]
    if findings["status"] != STATUS_CHECKED:
        parts.append(
            "This pull request no longer changes a build file, so there is nothing to compare."
        )
        return "\n".join(parts) + "\n"

    parts.append(
        f"Base `{findings['base_sha'][:12]}`, head `{findings['head_sha'][:12]}` "
        "(the merge of this pull request into its base)."
    )
    parts.append("")
    rows = findings["checks"][check][ROWS_KEY[check]]
    if not rows:
        parts.append(_NONE_FOUND[check])
        return "\n".join(parts) + "\n"

    parts.append(_EXPLANATION[check])
    parts.append("")
    parts.append("Labels these findings call for: " + ", ".join(f"`{x}`" for x in labels) + ".")
    parts.append("")
    header, lines = _table(check, rows)
    body = "\n".join(parts) + "\n" + header + "\n"
    shown = 0
    for line in lines:
        if max_length is not None and len(body) + len(line) + 200 > max_length:
            break
        body += line + "\n"
        shown += 1
    if shown < len(lines):
        body += (
            f"\n{len(lines) - shown} more rows do not fit in a comment; "
            f"the `{check}` job's summary lists every row.\n"
        )
    return body


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _write_json(data: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    command, args = argv[0], argv[1:]
    try:
        if command == "configurations" and not args:
            for project, configuration in CONFIGURATIONS:
                print(project, configuration, report_file_name(project, configuration))
            return 0
        if command == "analyze" and len(args) == 6:
            base_dir, head_dir, diff_path, base_sha, head_sha, out = args
            with open(diff_path, encoding="utf-8") as f:
                diff_text = f.read()
            findings = analyze(
                load_reports(base_dir), load_reports(head_dir), diff_text, base_sha, head_sha
            )
            _write_json(validate_findings(findings), out)
            return 0
        if command == "skipped" and len(args) == 1:
            _write_json(skipped_findings(), args[0])
            return 0
        if command == "summaries" and len(args) == 2:
            findings = load_findings(args[0])
            for check in CHECKS:
                with open(f"{args[1]}/{check}.md", "w", encoding="utf-8") as f:
                    f.write(render_comment(check, findings, owned=None, max_length=None))
            return 0
    except (ReportError, FindingsError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
