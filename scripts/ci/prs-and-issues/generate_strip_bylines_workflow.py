#!/usr/bin/env python3
"""generate_strip_bylines_workflow.py: Copy the byline-strip script into its workflow.

The strip-bylines job in .github/workflows/strip-session-bylines.yml runs its
script in place, with no checkout.
The script is kept here as ordinary modules, where it is linted and tested, and
this generator copies them into the workflow between two marker lines:

    # generated code begins here
    # generated code ends here

The modules are concatenated in MODULES order, which puts each module after
the ones it imports.
Shebang lines are dropped, and so are the `from <module> import ...` lines
between these modules, since everything ends up in one namespace.
The markers' indentation is applied to every generated line.

Usage:
    python3 scripts/ci/prs-and-issues/generate_strip_bylines_workflow.py
    python3 scripts/ci/prs-and-issues/generate_strip_bylines_workflow.py --check

With --check it writes nothing and exits 1 if the workflow is out of date.
Either way it exits 2 if the markers are not each found exactly once.
"""

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
WORKFLOW = os.path.join(REPO_ROOT, ".github", "workflows", "strip-session-bylines.yml")

MODULES = ["github_headers", "session_bylines", "strip_session_bylines"]

BEGIN = "# generated code begins here"
END = "# generated code ends here"

_LOCAL_IMPORT = re.compile(r"^from (" + "|".join(MODULES) + r") import ")


def script() -> str:
    """Return the modules' source joined into one script."""
    parts = []
    for name in MODULES:
        with open(os.path.join(_HERE, name + ".py"), encoding="utf-8") as f:
            lines = f.read().splitlines()
        parts.append(
            "\n".join(
                line
                for line in lines
                if not line.startswith("#!") and not _LOCAL_IMPORT.match(line)
            ).strip("\n")
        )
    return "\n\n\n".join(parts) + "\n"


def generate(workflow: str, code: str) -> str:
    """Return `workflow` with `code` placed between the markers.

    Raises ValueError if either marker is missing or repeated, or if the end
    marker comes first.
    """
    lines = workflow.splitlines(keepends=True)
    begins = [i for i, line in enumerate(lines) if line.strip() == BEGIN]
    ends = [i for i, line in enumerate(lines) if line.strip() == END]
    if len(begins) != 1 or len(ends) != 1 or begins[0] > ends[0]:
        raise ValueError(f"expected one {BEGIN!r} line followed by one {END!r} line")
    begin, end = begins[0], ends[0]
    indent = lines[begin][: len(lines[begin]) - len(lines[begin].lstrip())]
    body = [(indent + line).rstrip() + "\n" for line in code.splitlines()]
    return "".join(lines[: begin + 1] + body + lines[end:])


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    check = args == ["--check"]
    if args and not check:
        print("usage: generate_strip_bylines_workflow.py [--check]", file=sys.stderr)
        return 2

    with open(WORKFLOW, encoding="utf-8") as f:
        current = f.read()
    try:
        wanted = generate(current, script())
    except ValueError as exc:
        print(f"{os.path.relpath(WORKFLOW, REPO_ROOT)}: {exc}", file=sys.stderr)
        return 2

    if check:
        if wanted != current:
            print(
                f"{os.path.relpath(WORKFLOW, REPO_ROOT)} is out of date; run "
                "scripts/ci/prs-and-issues/generate_strip_bylines_workflow.py",
                file=sys.stderr,
            )
            return 1
        return 0
    if wanted != current:
        with open(WORKFLOW, "w", encoding="utf-8") as f:
            f.write(wanted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
