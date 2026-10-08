#!/usr/bin/env python3
"""Guard: every Android SDK install site satisfies compileSdk and agrees with the hook.

Issues #987 and #988. The API level each Gradle module compiles against
(`compileSdk` in its `build.gradle.kts`) and the SDK packages installed so it
can are set in unrelated places:

1. `.claude/hooks/session-start.sh`, the `SDK_PACKAGES` associative array. This
   is the source of truth.
2. `.claude/setup-environment.sh`, the `SDK_PACKAGES` indexed array.
   `scripts/test_setup_environment.sh` already holds it equal to the hook's list.
3. Every `sdkmanager` invocation in a workflow `run:` step. The ones that
   install a platform are in `dependabot-verification-metadata-regen.yml` and
   `regenerate-gradle-toolchain.yml`; `build.yml`'s installs the E2E emulator.
4. Every `android-actions/setup-android` step's `packages` input. Today each
   names `platform-tools` or nothing, so none of them installs a platform.

The jobs that set up the SDK through item 4 (in `build.yml`, `codeql.yml` and
`release.yml`) install no platform themselves, so they build against a
platform no site in this repository declares: the one preinstalled on the
GitHub-hosted runner image. That image's
platform set moves with the image, not with this tree, so nothing here can
check it.

A missed site fails at build time, but late: only on the job that was missed,
after it has set up a JDK, an SDK and a Gradle cache, and with an error about a
missing platform rather than about the mismatch that caused it.

The rules:

- Every site that installs an SDK platform installs one for each distinct
  `compileSdk` across the modules. Both `.claude/` lists count as installing
  one, so a list that lost its platform altogether is caught too. A workflow
  site that names no `platforms;` package (`build.yml`'s emulator and
  system-image install, a `packages: platform-tools` input) is not a platform
  install site and is not held to it.
- A platform satisfies `compileSdk = N` when its package is `platforms;android-N`
  or `platforms;android-N.M`, matched on the major alone. Newer packages carry
  a minor version (`platforms;android-37` does not exist), so a string
  comparison could not work.
- Every package a workflow installs, in a family the hook's list provisions,
  appears in the hook's list. A family is the part of the id before the first
  `;`, so `build-tools;36.0.0` is in the `build-tools` family and
  `platform-tools` is its own. The relation is a subset, not equality: the
  `.claude/` pair deliberately also installs older build-tools that the
  workflows do not need. Families the hook does not provision (`emulator`,
  `system-images`) are outside the relation, because they serve the E2E
  emulator rather than the build.

The `.claude/` lists are not read from a shared file, and that is deliberate:
`.claude/setup-environment.sh` is pasted into the Claude Code for Web
environment and runs outside any checkout, so it cannot read one. A drift guard
is what keeps the copies honest instead.

Limits: `compileSdk` is read only in the plain `compileSdk = N` form. Any other
form (`compileSdk { version = release(N) { ... } }`, a variable), and any
property whose name starts with `compileSdk` (`compileSdkPreview`,
`compileSdkVersion(N)`), is reported as unreadable rather than skipped, so
whoever adopts one must teach this guard to read it. A minor `compileSdk` level is not modelled: `android-37.0` satisfies
`compileSdk = 37`, and so would `android-37.1`. The guard reads no shell script
other than the two `.claude/` ones, so an `sdkmanager` call in a script under
`scripts/` is not seen. `scripts/ci/test-support/setup-e2e-emulator.sh` holds
one, which installs only the emulator, its system image and `platform-tools`.
"""

import glob
import os
import re
import shlex
import unittest

import yaml

# The other guard over setup-android steps owns how one is recognised; reusing
# it keeps the two from disagreeing about which steps exist.
from test_setup_android_packages import PACKAGES_INPUT, setup_android_steps, step_label
from workflow_files import REPO_ROOT, load_workflow, relative, workflow_paths

HOOK = os.path.join(REPO_ROOT, ".claude", "hooks", "session-start.sh")
SETUP = os.path.join(REPO_ROOT, ".claude", "setup-environment.sh")

# The workflows known to install an SDK platform. Checked by name so that a
# parser change that stopped seeing either one fails here instead of leaving the
# rule to pass over nothing.
KNOWN_PLATFORM_WORKFLOWS = (
    ".github/workflows/dependabot-verification-metadata-regen.yml",
    ".github/workflows/regenerate-gradle-toolchain.yml",
)

