#!/usr/bin/env python3
"""Guard: every setup-android step says which SDK packages it installs.

`android-actions/setup-android` installs whatever its `packages` input names,
and that input carries a default the action's own releases change. At the
`v4.0.1` SHA this repository pins, the default is `tools platform-tools`.
Google then withdrew the standalone `tools` package from the SDK catalog, so
the action's `sdkmanager tools` call began exiting 1 and every job that had
accepted the default failed at its SDK setup step (issue #1127). Nothing in
this repository changed; a remote catalog did, and a SHA pin does not cover
what the pinned code downloads at run time.

Every such step now names its packages, so the installed set is a decision
recorded in this tree rather than whatever the upstream default happens to be
in a given release. This guard keeps a later edit, or a newly added job, from
quietly going back to the default.

The rule enforced here

    Every step using `android-actions/setup-android` must set the `packages`
    input to a string, and that string must not name `tools`.

The empty string is a valid value and means "install nothing". Two workflows
use it deliberately, `regenerate-gradle-toolchain.yml` and
`dependabot-verification-metadata-regen.yml`, because they pin their SDK
components by version in the step that follows rather than letting the action
choose. They were never affected by the `tools` withdrawal for the same reason
this guard exists: they had already stated their packages.

A missing value (`packages:` with nothing after it) is rejected rather than
read as the empty string. YAML gives it as None, and whether the Actions
runner would then treat the input as unset, and so reapply the action's
default, is not something this tree can settle. Writing `''` is unambiguous to
both the runner and the next reader, so the guard asks for it.

`tools` is rejected by name because it is the package the outage was about,
and because the action puts `tools/bin` on no code path's PATH: it adds
`cmdline-tools/<version>/bin` and `platform-tools`, and nothing else. Asking
for `tools` therefore buys a download and no reachable binary, whether or not
Google restores the package.

What this guard does not inspect

Only the `packages` input, and only in workflow files.

It does not check that the named packages exist in Google's catalog, which is
the remote fact that broke here and which no test in this tree can observe. A
step naming a package withdrawn tomorrow passes this guard and fails in CI,
exactly as `tools` did.

It does not check the pinned SHA or its currency. Which release the pins
should be on is a separate decision, and
`scripts/ci/prs-and-issues/watch_toolchain_bump.py`, which watches the Gradle
toolchain pins, has no notion of an action SHA.

It does not look at `sdkmanager --install` lines in `run:` steps. Those name
their packages inline and are already visible in the diff; the default this
guard is about has no such site to read.
"""

import glob
import os
import unittest

import yaml

_CI_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_CI_DIR))
_WORKFLOW_GLOBS = (".github/workflows/*.yml", ".github/workflows/*.yaml")

# The action whose `packages` input this guard inspects, matched on the part
# before `@` so that a hypothetical `android-actions/setup-android-something`
# is not mistaken for it.
SETUP_ANDROID_ACTION = "android-actions/setup-android"

# The input that decides what the action installs.
PACKAGES_INPUT = "packages"

# Withdrawn from the SDK catalog, and unreachable from PATH even when it
# installed. See the module docstring.
BANNED_PACKAGES = frozenset({"tools"})

# Marks an input the step did not mention at all, distinguishing it from
# `packages:` written with no value, which YAML gives as None.
_ABSENT = object()


def workflow_paths() -> list[str]:
    """Return every workflow file in the repository, sorted."""
    paths: list[str] = []
    for pattern in _WORKFLOW_GLOBS:
        paths.extend(glob.glob(os.path.join(_REPO_ROOT, pattern)))
    return sorted(paths)


