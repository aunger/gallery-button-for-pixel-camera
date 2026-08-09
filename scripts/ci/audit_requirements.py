#!/usr/bin/env python3
"""Audit this repo's hash-pinned requirements locks for known vulnerabilities.

Every Python dependency in this repo is installed from a fully resolved lock
under ``scripts/`` with ``pip install --require-hashes`` (issue #723).  That is
good supply-chain hygiene, but pinning is exactly what makes a lock go quietly
stale: a dependency ships a fix and the pin keeps the old version indefinitely.
Nothing was watching for that until this script (issue #804).  The cost was
concrete: ``cryptography`` 49.0.0 carried a HIGH advisory for a week while the
repo's attention went to a *different*, unfixable alert in the same lock
(issue #788, PR #801).

This wraps ``pip-audit`` in the two behaviors a bare invocation does not have.

Per-lock ignore entries with a recorded rationale
    Some findings are real, unfixable by regeneration, and unreachable in this
    repo's usage, so the gate would be red on arrival.  ``pip-audit`` has its
    own ``--ignore-vuln ID``, which is not usable here: it suppresses the
    finding before it is reported, and it is global rather than per-lock.  A
    finding that is unreachable in the semgrep lock is not automatically
    unreachable in the script-runtime lock.

A stale ignore entry fails the build
    This is the property that actually matters.  When an ignored ID stops being
    reported, the entry has become stale, usually because an upstream cap
    lifted or the package was dropped, and that is the moment to revisit it.
    Without this, an ignore list only ever grows and rots into noise.  The
    ``click`` case in issue #788 was exactly this shape: semgrep quietly
    relaxed its ``click~=8.1.8`` cap, and nothing was watching, so a fixable
    HIGH was recorded as unfixable.  Suppressing findings with
    ``--ignore-vuln`` would make this check impossible, which is why the
    filtering happens here instead.

Why the locks are not handed to ``pip-audit`` directly
    The locks are generated with ``uv pip compile --universal``, so they are a
    superset covering every interpreter and platform the install sites use, and
    some entries are marker-gated (``pywin32 ; sys_platform == 'win32'``,
    ``rpds-py==0.30.0 ; python_full_version < '3.11'``, and similar).
    ``pip-audit -r <lock>`` evaluates those markers against the interpreter it
    happens to run under and silently skips the rest, so a CPython-on-Linux
    runner audits neither of those two.  Instead this script re-derives the pin
    list from the lock text and audits every pin regardless of marker.  A pin
    the parser does not recognize is an error, not a skip, so a change in the
    lock format shows up as a red build rather than as silent under-coverage.
    (Names repeated at different versions, as ``rpds-py`` is, cannot share one
    requirements file, because ``pip-audit`` rejects the duplicate, so the pins
    are audited in as many rounds as the most-repeated name requires.)

Locks are discovered rather than listed, so a lock added later is audited
without anyone having to remember to register it here.

Ignore file format (TOML; see scripts/ci/requirements-audit-ignore.toml)::

    [[ignore."scripts/ci/requirements-semgrep.txt"]]
    id = "PYSEC-0000-1"
    package = "somepkg"
    reason = "why this finding is tolerated"
    remove_when = "what would make this entry stop being needed"

Usage::

    python3 scripts/ci/audit_requirements.py [--ignore-file PATH] [LOCK ...]

With no LOCK arguments every ``scripts/**/requirements*.txt`` is audited, which
is how CI runs it.  Naming locks narrows the run, for debugging one lock: ignore
entries for the locks left out are reported as unchecked rather than treated as
misfiled, so a narrowed run is visibly not the full gate.

Exit codes:
    0  every lock is clean, or every finding is ignored and every ignore entry
       is still doing work.
    1  a finding is not ignored, or an ignore entry is stale/mismatched.
    2  the ignore file is invalid, a lock could not be parsed, or ``pip-audit``
       could not be run.
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11; pip-audit depends on tomli, so it is present.
    import tomli as tomllib

# scripts/ci/audit_requirements.py -> repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]

LOCK_GLOB = "scripts/**/requirements*.txt"
DEFAULT_IGNORE_FILE = "scripts/ci/requirements-audit-ignore.toml"

# A pinned requirement in a uv-generated lock: always at column 0, with its
# hashes and `# via` comment indented beneath it. The trailing marker
# (`; python_full_version < '3.11'`) and line continuation are deliberately not
# captured; every pin is audited regardless of which environment it applies to.
_PIN_RE = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s;\\]+)")

_IGNORE_FIELDS = ("id", "package", "reason", "remove_when")


class AuditError(Exception):
    """A problem that prevents the audit from producing a verdict (exit 2)."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class Pin:
    """One ``name==version`` entry from a lock, with *name* PEP 503-normalized."""

    name: str
    version: str


