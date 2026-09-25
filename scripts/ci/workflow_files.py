#!/usr/bin/env python3
"""Finding and reading this repository's workflow files.

Shared by the guards that assert a rule over every workflow:
`test_privileged_workflow_checkouts.py`, `test_setup_android_packages.py` and
`test_bounded_device_waits.py` in this directory, the first two of which each
carried their own copy until the second was written, and
`scripts/test_dependabot_config.sh`, which asks which actions the workflows
use.

Imported by bare module name, which resolves for the callers in this directory
because `.github/workflows/build.yml` discovers tests per directory rather than
recursively, putting `scripts/ci` on `sys.path`. A caller outside this
directory puts it there itself.
"""

import glob
import os

import yaml

_CI_DIR = os.path.dirname(os.path.abspath(__file__))

REPO_ROOT = os.path.dirname(os.path.dirname(_CI_DIR))

# Both extensions: GitHub accepts either, and a guard that checked one would
# pass a workflow it never opened.
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