_COMPILE_SDK = re.compile(r"^\s*compileSdk\s*=\s*(\d+)\s*(?://.*)?$")
# No closing word boundary, so the sibling properties (`compileSdkPreview`,
# `compileSdkVersion`, `compileSdkExtension`) are examined too, and reported as
# unreadable rather than passed over.
_MENTIONS_COMPILE_SDK = re.compile(r"\bcompileSdk")
_PLATFORM = re.compile(r"^platforms;android-(\d+)(?:\.\d+)?$")
_HOOK_KEY = re.compile(r'^\s*\["([^"]+)"\]=')
_SETUP_ITEM = re.compile(r'^\s*"([^"]+)"\s*$')
_SHELL_SEPARATORS = frozenset({"&&", "||", "|", ";", "&", "(", ")"})
# sdkmanager options under which the packages named are not being installed.
_NOT_AN_INSTALL = frozenset({"--uninstall"})


def build_files() -> list[str]:
    """Return the root build script and every module's, sorted."""
    paths = glob.glob(os.path.join(REPO_ROOT, "build.gradle.kts"))
    paths += glob.glob(os.path.join(REPO_ROOT, "*", "build.gradle.kts"))
    return sorted(paths)


def read_compile_sdk(text: str) -> tuple[list[int], list[str]]:
    """Return (levels, unreadable lines) for the compileSdk pins in a build script.

    A line that is a comment is ignored. Any other line naming `compileSdk`,
    or a property whose name starts with it, must be the plain
    `compileSdk = N` form; one that is not is returned as
    unreadable, so a form this guard cannot read fails it instead of slipping
    past.
    """
    levels: list[int] = []
    unreadable: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("//", "*", "/*")):
            continue
        if not _MENTIONS_COMPILE_SDK.search(stripped):
            continue
        match = _COMPILE_SDK.match(line)
        if match:
            levels.append(int(match.group(1)))
        else:
            unreadable.append(stripped)
    return levels, unreadable


def _array_block(text: str, opener: str) -> list[str]:
    """Return the lines of the bash array whose opening line is `opener`."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == opener:
            block: list[str] = []
            for inner in lines[i + 1 :]:
                if inner.strip() == ")":
                    return block
                block.append(inner)
            raise ValueError(f"array opened by {opener!r} is never closed")
    raise ValueError(f"no line reads {opener!r}")


def hook_packages(text: str) -> list[str]:
    """Return the package ids keying the hook's SDK_PACKAGES associative array."""
    block = _array_block(text, "declare -A SDK_PACKAGES=(")
    return [m.group(1) for m in map(_HOOK_KEY.match, block) if m]


def setup_packages(text: str) -> list[str]:
    """Return the package ids in the Setup script's SDK_PACKAGES indexed array."""
    block = _array_block(text, "SDK_PACKAGES=(")
    return [m.group(1) for m in map(_SETUP_ITEM.match, block) if m]


def sdkmanager_installs(script: str) -> list[list[str]]:
    """Return the packages named by each sdkmanager invocation in a shell script.

    Line continuations are joined first, so an invocation split across lines
    is read whole. Options (anything starting with `-`) are dropped, which
    leaves `sdkmanager --licenses` as an invocation naming no packages. An
    `sdkmanager --uninstall` invocation removes rather than installs, so it is
    left out altogether. An invocation's arguments end at the first shell
    separator.

    Raises ValueError when a line naming sdkmanager cannot be tokenized, since
    skipping it would hide exactly the line this guard exists to read.
    """
    installs: list[list[str]] = []
    for line in script.replace("\\\n", " ").splitlines():
        if "sdkmanager" not in line:
            continue
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = "#"
        try:
            tokens = list(lexer)
        except ValueError as e:
            raise ValueError(f"cannot tokenize {line.strip()!r}: {e}") from e
        packages = None
        options: set[str] = set()
        for token in tokens:
            if packages is None:
                if os.path.basename(token) == "sdkmanager":
                    packages, options = [], set()
            elif token in _SHELL_SEPARATORS:
                if not options & _NOT_AN_INSTALL:
                    installs.append(packages)
                packages = None
            elif token.startswith("-"):
                options.add(token.split("=", 1)[0])
            else:
                packages.append(token)
        if packages is not None and not options & _NOT_AN_INSTALL:
            installs.append(packages)
    return installs


