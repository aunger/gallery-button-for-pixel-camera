#!/usr/bin/env python3
"""Check Markdown files for fenced code blocks with no declared language.

MD040 (markdownlint's rule of the same name: "fenced code blocks should have a
language specified") was enforced by markdownlint-cli2 until issue #688
replaced it with mdformat. mdformat reformats a fence's contents but does not
check whether the fence declares a language, so a new offender (a bare
```` ``` ```` with nothing after it) would go uncaught. This script closes that
gap: it runs as part of the markdown family in scripts/lint.sh (the git
pre-commit hook) and in .github/workflows/lint.yml's markdown-mdformat job, so
the hook and CI share one definition of the check.

Only the Python standard library is used, so this runs under the ambient
`python3` with no dependency install step, the same way scripts/lint.sh invokes
other first-party scripts (see e.g. scripts/check_non_docs_changes.sh). This
also sidesteps scripts/lint.sh's $LINT_BIN_DIR indirection, which exists to
resolve third-party tools installed from a trusted registry (issue #667) and
does not apply to a first-party script checked out with the repo itself.

The fence scanner is a lightweight, line-based approximation of CommonMark's
fenced-code-block rules (opening delimiter of 3+ backticks or tildes, optional
up to 3 spaces of indentation, closed only by a same-character delimiter at
least as long): the mdformat and mdformat-gfm output this repo produces is
plain, unindented fences at column 0 (`--wrap keep` normalizes prose but not
fence indentation), so full blockquote/list-nesting fidelity is not needed.

Usage:
    scripts/check_md040.py FILE [FILE ...]

Exit code:
    0  no fenced code block is missing a language in any given file.
    1  at least one fenced code block is missing a language; each is printed
       to stderr as "path:line: fenced code block has no language (MD040)".
    2  usage error (no files given).
"""

import re
import sys

# A fence opener/closer: up to 3 spaces of indentation, then 3+ of the same
# fence character, then (for an opener) an optional info string.
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*(.*)$")


def find_violations(path: str) -> list[int]:
    """Return the 1-based line numbers of fenced code blocks with no language in *path*."""
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    violations: list[int] = []
    fence_char: str | None = None
    fence_len = 0
    for lineno, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")
        match = _FENCE_RE.match(line)
        if fence_char is None:
            if not match:
                continue
            marker, info = match.group(1), match.group(2)
            char = marker[0]
            # A backtick info string cannot itself contain a backtick in
            # CommonMark (it would be ambiguous with inline code spans), so a
            # line like that is not a valid opening fence.
            if char == "`" and "`" in info:
                continue
            fence_char, fence_len = char, len(marker)
            if not info.strip():
                violations.append(lineno)
        else:
            # Inside a fence: only a same-character delimiter at least as long
            # as the opener, with nothing else but trailing space, closes it.
            close_re = re.compile(
                r"^ {0,3}(" + re.escape(fence_char) + "{" + str(fence_len) + ",})[ \t]*$"
            )
            if close_re.match(line):
                fence_char, fence_len = None, 0
    return violations


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: check_md040.py FILE [FILE ...]", file=sys.stderr)
        return 2

    status = 0
    for path in argv:
        for lineno in find_violations(path):
            print(f"{path}:{lineno}: fenced code block has no language (MD040)", file=sys.stderr)
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
