#!/usr/bin/env python3
"""pinned_plugin_files.py: Read plugin files at the commit .claude/gb4pc-tools pins.

The tests that check this repository against aunger/dot-and-tilde's plugins
(test_orchestrate_config.py, test_plugin_helper_copies.py) read the plugins'
files at the commit the marketplace pins, not at whatever dot-and-tilde's main
holds, so a pin bump is what brings a plugin change into them.

`PinnedCommit(directory)` makes a shallow fetch of that one commit into a bare
repository under `directory`, and raises `FetchFailed` when it cannot (offline,
or a sandbox that cannot reach GitHub), which a test may treat as a reason to
skip. `PinnedCommit.read(plugin, path)` returns one file of a plugin, and
raises CalledProcessError when it cannot.

This module has no `main`; it is imported, not run directly.
Standard library only.
"""

import json
import os
import shutil
import subprocess

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MARKETPLACE = os.path.join(
    REPO_ROOT, ".claude", "gb4pc-tools", ".claude-plugin", "marketplace.json"
)
GIT_TIMEOUT_SECONDS = 120


class FetchFailed(Exception):
    """The pinned commit could not be fetched."""


def marketplace_sources():
    """Return {plugin name: its source object} from the committed marketplace."""
    with open(MARKETPLACE, encoding="utf-8") as handle:
        return {entry["name"]: entry["source"] for entry in json.load(handle)["plugins"]}


class PinnedCommit:
    """The commit the marketplace pins, fetched into `directory`."""

    def __init__(self, directory):
        sources = marketplace_sources()
        pins = {(source["url"], source["sha"]) for source in sources.values()}
        if len(pins) != 1:
            raise ValueError("the marketplace pins more than one repository or commit: %r" % pins)
        ((self.url, self.sha),) = pins
        self.paths = {name: source["path"] for name, source in sources.items()}
        self._executable = shutil.which("git")
        self._git_dir = os.path.join(directory, "pinned.git")
        try:
            if self._executable is None:
                raise FileNotFoundError("git is not on PATH")
            self._git("init", "-q", "--bare", self._git_dir)
            self._git(
                "fetch", "-q", "--depth", "1", "https://github.com/%s.git" % self.url, self.sha
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as error:
            detail = getattr(error, "stderr", None) or error
            raise FetchFailed(
                "could not fetch %s at %s: %s" % (self.url, self.sha, detail)
            ) from error

    def _git(self, *args):
        # --git-dir, so a GIT_DIR inherited from a git hook cannot redirect it.
        return subprocess.run(
            [self._executable, "--git-dir", self._git_dir, *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout

    def read(self, plugin, path):
        """Return the text of `path`, relative to `plugin`'s directory, at the pinned commit."""
        return self._git("show", "%s:%s/%s" % (self.sha, self.paths[plugin], path))
