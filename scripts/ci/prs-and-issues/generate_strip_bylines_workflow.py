#!/usr/bin/env python3
"""generate_strip_bylines_workflow.py: Copy the byline-strip script into its workflow.

The strip-bylines job in .github/workflows/strip-session-bylines.yml runs its
script in place, with no checkout.
The script is kept here as ordinary modules, where it is linted and tested, and
this generator copies them into the workflow between two marker lines:

    # generated code begins here
    # generated code ends here

The modules are concatenated in MODULES order, which puts each module after
the ones it imports, each headed by a comment naming its file.
Each module's shebang and docstring are dropped, and so are the single-line
`from <module> import name, ...` lines between these modules, since
everything ends up in one namespace.
The markers' indentation is applied to every non-blank generated line.

The generator refuses to write code it cannot copy faithfully:

- any other import of one of the modules (`import x`, an `as` alias, or a
  parenthesized import split over lines), which would leave the copy broken;
- a top-level name defined in more than one module, which the copy would
  silently resolve to the last definition;
- `${{`, which GitHub Actions would evaluate inside the `run:` block before
  Python saw the script.

Usage:
    python3 scripts/ci/prs-and-issues/generate_strip_bylines_workflow.py
    python3 scripts/ci/prs-and-issues/generate_strip_bylines_workflow.py --check

With --check it writes nothing and exits 1 if the workflow is out of date.
Either way it exits 2 if the markers are not each found exactly once, or if
the modules cannot be copied.
"""

import ast
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
WORKFLOW = os.path.join(REPO_ROOT, ".github", "workflows", "strip-session-bylines.yml")

MODULES = ["github_headers", "session_bylines", "strip_session_bylines"]

BEGIN = "# generated code begins here"
END = "# generated code ends here"

_NAMES = "|".join(MODULES)
_LOCAL_IMPORT = re.compile(rf"^from ({_NAMES}) import \w+(, \w+)*$")
_ANY_LOCAL_IMPORT = re.compile(rf"^\s*(from|import)\s+({_NAMES})\b")


def _top_level_names(tree: ast.Module) -> set[str]:
    """Return the names a module's top level defines, other than by import."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(t.id for t in targets if isinstance(t, ast.Name))
    return names


def script(directory: str = _HERE) -> str:
    """Return the modules' source joined into one script.

    Raises ValueError for anything the module docstring says it refuses.
    """
    parts = []
    defined: dict[str, str] = {}
    for name in MODULES:
        with open(os.path.join(directory, name + ".py"), encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)

        for symbol in sorted(_top_level_names(tree)):
            if symbol in defined:
                raise ValueError(f"{symbol!r} is defined in both {defined[symbol]} and {name}.py")
            defined[symbol] = f"{name}.py"

        lines = source.splitlines()
        start = 1 if lines and lines[0].startswith("#!") else 0
        first = tree.body[0] if tree.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            start = first.end_lineno

        kept = []
        for line in lines[start:]:
            if _LOCAL_IMPORT.match(line):
                continue
            if _ANY_LOCAL_IMPORT.match(line):
                raise ValueError(f"{name}.py: cannot copy this import: {line.strip()}")
            kept.append(line)
        parts.append(f"# --- {name}.py ---\n" + "\n".join(kept).strip("\n"))

    code = "\n\n\n".join(parts) + "\n"
    if "${{" in code:
        raise ValueError("the modules contain '${{', which GitHub Actions would evaluate")
    return code


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
    body = [(indent + line if line.strip() else "") + "\n" for line in code.splitlines()]
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