def workflow_installs(workflow: dict):
    """Yield (job name, step label, packages) per sdkmanager call in a workflow."""
    for job_name, job in (workflow.get("jobs") or {}).items():
        for index, step in enumerate((job or {}).get("steps") or []):
            if not isinstance(step, dict) or not isinstance(step.get("run"), str):
                continue
            name = step.get("name")
            label = name.strip() if isinstance(name, str) and name.strip() else f"#{index + 1}"
            for packages in sdkmanager_installs(step["run"]):
                yield job_name, label, packages


def platform_levels(packages) -> set[int]:
    """Return the API majors of the SDK platforms among `packages`."""
    return {int(m.group(1)) for m in map(_PLATFORM.match, packages) if m}


def names_a_platform(packages) -> bool:
    """Whether any package is an SDK platform, satisfiable or not."""
    return any(p.startswith("platforms;") for p in packages)


def family(package: str) -> str:
    """Return a package id's family: the part before its first `;`."""
    return package.split(";", 1)[0]


def violations(
    compile_sdks: dict[str, int],
    authority: list[str],
    lists: dict[str, list[str]],
    workflow_sites: list[tuple[str, list[str]]],
) -> list[str]:
    """Return a message for each install site that breaks the rules.

    `compile_sdks` maps each build script to its compileSdk. `authority` is
    the hook's package list. `lists` maps each `.claude/` list to its packages
    (the hook's among them), and every one is held to compileSdk.
    `workflow_sites` pairs a label for each workflow sdkmanager invocation with
    the packages it names.
    """
    found: list[str] = []
    needed = set(compile_sdks.values())
    by_level = ", ".join(f"{path} = {level}" for path, level in sorted(compile_sdks.items()))

    def check_platforms(where: str, packages: list[str]) -> None:
        missing = sorted(needed - platform_levels(packages))
        if missing:
            wanted = " and ".join(f"platforms;android-{n}[.M]" for n in missing)
            found.append(f"{where} installs no {wanted}, which compileSdk needs ({by_level})")

    for where, packages in lists.items():
        check_platforms(where, packages)

    families = {family(p) for p in authority}
    for where, packages in workflow_sites:
        if names_a_platform(packages):
            check_platforms(where, packages)
        extra = [p for p in packages if family(p) in families and p not in authority]
        if extra:
            found.append(
                f"{where} installs {', '.join(extra)}, which the session-start "
                f"hook's SDK_PACKAGES does not list; add it there (and to "
                f".claude/setup-environment.sh), or install what the hook lists"
            )
    return found


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def tree_compile_sdks() -> tuple[dict[str, int], list[str]]:
    """Return (compileSdk per build script, problems reading them) for this tree."""
    levels: dict[str, int] = {}
    problems: list[str] = []
    for path in build_files():
        rel = os.path.relpath(path, REPO_ROOT)
        found, unreadable = read_compile_sdk(_read(path))
        for line in unreadable:
            problems.append(f"{rel}: cannot read compileSdk from {line!r}")
        if len(found) > 1:
            problems.append(f"{rel}: sets compileSdk {len(found)} times")
        if found:
            levels[rel] = found[0]
    return levels, problems


def setup_android_installs(workflow: dict):
    """Yield (step label, packages) per setup-android step's `packages` input.

    A step whose input is missing or not a string is skipped here, because
    `test_setup_android_packages.py` already fails on it.
    """
    for job_name, step in setup_android_steps(workflow):
        packages = (step.get("with") or {}).get(PACKAGES_INPUT)
        if isinstance(packages, str):
            yield step_label(job_name, step), packages.split()


def tree_workflow_sites() -> list[tuple[str, list[str]]]:
    """Return a label and the packages for each install site in a workflow.

    The sites are every sdkmanager call in a `run:` step and every
    setup-android step's `packages` input.
    """
    sites: list[tuple[str, list[str]]] = []
    for path in workflow_paths():
        workflow = load_workflow(path)
        for job, step, packages in workflow_installs(workflow):
            sites.append((f"{relative(path)} job {job!r} step {step!r}", packages))
        for label, packages in setup_android_installs(workflow):
            sites.append((f"{relative(path)} {label} {PACKAGES_INPUT!r} input", packages))
    return sites


