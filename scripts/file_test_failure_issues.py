#!/usr/bin/env python3
"""File a GitHub issue for each failed test found in JUnit XML results.

Reads TEST-*.xml files from one or more result directories and creates (or
comments on) a GitHub issue for every failing test case.

Usage:
    python3 scripts/file_test_failure_issues.py \\
        path/to/unit-results       --suite-label "Unit Tests" \\
        path/to/e2e-results        --suite-label "Instrumented Tests"

Each directory path must be immediately followed by --suite-label <name>.
Exit code is always 0 — API failures are logged but do not fail the CI run.

Required environment variables:
    GITHUB_TOKEN        Personal access token or Actions secret with issues: write
    GITHUB_REPOSITORY   Owner/repo (e.g. "aunger/gallery-button-for-pixel-camera")

Optional environment variables (populated automatically by GitHub Actions):
    GITHUB_SERVER_URL   Default: https://github.com
    GITHUB_RUN_ID       Workflow run ID (for CI run link)
    WORKFLOW_RUN_SHA    Commit SHA of the triggering workflow run
"""

import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class FailedTest:
    class_name: str
    method_name: str
    failure_message: str
    stack_trace: str
    suite_label: str
    artifact_name: str


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_failures(directory: Path, suite_label: str, artifact_name: str) -> list[FailedTest]:
    """Return one FailedTest for every failing test case found in *directory*."""
    failures: list[FailedTest] = []

    xml_files = sorted(directory.glob("TEST-*.xml"))
    for xml_path in xml_files:
        try:
            tree = ET.parse(xml_path)
        except ET.ParseError as exc:
            print(f"  Warning: could not parse {xml_path}: {exc}", file=sys.stderr)
            continue

        root = tree.getroot()
        if root.tag == "testsuites":
            suite_elements = root.findall("testsuite")
        else:
            suite_elements = [root]

        for suite in suite_elements:
            class_name = suite.get("name", xml_path.stem)

            for tc in suite.findall("testcase"):
                method_name = tc.get("name", "<unknown>")

                failure_el = tc.find("failure")
                error_el = tc.find("error")
                skipped_el = tc.find("skipped")

                if skipped_el is not None:
                    continue  # not a failure

                problem_el = failure_el if failure_el is not None else error_el
                if problem_el is None:
                    continue  # passed

                failure_message = problem_el.get("message", "")
                stack_trace = (problem_el.text or "").strip()

                failures.append(FailedTest(
                    class_name=class_name,
                    method_name=method_name,
                    failure_message=failure_message,
                    stack_trace=stack_trace,
                    suite_label=suite_label,
                    artifact_name=artifact_name,
                ))

    return failures


# ---------------------------------------------------------------------------
# Title / body construction
# ---------------------------------------------------------------------------

_STACK_TRACE_LIMIT = 2000


def make_issue_title(class_name: str, method_name: str, timestamp: datetime, sha: str) -> str:
    """Return the issue title string.

    Format: [Test Failure] <ClassName>.<methodName> @ <yyMMdd-hhmm>-<gitsha7>
    """
    ts = timestamp.strftime("%y%m%d-%H%M")
    short_sha = sha[:7] if sha else "unknown"
    return f"[Test Failure] {class_name}.{method_name} @ {ts}-{short_sha}"


def _find_ocr_text(directory: Path, class_name: str, method_name: str) -> str | None:
    """Return OCR text from a companion .ocr.txt file matching the test, or None."""
    # Match by class name (last component) + method name prefix in filename.
    short_class = class_name.split(".")[-1]
    prefix = f"{short_class}_{method_name}"
    for ocr_path in directory.glob("*.ocr.txt"):
        if ocr_path.stem.startswith(prefix) or prefix in ocr_path.stem:
            try:
                return ocr_path.read_text(encoding="utf-8").strip()
            except OSError:
                pass
    return None