def load_workflow(path: str) -> dict:
    """Return a parsed workflow file, or an empty mapping if it holds nothing."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def setup_android_steps(workflow: dict):
    """Yield (job name, step) for each setup-android step in a workflow."""
    for job_name, job in (workflow.get("jobs") or {}).items():
        for step in (job or {}).get("steps") or []:
            if not isinstance(step, dict):
                continue
            uses = step.get("uses")
            if isinstance(uses, str) and uses.split("@", 1)[0] == SETUP_ANDROID_ACTION:
                yield job_name, step


def violations(workflow: dict) -> list[str]:
    """Return a message for each setup-android step that breaks the rule."""
    found: list[str] = []
    for job_name, step in setup_android_steps(workflow):
        packages = (step.get("with") or {}).get(PACKAGES_INPUT, _ABSENT)
        if packages is _ABSENT:
            found.append(
                f"job {job_name!r} uses {SETUP_ANDROID_ACTION} without a "
                f"{PACKAGES_INPUT!r} input, so it installs whatever the pinned "
                f"release defaults to; name the packages, or '' for none"
            )
            continue
        if not isinstance(packages, str):
            found.append(
                f"job {job_name!r} sets {PACKAGES_INPUT!r} to {packages!r}; write it "
                f"as a string, using '' to install nothing"
            )
            continue
        banned = sorted(set(packages.split()) & BANNED_PACKAGES)
        if banned:
            found.append(
                f"job {job_name!r} asks for {', '.join(banned)} in {PACKAGES_INPUT!r}; "
                f"the package is withdrawn from the SDK catalog and its bin "
                f"directory is on no PATH the action sets"
            )
    return found


class SetupAndroidPackagesTest(unittest.TestCase):
    """Every workflow in this repository obeys the rule."""

    def test_every_setup_android_step_names_its_packages(self):
        paths = workflow_paths()
        self.assertTrue(paths, "found no workflow files to check")
        for path in paths:
            relative = os.path.relpath(path, _REPO_ROOT)
            with self.subTest(workflow=relative):
                found = violations(load_workflow(path))
                self.assertEqual([], found, f"{relative}: " + "; ".join(found))

    def test_the_action_is_actually_used_somewhere(self):
        """A guard over an action no workflow uses would pass on an empty set.

        This repository sets up the Android SDK in CI, so finding no step at
        all means the search stopped matching, not that the need went away.
        """
        steps = [
            (os.path.relpath(path, _REPO_ROOT), job)
            for path in workflow_paths()
            for job, _ in setup_android_steps(load_workflow(path))
        ]
        self.assertTrue(steps, f"no workflow step uses {SETUP_ANDROID_ACTION}")


class ViolationDetectionTest(unittest.TestCase):
    """The rule itself, exercised against synthetic workflows.

    Without these, a guard that had stopped detecting anything would still
    report a clean tree.
    """

    DEFAULTED = """
jobs:
  build:
    steps:
      - uses: android-actions/setup-android@40fd30fb8d7440372e1316f5d1809ec01dcd3699 # v4.0.1
      - run: ./gradlew assembleDebug
"""

    def _violations(self, text: str) -> list[str]:
        return violations(yaml.safe_load(text))

    def _with_packages(self, value: str) -> str:
        return self.DEFAULTED.replace(
            " # v4.0.1\n",
            " # v4.0.1\n        with:\n          packages: " + value + "\n",
        )

    def test_missing_packages_input_is_reported(self):
        found = self._violations(self.DEFAULTED)
        self.assertEqual(1, len(found), found)
        self.assertIn("defaults to", found[0])

    def test_platform_tools_is_accepted(self):
        self.assertEqual([], self._violations(self._with_packages("platform-tools")))

    def test_empty_string_is_accepted(self):
        self.assertEqual([], self._violations(self._with_packages("''")))

    def test_valueless_packages_key_is_reported(self):
        found = self._violations(self._with_packages(""))
        self.assertEqual(1, len(found), found)
        self.assertIn("as a string", found[0])

    def test_tools_is_reported(self):
        found = self._violations(self._with_packages("tools platform-tools"))
        self.assertEqual(1, len(found), found)
        self.assertIn("tools", found[0])

    def test_tools_is_reported_when_it_is_the_only_package(self):
        found = self._violations(self._with_packages("tools"))
        self.assertEqual(1, len(found), found)

    def test_platform_tools_alone_does_not_match_the_banned_name(self):
        # `tools` is matched as a whole entry, not as a substring, or every
        # `platform-tools` in the tree would be reported.
        self.assertEqual([], self._violations(self._with_packages("platform-tools emulator")))

    def test_a_different_action_is_ignored(self):
        other = self.DEFAULTED.replace(
            "android-actions/setup-android@", "android-actions/setup-android-something@"
        )
        self.assertEqual([], self._violations(other))

    def test_every_step_is_judged_not_only_the_first(self):
        two = self.DEFAULTED.replace(
            "      - run: ./gradlew assembleDebug\n",
            "      - run: ./gradlew assembleDebug\n"
            "  release:\n"
            "    steps:\n"
            "      - uses: android-actions/setup-android@40fd30fb8d7440372e1316f5d1809ec01dcd3699\n",
        )
        self.assertEqual(2, len(self._violations(two)))


if __name__ == "__main__":
    unittest.main()