@dataclass(frozen=True)
class Finding:
    """One vulnerability pip-audit reported against one pin in one lock."""

    lock: str
    package: str
    version: str
    vuln_id: str
    fix_versions: tuple[str, ...]
    aliases: tuple[str, ...]

    def describe(self) -> str:
        alias_text = f" ({', '.join(self.aliases)})" if self.aliases else ""
        fix_text = ", ".join(self.fix_versions) if self.fix_versions else "none"
        return (
            f"{self.package} {self.version}  {self.vuln_id}{alias_text}  fix versions: {fix_text}"
        )


@dataclass(frozen=True)
class IgnoreEntry:
    """One tolerated finding, scoped to the lock it was justified against."""

    lock: str
    vuln_id: str
    package: str
    reason: str
    remove_when: str


@dataclass
class Report:
    """The verdict: what was audited, what was tolerated, and what failed."""

    audited: list[tuple[str, int, int]]  # (lock, pin count, finding count)
    honored: list[tuple[IgnoreEntry, Finding]]
    unignored: list[Finding]
    stale: list[IgnoreEntry]
    mismatched: list[tuple[IgnoreEntry, Finding]]
    # Entries for locks a narrowed run did not audit. Never a failure, but
    # reported so a subset run is not mistaken for the full gate.
    out_of_scope: list[IgnoreEntry]

    @property
    def failed(self) -> bool:
        return bool(self.unignored or self.stale or self.mismatched)


# ---------------------------------------------------------------------------
# Lock discovery and parsing
# ---------------------------------------------------------------------------


def normalize(name: str) -> str:
    """Return *name* in PEP 503 normalized form, as pip-audit reports it."""
    return re.sub(r"[-_.]+", "-", name).lower()


def discover_locks(root: Path) -> list[Path]:
    """Return every requirements lock under *root*, sorted by repo-relative path."""
    return sorted(root.glob(LOCK_GLOB))


def relative_to_root(path: Path, root: Path) -> str:
    """Return *path* as a root-relative, forward-slash string.

    The ignore file keys on this form, and it is checked into git, so it has to
    mean the same thing on every platform.  ``str()`` of a relative path uses the
    OS separator, which would make the checked-in keys unmatchable on Windows.
    """
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise AuditError(
            f"{path} is outside the repo root {root}; pass --root to widen it"
        ) from exc


def parse_pins(text: str, source: str) -> list[Pin]:
    """Return the unique pins declared in lock *text*.

    Raises AuditError on any column-0, non-comment line that is not a pin: an
    unrecognized line means the lock format changed, and skipping it would
    silently shrink the audit's coverage.
    """
    pins: list[Pin] = []
    seen: set[Pin] = set()
    for lineno, line in enumerate(text.splitlines(), start=1):
        # Hashes, `# via` comments and blank lines are all indented or empty.
        if not line or line[0].isspace() or line.startswith("#"):
            continue
        match = _PIN_RE.match(line)
        if match is None:
            raise AuditError(
                f"{source}:{lineno}: not a `name==version` pin: {line!r}. "
                "The lock format changed; update _PIN_RE in "
                "scripts/ci/audit_requirements.py rather than letting the entry go unaudited."
            )
        pin = Pin(normalize(match.group("name")), match.group("version"))
        if pin not in seen:
            seen.add(pin)
            pins.append(pin)
    if not pins:
        raise AuditError(f"{source}: no pins found; refusing to report an empty lock as clean.")
    return pins


def plan_rounds(pins: list[Pin]) -> list[list[Pin]]:
    """Split *pins* into groups in which no package name repeats.

    A universal lock can pin one name at two versions under complementary
    markers (``rpds-py`` is pinned at 0.30.0 for Python < 3.11 and at 2026.6.3
    otherwise).  pip-audit rejects a requirements file with a duplicate name,
    so those versions have to be audited in separate passes.  Almost always
    this returns a single round.
    """
    rounds: list[dict[str, Pin]] = []
    for pin in pins:
        for group in rounds:
            if pin.name not in group:
                group[pin.name] = pin
                break
        else:
            rounds.append({pin.name: pin})
    return [sorted(group.values()) for group in rounds]


def pins_to_requirements(pins: list[Pin]) -> str:
    """Render *pins* as a plain requirements file body for pip-audit."""
    return "".join(f"{pin.name}=={pin.version}\n" for pin in pins)


# ---------------------------------------------------------------------------
# pip-audit invocation
# ---------------------------------------------------------------------------