def make_issue_body(
    failure: FailedTest,
    directory: Path,
    timestamp: datetime,
    sha: str,
    github_server_url: str,
    github_repository: str,
    github_run_id: str,
    workflow_run_branch: str,
) -> str:
    """Build the Markdown body for a test-failure issue."""
    ts_str = timestamp.strftime("%Y-%m-%d %H:%M UTC")
    short_sha = sha[:7] if sha else "unknown"

    run_url = (
        f"{github_server_url}/{github_repository}/actions/runs/{github_run_id}"
        if github_run_id
        else "_CI run ID not available_"
    )

    artifact_url = (
        f"{run_url}#artifacts"
        if github_run_id
        else "_not available_"
    )

    stack_excerpt = failure.stack_trace
    if len(stack_excerpt) > _STACK_TRACE_LIMIT:
        stack_excerpt = stack_excerpt[:_STACK_TRACE_LIMIT] + "\n... (truncated)"

    ocr_text = _find_ocr_text(directory, failure.class_name, failure.method_name)
    ocr_section = ""
    if ocr_text:
        ocr_section = f"\n### OCR text from screenshot\n\n```\n{ocr_text}\n```\n"

    branch_info = f"`{workflow_run_branch}`" if workflow_run_branch else "_unknown_"

    body = f"""\
## Test Failure

| Field | Value |
|-------|-------|
| Suite | {failure.suite_label} |
| Class | `{failure.class_name}` |
| Method | `{failure.method_name}` |
| Branch | {branch_info} |
| Commit | `{short_sha}` |
| Detected at | {ts_str} |

### Failure message

```
{failure.failure_message}
```

### Stack trace

```
{stack_excerpt}
```

### Links

- [CI run]({run_url})
- [Test artifact: {failure.artifact_name}]({artifact_url})
{ocr_section}
---
_Filed automatically by CI on failure of `{failure.class_name}.{failure.method_name}`._
"""
    return body


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


LABELS = ["test-failure", "ci", "for ai to do"]


