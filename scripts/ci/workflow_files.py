#!/usr/bin/env python3
"""Finding and reading this repository's workflow files.

Shared by the guards in this directory that state a rule over every workflow
and assert it against the tree: `test_privileged_workflow_checkouts.py` and
`test_setup_android_packages.py`. Both need the same two things, the list of
workflow files and a parsed one, and both had their own copy until the second
guard was written.

Imported by bare module name. `.github/workflows/build.yml` discovers tests
per directory rather than recursively, so `scripts/ci` is on `sys.path` when
these guards run, the same way the repository's other intra-directory imports
resolve.

A guard's rule, its rationale and its limits stay in that guard. Nothing here
judges a workflow; this module only locates and parses them.
"""

import glob
import os

import yaml

_CI_DIR = os.path.dirname(os.path.abspath(__file__))

# The repository root, for resolving the globs below and for reporting a
# workflow by its path relative to the tree rather than by an absolute one.
REPO_ROOT = os.path.dirname(os.path.dirname(_CI_DIR))

# Both extensions, because GitHub accepts either and a guard that checked only
# one would pass a workflow it never opened.
WORKFLOW_GLOBS = (".github/workflows/*.yml", ".github/workflows/*.yaml")


def workflow_paths() -> list[str]:
    """Return every workflow file in the repository, sorted."""
    paths: list[str] = []
    for pattern in WORKFLOW_GLOBS:
        paths.extend(glob.glob(os.path.join(REPO_ROOT, pattern)))
    return sorted(paths)


def load_workflow(path: str) -> dict:
    """Return a parsed workflow file, or an empty mapping if it holds nothing."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def relative(path: str) -> str:
    """Return a workflow's path relative to the repository root, for messages."""
    return os.path.relpath(path, REPO_ROOT)