def pip_audit_argv(req_path: Path) -> list[str]:
    """Return the pip-audit command line for the requirements file at *req_path*.

    ``--no-deps`` is required because the pins are already a full transitive
    closure; without it pip-audit tries to resolve and install.  ``--strict``
    turns a dependency pip-audit could not collect into a failure instead of a
    silent omission.  pip-audit is invoked through ``sys.executable -m`` so the
    interpreter running this script is the one that has it installed.
    """
    return [
        sys.executable,
        "-m",
        "pip_audit",
        "--no-deps",
        "--disable-pip",
        "--strict",
        "--progress-spinner",
        "off",
        "--desc",
        "off",
        "--format",
        "json",
        "--requirement",
        str(req_path),
    ]


def parse_pip_audit_result(returncode: int, stdout: str, stderr: str) -> dict:
    """Return pip-audit's JSON report, or raise AuditError quoting what it said.

    pip-audit exits 1 both when it found vulnerabilities (with a JSON report on
    stdout) and when ``--strict`` tripped over a dependency it could not audit
    (with nothing on stdout and the diagnosis on stderr).  Only the exit code
    does not distinguish those, so both codes are accepted and the verdict comes
    from whether a report actually arrived.  Whatever pip-audit printed is
    carried into the error either way: a gate whose whole argument is that it
    should tell you what to do when it goes red cannot swallow the one line
    naming the package that broke it.
    """
    detail = stderr.strip() or stdout.strip() or "(no output)"
    if returncode not in (0, 1):
        raise AuditError(f"pip-audit exited {returncode}:\n{detail}")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise AuditError(
            f"pip-audit exited {returncode} without a JSON report ({exc}). "
            f"Its own output was:\n{detail}"
        ) from exc


def run_pip_audit(requirements: str) -> dict:
    """Run pip-audit over *requirements* and return its parsed JSON report."""
    with tempfile.TemporaryDirectory() as tmpdir:
        req_path = Path(tmpdir) / "requirements.txt"
        req_path.write_text(requirements, encoding="utf-8")
        proc = subprocess.run(pip_audit_argv(req_path), capture_output=True, text=True, check=False)
    return parse_pip_audit_result(proc.returncode, proc.stdout, proc.stderr)


def findings_from_report(payload: dict, lock: str) -> list[Finding]:
    """Convert one pip-audit JSON report into Findings attributed to *lock*."""
    findings: list[Finding] = []
    for dep in payload.get("dependencies", []):
        for vuln in dep.get("vulns", []):
            findings.append(
                Finding(
                    lock=lock,
                    package=normalize(dep["name"]),
                    version=dep.get("version", ""),
                    vuln_id=vuln["id"],
                    fix_versions=tuple(vuln.get("fix_versions", ())),
                    aliases=tuple(vuln.get("aliases", ())),
                )
            )
    return findings


def audit_lock(lock: Path, rel: str, runner=run_pip_audit) -> tuple[int, list[Finding]]:
    """Audit one lock; return its pin count and every finding against it."""
    pins = parse_pins(lock.read_text(encoding="utf-8"), rel)
    findings: list[Finding] = []
    for group in plan_rounds(pins):
        findings.extend(findings_from_report(runner(pins_to_requirements(group)), rel))
    return len(pins), findings


# ---------------------------------------------------------------------------
# Ignore file
# ---------------------------------------------------------------------------


