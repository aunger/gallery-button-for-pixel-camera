#!/usr/bin/env python3
"""Guard: every setup-android step says which SDK packages it installs.

`android-actions/setup-android` installs whatever its `packages` input
names, and that input's default changes between the action's releases. At the
pinned `v4.0.1` it is `tools platform-tools`. Google then withdrew the
standalone `tools` package from the SDK catalog, so `sdkmanager tools` began
exiting 1 and every job that had accepted the default failed at its SDK setup
step (issue #1127). A SHA pin does not cover what the pinned code downloads at
run time, so the installed set is named here instead.

The rule: every step using `android-actions/setup-android` must set `packages`
to a string, and that string must not name `tools`.

`packages: ''` is valid and installs nothing, for a job that pins its own
components in the step that follows; `regenerate-gradle-toolchain.yml` and
`dependabot-verification-metadata-regen.yml` both do that, and so never
requested `tools`. A `packages:` key with no value is rejected instead: YAML
gives it as None, and whether the runner would then treat the input as unset
and reapply the action's default is not something this tree can settle.

`tools` is rejected by name because the action puts `tools/bin` on no PATH it
sets. It adds `cmdline-tools/<version>/bin` and `platform-tools`, nothing
else, so asking for `tools` buys a download and no reachable binary.

Limits: this reads the `packages` input of workflow files and nothing else. It
cannot see Google's catalog, so a step naming a package withdrawn tomorrow
passes here and fails in CI exactly as `tools` did. It judges no `sdkmanager
--install` line and no pin currency.
"""

import unittest

import yaml
from workflow_files import load_workflow, relative, workflow_paths

# Matched on the part before `@`, so `setup-android-something` is not mistaken
# for it.
SETUP_ANDROID_ACTION = "android-actions/setup-android"

PACKAGES_INPUT = "packages"

# Withdrawn from the catalog, and unreachable from PATH even when it installed.
BANNED_PACKAGES = frozenset({"tools"})

# An input the step did not mention, as distinct from `packages:` written with
# no value, which YAML gives as None.
_ABSENT = object()


def setup_android_steps(workflow: dict):
    """Yield (job name, step) for each setup-android step in a workflow."""
    for job_name, job in (workflow.get("jobs") or {}).items():
        for step in (job or {}).get("steps") or []:
            if not isinstance(step, dict):
                continue
            uses = step.get("uses")
            if isinstance(uses, str) and uses.split("@", 1)[0] == SETUP_ANDROID_ACTION:
                yield job_name, step


def step_label(job_name: str, step: dict) -> str:
    """Name the offending step, so two in one job do not read identically.

    `name:` is optional, so the `uses:` value stands in when it is absent. Two
    unnamed steps in one job then share a label, but it is the only other thing
    a step is guaranteed to carry.
    """
    name = step.get("name")
    if isinstance(name, str) and name.strip():
        return f"job {job_name!r} step {name.strip()!r}"
    return f"job {job_name!r} step {step.get('uses')!r}"


def violations(workflow: dict) -> list[str]:
    """Return a message for each setup-android step that breaks the rule."""
    found: list[str] = []
    for job_name, step in setup_android_steps(workflow):
        where = step_label(job_name, step)
        packages = (step.get("with") or {}).get(PACKAGES_INPUT, _ABSENT)
        if packages is _ABSENT:
            found.append(
                f"{where} sets no {PACKAGES_INPUT!r} input, so it installs "
                f"whatever the pinned release defaults to; name the packages, "
                f"or '' for none"
            )
            continue
        if not isinstance(packages, str):
            found.append(
                f"{where} sets {PACKAGES_INPUT!r} to {packages!r}; write it "
                f"as a string, using '' to install nothing"
            )
            continue
        banned = sorted(set(packages.split()) & BANNED_PACKAGES)
        if banned:
            found.append(
                f"{where} asks for {', '.join(banned)} in {PACKAGES_INPUT!r}; "
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
            rel = relative(path)
            with self.subTest(workflow=rel):
                found = violations(load_workflow(path))
                self.assertEqual([], found, f"{rel}: " + "; ".join(found))

    def test_the_action_is_actually_used_somewhere(self):
        """A guard over an action no workflow uses would pass on an empty set."""
        steps = [
            (relative(path), job)
            for path in workflow_paths()
            for job, _ in setup_android_steps(load_workflow(path))
        ]
        self.assertTrue(steps, f"no workflow step uses {SETUP_ANDROID_ACTION}")


class ViolationDetectionTest(unittest.TestCase):
    """The rule itself: without these, a guard that had stopped detecting
    anything would still report a clean tree."""

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
        # Matched as a whole entry, not a substring.
        self.assertEqual([], self._violations(self._with_packages("platform-tools emulator")))

    def test_a_different_action_is_ignored(self):
        other = self.DEFAULTED.replace(
            "android-actions/setup-android@", "android-actions/setup-android-something@"
        )
        self.assertEqual([], self._violations(other))

    def test_a_named_step_is_reported_by_its_name(self):
        named = self.DEFAULTED.replace(
            "      - uses: android-actions/setup-android@",
            "      - name: Set up Android SDK\n        uses: android-actions/setup-android@",
        )
        found = self._violations(named)
        self.assertEqual(1, len(found), found)
        self.assertIn("step 'Set up Android SDK'", found[0])

    def test_an_unnamed_step_falls_back_to_its_uses(self):
        found = self._violations(self.DEFAULTED)
        self.assertEqual(1, len(found), found)
        self.assertIn("android-actions/setup-android@", found[0])

    def test_two_steps_in_one_job_are_told_apart(self):
        # The reason a step is named at all.
        two = self.DEFAULTED.replace(
            "      - run: ./gradlew assembleDebug\n",
            "      - name: Set up Android SDK again\n"
            "        uses: android-actions/setup-android@40fd30fb8d7440372e1316f5d1809ec01dcd3699\n",
        ).replace(
            "      - uses: android-actions/setup-android@",
            "      - name: Set up Android SDK\n        uses: android-actions/setup-android@",
        )
        found = self._violations(two)
        self.assertEqual(2, len(found), found)
        self.assertEqual(2, len(set(found)), found)

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