class TreeTest(unittest.TestCase):
    """This repository's install sites obey the rules."""

    def test_compile_sdk_is_readable(self):
        levels, problems = tree_compile_sdks()
        if problems:
            self.fail("\n".join(problems))
        self.assertIn("app/build.gradle.kts", levels, "found no compileSdk in the app module")

    def test_every_install_site_satisfies_compile_sdk_and_the_hook(self):
        levels, _ = tree_compile_sdks()
        hook = hook_packages(_read(HOOK))
        lists = {
            relative(HOOK) + " SDK_PACKAGES": hook,
            relative(SETUP) + " SDK_PACKAGES": setup_packages(_read(SETUP)),
        }
        found = violations(levels, hook, lists, tree_workflow_sites())
        if found:
            self.fail("\n".join(found))

    def test_the_sites_are_actually_read(self):
        """A rule over lists the parser stopped finding would pass on nothing."""
        self.assertTrue(hook_packages(_read(HOOK)), "read no packages from the hook")
        self.assertTrue(setup_packages(_read(SETUP)), "read no packages from the Setup script")
        installing = {
            label.split(" job ", 1)[0]
            for label, packages in tree_workflow_sites()
            if names_a_platform(packages)
        }
        for path in KNOWN_PLATFORM_WORKFLOWS:
            with self.subTest(workflow=path):
                self.assertIn(path, installing, f"{path} has no sdkmanager call naming a platform")
        inputs = [label for label, _ in tree_workflow_sites() if label.endswith(" input")]
        self.assertTrue(inputs, "read no setup-android packages input from any workflow")


