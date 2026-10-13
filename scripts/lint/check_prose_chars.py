#!/usr/bin/env python3
"""Check text files for the typographic characters .claude/rules/prose-style.md bans.

The rule reaches "prose in documents, code comments, and text output" and asks
for widely compatible characters: no em or en dash, no ellipsis character, and
no angled or smart quotes. Issue #935 swept the string resources and added
app/src/test/java/com/gb4pc/StringResourceProseStyleTest.kt, which reflects
over `R.string` and so can see nothing else. Issue #964 swept the last fourteen
em dashes out of build scripts, a workflow, and the manifest, all of which that
test structurally cannot reach.

This is the guard for that larger population. It scans the raw text of every
file it is handed, so it sees code comments, build output strings such as a
`GradleException` message, workflow step names, and Markdown alike. It runs in
the hygiene family of scripts/lint/lint.sh, which means the git pre-commit hook
checks staged files and .github/workflows/lint.yml's hygiene job checks the
whole tree on every pull request, docs-only ones included.

The two guards overlap on `strings.xml` but are not redundant: this one reads
the file as written, while the Kotlin test reads the values Android decodes,
so only the Kotlin test catches a banned character spelled as a `\\u2014`
escape in a string resource.

What this does not do
---------------------

The rule exempts "characters chosen for their specific meaning in code or
markup". This check does not try to tell prose from code, because the tree has
no such use today and a per-line judgement would be guesswork. A file that
genuinely needs one of these characters should spell it as an escape, the way
this script and the Kotlin test do; failing that, add the path to
EXEMPT_PATHS with the reason.

Usage:
    scripts/lint/check_prose_chars.py FILE [FILE ...]

Exit code:
    0  no given file contains a banned character.
    1  at least one does; each occurrence is printed to stderr as
       "path:line:column: <name> U+XXXX (<advice>)".
    2  usage error (no files given).
"""

import os
import sys

# Spelled as escapes so this file, which has to name them, is not itself a
# place they can be copied out of. Keep in step with bannedCharacters in
# StringResourceProseStyleTest.kt.
BANNED_CHARACTERS = {
    "\u2014": ("em dash", "use a comma, semicolon, parentheses, or '--'"),
    "\u2013": ("en dash", "use a comma, semicolon, parentheses, or '-'"),
    "\u2026": ("ellipsis character", "use three periods"),
    "\u2018": ("smart single quote", "use '"),
    "\u2019": ("smart single quote", "use '"),
    "\u201c": ("smart double quote", 'use "'),
    "\u201d": ("smart double quote", 'use "'),
    "\u00ab": ("angle quote", 'use "'),
    "\u00bb": ("angle quote", 'use "'),
}

# Repository-relative paths the check skips, each with its reason.
EXEMPT_PATHS = {
    # Vendored Gradle wrapper boilerplate (its comments use angle quotes).
    # `gradle wrapper` regenerates it, so a hand edit is undone at the next
    # bump (issue #964).
    "gradlew",
}


def find_violations(path: str) -> list[tuple[int, int, str]]:
    """Return (line, column, character) for each banned character in *path*, 1-based.

    A file that is not valid UTF-8 is not prose this rule can apply to, so it
    yields no violations rather than an error.
    """
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        return []

    return [
        (lineno, col, ch)
        for lineno, line in enumerate(lines, start=1)
        for col, ch in enumerate(line, start=1)
        if ch in BANNED_CHARACTERS
    ]


def is_exempt(path: str) -> bool:
    return os.path.normpath(path) in EXEMPT_PATHS


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: check_prose_chars.py FILE [FILE ...]", file=sys.stderr)
        return 2

    status = 0
    for path in argv:
        if is_exempt(path):
            continue
        for lineno, col, ch in find_violations(path):
            name, advice = BANNED_CHARACTERS[ch]
            print(f"{path}:{lineno}:{col}: {name} U+{ord(ch):04X} ({advice})", file=sys.stderr)
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