def find_existing_issue(
    token: str,
    repository: str,
    class_name: str,
    method_name: str,
) -> int | None:
    """Search open issues for an existing failure report.

    Returns the issue number if found, else None.
    """
    if requests is None:
        print("  Error: 'requests' library not available.", file=sys.stderr)
        return None

    query = f'repo:{repository} is:issue is:open label:test-failure "{class_name}.{method_name}" in:title'
    url = "https://api.github.com/search/issues"
    try:
        resp = requests.get(
            url,
            headers=_github_headers(token),
            params={"q": query, "per_page": 1},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        if items:
            return items[0]["number"]
    except Exception as exc:  # noqa: BLE001
        print(f"  Warning: issue search failed: {exc}", file=sys.stderr)
    return None


def create_issue(
    token: str,
    repository: str,
    title: str,
    body: str,
) -> int | None:
    """Create a new GitHub issue.  Returns the issue number or None on failure."""
    if requests is None:
        print("  Error: 'requests' library not available.", file=sys.stderr)
        return None

    url = f"https://api.github.com/repos/{repository}/issues"
    payload = {"title": title, "body": body, "labels": LABELS}
    try:
        resp = requests.post(url, headers=_github_headers(token), json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()["number"]
    except Exception as exc:  # noqa: BLE001
        print(f"  Warning: failed to create issue '{title}': {exc}", file=sys.stderr)
    return None


def add_issue_comment(
    token: str,
    repository: str,
    issue_number: int,
    body: str,
) -> bool:
    """Append a comment to an existing issue.  Returns True on success."""
    if requests is None:
        print("  Error: 'requests' library not available.", file=sys.stderr)
        return False

    url = f"https://api.github.com/repos/{repository}/issues/{issue_number}/comments"
    try:
        resp = requests.post(url, headers=_github_headers(token), json={"body": body}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  Warning: failed to comment on issue #{issue_number}: {exc}", file=sys.stderr)
    return False


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def process_failure(
    failure: FailedTest,
    directory: Path,
    token: str,
    repository: str,
    timestamp: datetime,
    sha: str,
    github_server_url: str,
    github_run_id: str,
    workflow_run_branch: str,
) -> None:
    """File or update a GitHub issue for a single test failure."""
    title = make_issue_title(failure.class_name, failure.method_name, timestamp, sha)
    body = make_issue_body(
        failure=failure,
        directory=directory,
        timestamp=timestamp,
        sha=sha,
        github_server_url=github_server_url,
        github_repository=repository,
        github_run_id=github_run_id,
        workflow_run_branch=workflow_run_branch,
    )

    existing = find_existing_issue(token, repository, failure.class_name, failure.method_name)
    if existing is not None:
        print(
            f"  Duplicate found: #{existing} — appending comment for "
            f"{failure.class_name}.{failure.method_name}",
            file=sys.stderr,
        )
        comment_body = f"### Recurrence detected\n\n{body}"
        add_issue_comment(token, repository, existing, comment_body)
    else:
        issue_num = create_issue(token, repository, title, body)
        if issue_num is not None:
            print(f"  Created issue #{issue_num}: {title}", file=sys.stderr)
        else:
            print(f"  Failed to create issue for: {title}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_USAGE = (
    "usage: file_test_failure_issues.py "
    "<dir> --suite-label <name> [<dir> --suite-label <name> ...]"
)


def parse_args(argv: list[str]) -> list[tuple[Path, str]]:
    """Parse argv into a list of (directory, label) pairs.

    Expected pattern: <dir> --suite-label <name> [<dir> --suite-label <name> ...]

    Raises SystemExit(2) on bad input (matching argparse convention).
    """
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        print(_USAGE)
        raise SystemExit(0)

    pairs: list[tuple[Path, str]] = []
    tokens = list(argv)
    i = 0
    while i < len(tokens):
        directory = tokens[i]
        if i + 3 > len(tokens):
            print(
                f"error: expected --suite-label <name> after '{directory}'\n{_USAGE}",
                file=sys.stderr,
            )
            raise SystemExit(2)
        flag = tokens[i + 1]
        label = tokens[i + 2]
        if flag != "--suite-label":
            print(
                f"error: expected --suite-label after '{directory}', got '{flag}'\n{_USAGE}",
                file=sys.stderr,
            )
            raise SystemExit(2)
        pairs.append((Path(directory), label))
        i += 3

    if not pairs:
        print(
            f"error: at least one <directory> --suite-label <name> pair is required\n{_USAGE}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    return pairs


def _artifact_name_for_label(label: str) -> str:
    """Derive the artifact name from the suite label."""
    label_lower = label.lower()
    if "unit" in label_lower:
        return "unit-test-results"
    if "instrumented" in label_lower or "e2e" in label_lower:
        return "e2e-test-results"
    return label.lower().replace(" ", "-")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    pairs = parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    github_server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    github_run_id = os.environ.get("GITHUB_RUN_ID", "")
    sha = os.environ.get("WORKFLOW_RUN_SHA", "")
    workflow_run_branch = os.environ.get("WORKFLOW_RUN_BRANCH", "")

    if not token:
        print("Warning: GITHUB_TOKEN not set — skipping issue filing.", file=sys.stderr)
        return 0
    if not repository:
        print("Warning: GITHUB_REPOSITORY not set — skipping issue filing.", file=sys.stderr)
        return 0

    timestamp = datetime.now(tz=timezone.utc)

    for directory, label in pairs:
        artifact_name = _artifact_name_for_label(label)

        if not directory.exists():
            print(
                f"Note: result directory not found, skipping: {directory}",
                file=sys.stderr,
            )
            continue
        if not directory.is_dir():
            print(
                f"Note: path is not a directory, skipping: {directory}",
                file=sys.stderr,
            )
            continue

        failures = parse_failures(directory, label, artifact_name)
        if not failures:
            print(f"Note: no test failures found in {directory}", file=sys.stderr)
            continue

        print(f"Processing {len(failures)} failure(s) from {label}...", file=sys.stderr)
        for failure in failures:
            try:
                process_failure(
                    failure=failure,
                    directory=directory,
                    token=token,
                    repository=repository,
                    timestamp=timestamp,
                    sha=sha,
                    github_server_url=github_server_url,
                    github_run_id=github_run_id,
                    workflow_run_branch=workflow_run_branch,
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"  Error processing {failure.class_name}.{failure.method_name}: {exc}",
                    file=sys.stderr,
                )

    return 0


if __name__ == "__main__":
    sys.exit(main())