def load_ignores(path: Path, known_locks: set[str]) -> list[IgnoreEntry]:
    """Parse the TOML ignore file at *path*.

    A missing file means nothing is ignored.  Every validation problem is an
    AuditError: a malformed or misfiled entry must not silently degrade into
    "ignores nothing" (which hides the entry's rationale) or into "ignores
    everything".

    *known_locks* is every lock in the tree, not only the ones the current run
    audits, so that narrowing a run to one lock does not make the entries for
    the others look misfiled.  Scoping the loaded entries to the run is the
    caller's job.
    """
    if not path.exists():
        return []

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise AuditError(f"{path}: invalid TOML: {exc}") from exc

    unknown_tables = sorted(set(data) - {"ignore"})
    if unknown_tables:
        raise AuditError(f"{path}: unknown top-level table(s): {', '.join(unknown_tables)}")

    raw_ignore = data.get("ignore", {})
    if not isinstance(raw_ignore, dict):
        raise AuditError(
            f"{path}: [ignore] must be a table mapping each lock path to an array of "
            f"entries; got {type(raw_ignore).__name__}."
        )

    entries: list[IgnoreEntry] = []
    seen: set[tuple[str, str]] = set()
    for lock, raw_entries in sorted(raw_ignore.items()):
        if lock not in known_locks:
            raise AuditError(
                f"{path}: ignore entries are filed under {lock!r}, which is not a requirements "
                f"lock in this tree ({', '.join(sorted(known_locks))}). "
                "Ignoring is per-lock: a finding unreachable in one lock is not "
                "automatically unreachable in another, so the path has to name a real lock."
            )
        if not isinstance(raw_entries, list):
            raise AuditError(f'{path}: [ignore."{lock}"] must be an array of tables.')
        for index, raw in enumerate(raw_entries, start=1):
            if not isinstance(raw, dict):
                raise AuditError(
                    f"{path}: entry {index} under {lock!r} must be a table with the fields "
                    f"{', '.join(_IGNORE_FIELDS)}; got {type(raw).__name__}."
                )
            bad = [
                f for f in _IGNORE_FIELDS if not isinstance(raw.get(f), str) or not raw[f].strip()
            ]
            if bad:
                raise AuditError(
                    f"{path}: entry {index} under {lock!r} (id {raw.get('id', '<none>')!r}) "
                    f"has missing, blank or non-string field(s): {', '.join(bad)}. Every "
                    "entry must record why the finding is tolerated and what would make the "
                    "entry unnecessary."
                )
            extra = sorted(set(raw) - set(_IGNORE_FIELDS))
            if extra:
                raise AuditError(
                    f"{path}: entry {raw['id']!r} under {lock!r} has unknown field(s): "
                    f"{', '.join(extra)}"
                )
            # Unique on (lock, package, id), which is what evaluate() matches
            # on. One advisory can hit two packages pinned in the same lock, and
            # the reachability argument is per package, so each needs its own
            # entry; keying uniqueness on (lock, id) alone would make the second
            # one unwritable and leave that finding permanently red with no
            # remedy inside the file.
            package = normalize(raw["package"])
            key = (lock, package, raw["id"])
            if key in seen:
                raise AuditError(
                    f"{path}: duplicate entry for {raw['id']!r} against {package!r} under {lock!r}"
                )
            seen.add(key)
            entries.append(
                IgnoreEntry(
                    lock=lock,
                    vuln_id=raw["id"],
                    package=package,
                    reason=raw["reason"].strip(),
                    remove_when=raw["remove_when"].strip(),
                )
            )
    return entries


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


def evaluate(
    audited: list[tuple[str, int, int]],
    findings: list[Finding],
    ignores: list[IgnoreEntry],
    out_of_scope: list[IgnoreEntry] | None = None,
) -> Report:
    """Match findings against ignore entries and classify every mismatch.

    An entry covers its advisory for its package in its lock at *every* version
    that lock pins the package at.  A universal lock can carry two versions of
    one name under complementary markers (``rpds-py``), and the reachability
    argument an entry records is about how this repo uses the package, not about
    a version, so splitting one advisory into a per-version entry would be noise.
    Both findings are still listed individually in the report; what is shared is
    the justification, not the visibility.
    """
    by_package: dict[tuple[str, str, str], list[Finding]] = {}
    by_id: dict[tuple[str, str], list[Finding]] = {}
    for f in findings:
        by_package.setdefault((f.lock, f.package, f.vuln_id), []).append(f)
        by_id.setdefault((f.lock, f.vuln_id), []).append(f)

    honored: list[tuple[IgnoreEntry, Finding]] = []
    stale: list[IgnoreEntry] = []
    mismatched: list[tuple[IgnoreEntry, Finding]] = []
    accounted: set[Finding] = set()
    for entry in ignores:
        matches = by_package.get((entry.lock, entry.package, entry.vuln_id), [])
        if matches:
            honored.extend((entry, f) for f in matches)
            accounted.update(matches)
            continue
        others = by_id.get((entry.lock, entry.vuln_id), [])
        if others:
            # Reported, but against a package this entry does not name, so the
            # entry is wrong rather than merely tolerant. These are accounted
            # for so they are not *also* listed as having no entry at all,
            # which would tell the reader to add one that already exists.
            mismatched.extend((entry, f) for f in others)
            accounted.update(others)
            continue
        stale.append(entry)

    unignored = [f for f in findings if f not in accounted]
    return Report(
        audited=audited,
        honored=honored,
        unignored=unignored,
        stale=stale,
        mismatched=mismatched,
        out_of_scope=list(out_of_scope or ()),
    )