class ParsingTest(unittest.TestCase):
    """The readers, against the shapes the real files use."""

    def test_compile_sdk_plain_form(self):
        self.assertEqual(([35], []), read_compile_sdk("android {\n    compileSdk = 35\n}\n"))

    def test_compile_sdk_with_trailing_comment(self):
        self.assertEqual(([37], []), read_compile_sdk("    compileSdk = 37 // API 37.0\n"))

    def test_compile_sdk_in_a_comment_is_ignored(self):
        text = "    // accepts any symbol up to compileSdk.\n    compileSdk = 35\n"
        self.assertEqual(([35], []), read_compile_sdk(text))

    def test_compile_sdk_block_form_is_unreadable(self):
        levels, unreadable = read_compile_sdk("    compileSdk {\n        version = release(37)\n")
        self.assertEqual([], levels)
        self.assertEqual(["compileSdk {"], unreadable)

    def test_compile_sdk_from_a_variable_is_unreadable(self):
        self.assertEqual(
            ([], ["compileSdk = sdkLevel"]), read_compile_sdk("compileSdk = sdkLevel\n")
        )

    def test_sibling_compile_sdk_properties_are_unreadable(self):
        # A module that set its level through one of these would otherwise drop
        # out of the levels unnoticed.
        for line in (
            'compileSdkPreview = "Baklava"',
            "compileSdkVersion(35)",
            "compileSdkVersion = 35",
            "compileSdkExtension = 15",
        ):
            with self.subTest(line=line):
                self.assertEqual(([], [line]), read_compile_sdk("    " + line + "\n"))

    def test_hook_array_keys(self):
        text = (
            "declare -A SDK_PACKAGES=(\n"
            '    ["platforms;android-35"]="$ANDROID_HOME_DIR/platforms/android-35"\n'
            '    ["platform-tools"]="$ANDROID_HOME_DIR/platform-tools"\n'
            ")\n"
            '["outside;the-array"]="x"\n'
        )
        self.assertEqual(["platforms;android-35", "platform-tools"], hook_packages(text))

    def test_setup_array_items(self):
        text = 'SDK_PACKAGES=(\n    "platforms;android-35"\n    "build-tools;36.0.0"\n)\n'
        self.assertEqual(["platforms;android-35", "build-tools;36.0.0"], setup_packages(text))

    def test_missing_array_is_an_error(self):
        with self.assertRaises(ValueError):
            setup_packages("OTHER=(\n)\n")

    def test_unclosed_array_is_an_error(self):
        with self.assertRaises(ValueError):
            hook_packages('declare -A SDK_PACKAGES=(\n    ["platform-tools"]="x"\n')

    def test_sdkmanager_single_line(self):
        run = 'sdkmanager --install "build-tools;36.0.0" "platforms;android-35" "platform-tools"\n'
        self.assertEqual(
            [["build-tools;36.0.0", "platforms;android-35", "platform-tools"]],
            sdkmanager_installs(run),
        )

    def test_sdkmanager_continued_across_lines(self):
        run = 'sdkmanager --install \\\n  "emulator" \\\n  "system-images;android-35;google_apis;x86_64"\n\nAVD_HOME=x\n'
        self.assertEqual(
            [["emulator", "system-images;android-35;google_apis;x86_64"]],
            sdkmanager_installs(run),
        )

    def test_sdkmanager_arguments_end_at_a_separator(self):
        run = 'yes | sdkmanager --licenses && sdkmanager "platforms;android-35" | tail -1\n'
        self.assertEqual([[], ["platforms;android-35"]], sdkmanager_installs(run))

    def test_sdkmanager_by_path(self):
        self.assertEqual(
            [["platform-tools"]], sdkmanager_installs('"$TOOLS/sdkmanager" platform-tools\n')
        )

    def test_sdkmanager_uninstall_is_not_an_install(self):
        run = (
            'sdkmanager --uninstall "platforms;android-35" && '
            'sdkmanager --install "platforms;android-37.0"\n'
        )
        self.assertEqual([["platforms;android-37.0"]], sdkmanager_installs(run))

    def test_line_without_sdkmanager_is_not_tokenized(self):
        # An unbalanced quote elsewhere in the script is not this guard's business.
        self.assertEqual([], sdkmanager_installs("echo \"don't\n"))

    def test_untokenizable_sdkmanager_line_is_an_error(self):
        with self.assertRaises(ValueError):
            sdkmanager_installs('sdkmanager "platforms;android-35\n')

    def test_workflow_steps_are_labelled(self):
        workflow = yaml.safe_load(
            "jobs:\n"
            "  regen:\n"
            "    steps:\n"
            "      - uses: actions/checkout@v7\n"
            "      - name: Pin the SDK\n"
            '        run: sdkmanager --install "platforms;android-35"\n'
            '      - run: sdkmanager --install "platform-tools"\n'
        )
        self.assertEqual(
            [
                ("regen", "Pin the SDK", ["platforms;android-35"]),
                ("regen", "#3", ["platform-tools"]),
            ],
            list(workflow_installs(workflow)),
        )

    SETUP_ANDROID = (
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        "      - name: Set up Android SDK\n"
        "        uses: android-actions/setup-android@be39fa834029ff78f1a44aa3bb0819b8fc2bd8fd\n"
        "        with:\n"
        "          packages: {packages}\n"
    )

    def _setup_android(self, packages: str):
        workflow = yaml.safe_load(self.SETUP_ANDROID.format(packages=packages))
        return list(setup_android_installs(workflow))

    def test_setup_android_packages_input_is_read(self):
        self.assertEqual(
            [("job 'build' step 'Set up Android SDK'", ["platform-tools", "platforms;android-35"])],
            self._setup_android("platform-tools platforms;android-35"),
        )

    def test_setup_android_empty_packages_input_names_nothing(self):
        self.assertEqual([("job 'build' step 'Set up Android SDK'", [])], self._setup_android("''"))

    def test_setup_android_valueless_packages_input_is_left_to_the_other_guard(self):
        self.assertEqual([], self._setup_android(""))

    def test_setup_android_platform_is_held_to_the_rules(self):
        # The shape that passed both guards before setup-android inputs were read.
        (label, packages) = self._setup_android("platform-tools platforms;android-34")[0]
        found = violations(
            {"app/build.gradle.kts": 35},
            ["platforms;android-35", "platform-tools"],
            {},
            [(label, packages)],
        )
        self.assertEqual(2, len(found), found)
        self.assertIn("installs no platforms;android-35[.M]", found[0])
        self.assertIn("installs platforms;android-34, which", found[1])


