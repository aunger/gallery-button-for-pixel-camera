#!/usr/bin/env python3
"""pinned_plugin_files.py: Read files at the commits this repository pins.

This repository pins two others: aunger/dot-and-tilde's plugins through
.claude/gb4pc-tools, and aunger/vcs-automation's actions through the `uses:`
lines of .github/workflows. The tests that check this repository against either
(test_orchestrate_config.py, test_session_byline_pins.py,
test_shared_helper_copies.py, ci/test_label_existence_integration.py,
ci/test_label_tables.py) read the files at the pinned commit, not at whatever
the other repository's main holds, so a pin bump is what brings a change into
them.

`PinnedRepository(directory, url, sha)` makes a shallow fetch of one commit
into a bare repository under `directory`, and raises `FetchFailed` when it
cannot (offline, or a sandbox that cannot reach GitHub), which a test may treat
as a reason to skip. `read(path)` returns one file; a file missing from the
fetched commit raises `MissingFile`, which is not a fetch failure and is never
a reason to skip.

`PinnedCommit(directory)` is the dot-and-tilde commit the marketplace pins;
its `read(plugin, path)` takes a path relative to the plugin's directory.
`vcs_automation_pins()` lists the vcs-automation commits the workflows pin.
`definition_without_docstring()` is what the copy checks compare.

This module has no `main`; it is imported, not run directly.
Standard library only.
"""

import ast
import json
import os
import re
import shutil
import subprocess

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MARKETPLACE = os.path.join(
    REPO_ROOT, ".claude", "gb4pc-tools", ".claude-plugin", "marketplace.json"
)
WORKFLOWS = os.path.join(REPO_ROOT, ".github", "workflows")
VCS_AUTOMATION = "https://github.com/aunger/vcs-automation.git"
# A `uses:` line that calls one of vcs-automation's actions, pinned to a commit.
VCS_AUTOMATION_USES = re.compile(
    r"uses:\s*aunger/vcs-automation/(?P<action>[A-Za-z0-9_.-]+)@(?P<sha>[0-9a-f]{40})\b"
)
GIT_TIMEOUT_SECONDS = 120


class FetchFailed(Exception):
    """The pinned commit could not be fetched."""


class MissingFile(Exception):
    """The pinned commit was fetched, but holds no file at the path asked for."""


class PinnedRepository:
    """One commit of a repository, fetched into `directory`."""

    def __init__(self, directory, url, sha):
        self.url = url
        self.sha = sha
        self._executable = shutil.which("git")
        self._git_dir = os.path.join(directory, "pinned-%s.git" % sha)
        try:
            if self._executable is None:
                raise FileNotFoundError("git is not on PATH")
            self._git("init", "-q", "--bare", self._git_dir)
            self._git("fetch", "-q", "--depth", "1", url, sha)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as error:
            detail = getattr(error, "stderr", None) or error
            raise FetchFailed("could not fetch %s at %s: %s" % (url, sha, detail)) from error

    def _git(self, *args):
        # --git-dir, so a GIT_DIR inherited from a git hook cannot redirect it.
        return subprocess.run(
            [self._executable, "--git-dir", self._git_dir, *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout

    def read(self, path):
        """Return the text of `path` at the pinned commit."""
        try:
            return self._git("show", "%s:%s" % (self.sha, path))
        except subprocess.CalledProcessError as error:
            raise MissingFile(
                "%s at %s has no %s: %s" % (self.url, self.sha, path, error.stderr.strip())
            ) from error


def marketplace_sources():
    """Return {plugin name: its source object} from the committed marketplace."""
    with open(MARKETPLACE, encoding="utf-8") as handle:
        return {entry["name"]: entry["source"] for entry in json.load(handle)["plugins"]}


class PinnedCommit(PinnedRepository):
    """The dot-and-tilde commit the marketplace pins, fetched into `directory`."""

    def __init__(self, directory):
        sources = marketplace_sources()
        pins = {(source["url"], source["sha"]) for source in sources.values()}
        if len(pins) != 1:
            raise ValueError("the marketplace pins more than one repository or commit: %r" % pins)
        ((repository, sha),) = pins
        self.paths = {name: source["path"] for name, source in sources.items()}
        super().__init__(directory, "https://github.com/%s.git" % repository, sha)
        self.repository = repository

    def read(self, plugin, path):
        """Return the text of `path`, relative to `plugin`'s directory, at the pinned commit."""
        return super().read("%s/%s" % (self.paths[plugin], path))


def vcs_automation_pins(action=None):
    """Return {sha: sorted workflow file names} for the vcs-automation commits the workflows pin.

    With `action`, only the `uses:` lines that call that action count.
    """
    pins = {}
    for name in sorted(os.listdir(WORKFLOWS)):
        if not name.endswith((".yml", ".yaml")):
            continue
        with open(os.path.join(WORKFLOWS, name), encoding="utf-8") as handle:
            for match in VCS_AUTOMATION_USES.finditer(handle.read()):
                if action is None or match.group("action") == action:
                    pins.setdefault(match.group("sha"), set()).add(name)
    return {sha: sorted(names) for sha, names in pins.items()}


def vcs_automation_pin(action):
    """Return the one vcs-automation commit the workflows pin `action` at."""
    pins = vcs_automation_pins(action)
    if len(pins) != 1:
        raise AssertionError(
            "expected the workflows to pin aunger/vcs-automation/%s at one commit, found %r"
            % (action, pins)
        )
    (sha,) = pins
    return sha


def definition_without_docstring(source, name, where):
    """Return the ast dump of the top-level function or assignment `name`, docstring removed."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:]
            return ast.dump(node)
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.dump(node)
    raise AssertionError("%s defines no top-level %s" % (where, name))