def format_report(report: Report, ignore_file: str) -> str:
    """Render *report* as the text the CI log shows."""
    lines: list[str] = []
    width = max((len(lock) for lock, _, _ in report.audited), default=0)
    for lock, pin_count, finding_count in report.audited:
        verdict = f"{finding_count} finding(s)" if finding_count else "no findings"
        lines.append(f"  {lock:<{width}}  {pin_count:>3} pins  {verdict}")

    if report.out_of_scope:
        locks = sorted({entry.lock for entry in report.out_of_scope})
        lines.append("")
        lines.append(
            f"Not the full gate: {len(report.out_of_scope)} ignore entry(s) left unchecked "
            f"because this run does not cover their lock ({', '.join(locks)})."
        )

    if report.honored:
        lines.append("")
        lines.append(f"Ignored, per {ignore_file}:")
        for entry, finding in report.honored:
            lines.append(f"  [{entry.lock}] {finding.describe()}")
            lines.append(f"      reason: {entry.reason}")
            lines.append(f"      remove when: {entry.remove_when}")

    if report.unignored:
        lines.append("")
        lines.append("Known vulnerabilities with no ignore entry:")
        for finding in report.unignored:
            lines.append(f"  [{finding.lock}] {finding.describe()}")
        lines.append(
            "Regenerate the lock to take the fix (see the --upgrade note in the matching "
            f"`.in` file), or add a justified entry to {ignore_file}."
        )

    if report.stale:
        lines.append("")
        lines.append("Stale ignore entries (no longer reported, so no longer needed):")
        for entry in report.stale:
            lines.append(f"  [{entry.lock}] {entry.vuln_id} ({entry.package})")
            lines.append(f"      recorded as removable when: {entry.remove_when}")
        lines.append(
            f"Delete these from {ignore_file}. An entry that stops matching usually means an "
            "upstream cap lifted or the package was dropped, which is the moment to re-check "
            "the reasoning the entry recorded."
        )

    if report.mismatched:
        lines.append("")
        lines.append("Ignore entries naming the wrong package:")
        for entry, finding in report.mismatched:
            lines.append(
                f"  [{entry.lock}] {entry.vuln_id} is recorded against {entry.package!r} "
                f"but is reported against {finding.package!r}"
            )
        lines.append(
            f"Correct the `package` field in {ignore_file}, or file the entry under the "
            "advisory ID that actually covers the package it names. These are not listed "
            "above as unignored: they already have an entry, it is just wrong."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit this repo's hash-pinned requirements locks for known vulnerabilities."
    )
    parser.add_argument(
        "locks",
        nargs="*",
        type=Path,
        help=(
            f"locks to audit (default, and how CI runs it: every {LOCK_GLOB} under the "
            "repo root). Naming locks narrows the run; ignore entries for the other "
            "locks are reported as unchecked."
        ),
    )
    parser.add_argument(
        "--ignore-file",
        type=Path,
        default=REPO_ROOT / DEFAULT_IGNORE_FILE,
        help=f"TOML ignore list (default: {DEFAULT_IGNORE_FILE})",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="repo root that lock paths are reported relative to (default: this checkout)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, runner=run_pip_audit) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    locks = [p.resolve() for p in args.locks] or discover_locks(root)

    try:
        if not locks:
            raise AuditError(f"no locks matched {LOCK_GLOB} under {root}")

        rels = [relative_to_root(lock, root) for lock in locks]
        in_scope = set(rels)
        # Validate ignore entries against every lock in the tree, not just the
        # ones this run audits. An entry naming a path that is a lock nowhere is
        # misfiled and must be loud; an entry naming a real lock that a narrowed
        # run happens not to cover is simply out of scope, and treating that as
        # misfiled made the documented `LOCK ...` form unusable.
        known = in_scope | {relative_to_root(p, root) for p in discover_locks(root)}
        loaded = load_ignores(args.ignore_file, known)
        ignores = [entry for entry in loaded if entry.lock in in_scope]
        out_of_scope = [entry for entry in loaded if entry.lock not in in_scope]

        audited: list[tuple[str, int, int]] = []
        findings: list[Finding] = []
        for lock, rel in zip(locks, rels):
            pin_count, lock_findings = audit_lock(lock, rel, runner=runner)
            audited.append((rel, pin_count, len(lock_findings)))
            findings.extend(lock_findings)
    except AuditError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    report = evaluate(audited, findings, ignores, out_of_scope)
    try:
        ignore_file = relative_to_root(args.ignore_file.resolve(), root)
    except AuditError:
        ignore_file = str(args.ignore_file)
    print(f"Audited {len(locks)} requirements lock(s) with pip-audit:")
    print(format_report(report, ignore_file))

    if report.failed:
        print("\nRequirements audit failed.", file=sys.stderr)
        return 1
    print("\nRequirements audit passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