class ViolationDetectionTest(unittest.TestCase):
    """The rules themselves: without these, a guard that had stopped detecting
    anything would still report a clean tree."""

    HOOK_LIST = [
        "platforms;android-35",
        "build-tools;36.0.0",
        "build-tools;35.0.0",
        "platform-tools",
    ]
    WORKFLOW = ["build-tools;36.0.0", "platforms;android-35", "platform-tools"]

    def _violations(self, compile_sdks=None, hook=None, workflow=None, setup=None):
        hook = self.HOOK_LIST if hook is None else hook
        lists = {"hook": hook, "setup": hook if setup is None else setup}
        return violations(
            {"app/build.gradle.kts": 35} if compile_sdks is None else compile_sdks,
            hook,
            lists,
            [("regen", self.WORKFLOW if workflow is None else workflow)],
        )

    def test_the_current_shape_passes(self):
        self.assertEqual([], self._violations())

    def test_compile_sdk_moved_alone_is_reported_at_every_site(self):
        found = self._violations(compile_sdks={"app/build.gradle.kts": 37})
        self.assertEqual(3, len(found), found)
        for where in ("hook", "setup", "regen"):
            self.assertTrue(any(m.startswith(where + " ") for m in found), found)
        self.assertIn("platforms;android-37[.M]", found[0])

    def test_platform_minor_satisfies_the_major(self):
        hook = ["platforms;android-37.0", "platforms;android-35", "platform-tools"]
        workflow = ["platforms;android-37.0", "platforms;android-35", "platform-tools"]
        found = self._violations(
            compile_sdks={"app/build.gradle.kts": 37, "e2e-mock-camera/build.gradle.kts": 35},
            hook=hook,
            workflow=workflow,
        )
        self.assertEqual([], found)

    def test_bare_major_does_not_satisfy_a_different_level(self):
        found = self._violations(compile_sdks={"app/build.gradle.kts": 36})
        self.assertEqual(3, len(found), found)

    def test_every_module_level_is_needed(self):
        hook = ["platforms;android-37.0", "platform-tools"]
        found = self._violations(
            compile_sdks={"app/build.gradle.kts": 37, "e2e-mock-camera/build.gradle.kts": 35},
            hook=hook,
            workflow=["platforms;android-37.0"],
        )
        self.assertEqual(3, len(found), found)
        self.assertIn("platforms;android-35[.M]", found[0])
        self.assertIn("e2e-mock-camera/build.gradle.kts = 35", found[0])

    def test_workflow_left_behind_is_reported(self):
        # The #986 move done everywhere but one workflow.
        hook = ["platforms;android-37.0", "build-tools;36.0.0", "platform-tools"]
        found = self._violations(compile_sdks={"app/build.gradle.kts": 37}, hook=hook)
        self.assertEqual(2, len(found), found)
        self.assertTrue(all(m.startswith("regen ") for m in found), found)
        self.assertIn("installs platforms;android-35, which", found[1])

    def test_hook_left_behind_is_reported(self):
        workflow = ["build-tools;36.0.0", "platforms;android-37.0", "platform-tools"]
        found = self._violations(compile_sdks={"app/build.gradle.kts": 37}, workflow=workflow)
        self.assertEqual(3, len(found), found)
        self.assertIn("installs platforms;android-37.0, which", found[2])

    def test_setup_list_without_a_platform_is_reported(self):
        found = self._violations(setup=["platform-tools"])
        self.assertEqual(1, len(found), found)
        self.assertTrue(found[0].startswith("setup "), found)

    def test_build_tools_drift_is_reported(self):
        found = self._violations(
            workflow=["build-tools;36.0.1", "platforms;android-35", "platform-tools"]
        )
        self.assertEqual(1, len(found), found)
        self.assertIn("build-tools;36.0.1", found[0])

    def test_a_subset_of_the_hook_passes(self):
        self.assertEqual([], self._violations(workflow=["platforms;android-35"]))

    def test_an_install_naming_no_platform_is_not_held_to_compile_sdk(self):
        found = self._violations(
            workflow=["emulator", "system-images;android-35;google_apis;x86_64", "platform-tools"]
        )
        self.assertEqual([], found)

    def test_an_unsatisfying_platform_makes_it_an_install_site(self):
        found = self._violations(workflow=["platforms;android-34"])
        self.assertEqual(2, len(found), found)

    def test_families_the_hook_does_not_provision_are_outside_the_relation(self):
        self.assertEqual([], self._violations(workflow=["platforms;android-35", "emulator"]))


if __name__ == "__main__":
    unittest.main()
