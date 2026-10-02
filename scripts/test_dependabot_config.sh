#!/usr/bin/env bash
# test_dependabot_config.sh: guard tests for .github/dependabot.yml (issue #897).
#
# The failure this exists to prevent is silent. Dependabot's Gradle file
# fetcher never walks up out of the configured `directory`, so with
# directory "/app" the only manifest it reads is app/build.gradle.kts, which
# declares no `repositories` block and cannot (settings.gradle.kts sets
# `RepositoriesMode.FAIL_ON_PROJECT_REPOS`, and its
# `dependencyResolutionManagement { repositories { google() } }` is outside
# that directory). Finding no repository, Dependabot falls back to Maven
# Central alone, where every `androidx.*` and `com.google.android.material`
# coordinate 404s. The job then logs "No update possible", reports success,
# and opens nothing: no pull request, no warning, no failed check. That is
# how #834's app-runtime coverage sat inert from 2026-08-10 to 2026-08-17
# with twelve Google-hosted coordinates unqueried and eleven of them stale.
#
# The `registries` entry re-supplies Google's Maven repository, and these
# checks assert it stays wired up: declared, referenced, pointed at a URL
# Dependabot actually resolves against, and additive rather than replacing
# Maven Central (which still serves junit, mockito, robolectric,
# kotlinx-coroutines-test, org.json and subsampling-scale-image-view).
#
# The URL check is an exact match, not a hostname match, because Dependabot
# does not treat every URL under a Google host alike: it routes a lookup
# through its group-index.xml handling only when the URL is exactly
# `https://maven.google.com`, its own constant for a `google()` declaration
# (gradle/package/package_details_fetcher.rb compares by string equality).
# `https://dl.google.com/dl/android/maven2`, where that host redirects,
# resolves through the ordinary maven-metadata.xml path and is accepted too.
# A plausible-looking hybrid such as `https://maven.google.com/dl/android/maven2`
# has a Google hostname and serves 404 for both, which is why hostname alone
# is not the test.
#
# The second half of the file, the cooldown checks, guards a failure with the
# same signature and a different cause (issue #905). GitHub has applied a
# three-day cooldown by default since 2026-07-14, with no `cooldown` key
# needed. On https://maven.google.com Dependabot lists versions from
# group-index.xml, which carries version numbers and no dates, and reads a
# date only for the one version named by `<latest>` in maven-metadata.xml
# (dependabot-core's `ReleaseDateExtractor`, which pairs
# `//metadata/versioning/lastUpdated` with `//metadata/versioning/latest`, in
# gradle/lib/dependabot/gradle/package/release_date_extractor.rb:146).
# Every other candidate is undated, and cooldown filters an undated release
# out, so a Google-hosted coordinate whose newest published version is a
# prerelease has its whole candidate set emptied: the run succeeds, opens
# nothing, and says nothing. androidx.lifecycle sat at 2.8.7 against a
# published 2.11.0 this way, because 2.12.0-alpha01 held `<latest>`, and
# AndroidX publishes alphas continuously, so which coordinates this hides
# moves around over time. The same applies to any coordinate an `ignore` rule
# caps to a version line, since a capped target is never `<latest>`.
#
# The fix is a `cooldown` block that excludes the Google-hosted coordinates
# and keeps the delay for the Central-hosted ones, where dates resolve
# correctly. These checks assert both halves of that against the coordinates
# actually declared in each entry's Gradle manifest, rather than against a
# list duplicated here. So adding a Google-hosted dependency that the patterns
# do not cover fails a check instead of going quiet: this file's, when the
# prefix model in scripts/ci/gradle_coordinates.py classes it Google-hosted,
# and otherwise scripts/ci/test_gradle_coordinates.py's, which asks
# https://maven.google.com and fails on a coordinate the model classes wrongly
# (issue #914).
#
# The third family, the grouping and pull request limit checks, guards the
# starvation #872 fixed (issue #873). Dependabot proposes nothing once an
# entry's open pull requests reach `open-pull-requests-limit`, and says so
# only in a run log nobody reads: the default of 5 was fully consumed by
# test-only bumps (#845 through #849), so no app-runtime bump, the coverage
# .github/dependabot.yml exists for, could be proposed until one of those
# closed. #872's two keys answer that (the `test-dependencies` group
# collapses every test-only bump into one pull request, and the limit rose to
# 10), and neither key had a guard.
#
# The check is that the limit cannot be what stops a bump from being proposed:
# it counts the pull requests this entry's own coordinates can want open at
# once (one per group that takes anything, plus one per coordinate no group
# takes) and requires the limit to stay above that count. Against the old
# default of 5 it fails, which is the starvation.
#
# It asks for a margin rather than for bare coverage (issue #937). A limit equal
# to the count covers exactly what today's manifest can want and starves the
# next ungrouped coordinate added to it, so a check that is pass/fail at
# coverage goes red only once that coordinate is in the manifest, which is the
# state #871 reported after the fact. Failing at parity moves the report onto
# the file that still works and has nothing left over, one step before the
# coordinate that would starve it. Today that is 9 streams against a limit of
# 10, one slot clear.
#
# The margin is one slot and the report is a failure, with nothing between
# them. A warning band above the failure was built and then removed, and the
# reason is not that a non-fatal report cannot be delivered from here.
#
# Printing one is certainly not enough: this script reports through stdout,
# build.yml's shell-tests job reads the exit status alone, and nothing collects
# the rest, so a report that does not fail the job arrives in a log nobody
# reads, which is the delivery this file's own header condemns in the paragraph
# motivating the check. The band answered that rather than accepting it, by
# re-emitting each warning as a ::warning annotation under GitHub Actions, and
# issue #948 confirmed on three live runs that GitHub renders one in the run
# summary at annotation_level warning, carrying the line's own text, on a job
# that still concluded success.
#
# It was removed anyway, to keep this script to a single report channel. So a
# non-fatal report from here is deliverable and #948 records how; what it costs
# is a second channel to maintain and to read.
#
# Grouping is checked in both directions. Every test-only coordinate belongs
# to some group, so a run of test-only bumps stays one pull request and one
# gradle/verification-metadata.xml regeneration. And no group takes a
# coordinate that ships in the APK, which is what keeps each app-runtime
# dependency in a pull request and a review of its own, as
# .github/dependabot.yml says it is. androidx.compose:compose-bom is the
# coordinate that makes the second check bite: it is declared under both
# `androidTestImplementation` and the shipping `implementation` block, so a
# pattern reaching it would fold a shipping dependency into the test group.
#
# That second check is deliberately stronger than "no group mixes the two
# kinds", because a group of nothing but shipping coordinates would weaken
# the limit check rather than the review: grouping this entry's eight
# app-runtime coordinates drops the count the limit must cover from 9 to 2,
# so a limit that starves the file would pass.
#
# Membership is modelled from `patterns` and `exclude-patterns` alone.
# Crediting a group with more than it takes is the unsafe direction for both
# the test-only check and the limit check, so a group that narrows by
# `dependency-type`, which nothing here can see, is reported as unmodellable
# rather than approximated. A group scoped to `applies-to: security-updates`
# takes no version update and is modelled as taking nothing.
#
# The fourth family, the github-actions checks, covers the entry that watches
# the action pins (issue #1135). Three properties, none of them readable from
# the file's shape alone: every action the workflows use is taken by some
# group, the group count stays inside the one-to-five band #1135 settled on,
# and the entry's pull request limit covers one per group. Ungrouped,
# Dependabot opens a pull request per action; a limit under the group count
# starves one of them permanently.
#
# The action list is read out of the workflows, so an action no pattern reaches
# fails a check instead of quietly getting a pull request of its own.
#
# Both names Dependabot can give a reference are checked, `owner/repo` and
# `owner/repo/path`, because which one it gets follows from how it is pinned: a
# pattern taking one name and not the other would move an action between pull
# requests as its pin style changed.
#
# The fifth family, the uv checks, covers the entry that updates the Python
# locks under scripts/ (issue #1191). The locks are found and parsed by
# scripts/ci/audit_requirements.py, the same way pip-audit's gate finds them,
# so a lock that gates an audit and that no entry reaches fails here. Each lock
# needs its `.in` beside it under the same basename and a header recording
# `--universal` and `--python-version`, since that is how Dependabot's uv
# updater finds what to recompile and how it reproduces the lock as it was
# made. No `pip` entry may reach them, because pip-compile would re-resolve
# them for one interpreter. The entry must allow indirect dependencies, or the
# lock-only packages, where #1189's pyjwt advisory was, are never proposed.
# Every locked package must be grouped, no group may take both a `.in` pin and
# a package only the locks name, and the limit must cover the groups.
# The updater pins with `-P NAME==VERSION`, which uv applies to every fork of a
# universal resolution, so a package a lock pins at two versions under
# complementary markers (rpds-py was, until the locks' Python floor rose to 3.11
# in issue #1196) cannot be moved; the entry's name-only `ignore` rules must name
# exactly those packages, and none is needed while no lock pins one twice.
# The same pin cannot move a package past a cap in semgrep's own requirements
# (issue #1195), and pydantic-core's newer releases resolve only by moving
# pydantic to a pre-release (PR #1203), so those packages are ignored by version
# instead: each rule names a package one lock pins (`ignore` applies to every
# lock in the entry, so a package two locks pin is refused) and a closed range
# of versions, so a forgotten rule expires once the package publishes a version
# above it. A range must sit above every version its lock pins, since it exists
# to hide releases the lock has not reached; a lock that reaches one leaves it
# stale, and the check names it.
# And every locked package must have an entry Dependabot's uv parser keeps,
# since it drops one whose marker contains "<" without the substring
# `python_version`.
# scripts/test_dependabot_config_uv.sh breaks each config-side property in turn
# and checks the matching check fails.
#
# What this cannot check is whether GitHub's Dependabot service accepts the
# file and whether a run actually opens pull requests, or honors the raised
# limit when a sixth pull request is wanted. Only a live run on the default
# branch shows that; it starts within about three minutes of any change to
# this file landing.
#
# Always exits 0 on success, non-zero on failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG="${1:-$REPO_ROOT/.github/dependabot.yml}"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

echo "Checking $CONFIG"

if [ ! -f "$CONFIG" ]; then
    fail "$CONFIG exists"
    echo
    echo "test_dependabot_config.sh: $PASS passed, $FAIL failed"
    exit 1
fi
pass "$CONFIG exists"

set +e
OUTPUT="$(python3 - "$CONFIG" "$REPO_ROOT" <<'PY'
import fnmatch
import os
import re
import sys
from pathlib import Path

config_path = sys.argv[1]
repo_root = sys.argv[2]

try:
    import yaml
except ImportError:
    print("  FAIL: PyYAML is not installed (see scripts/requirements.txt); cannot check dependabot.yml")
    sys.exit(1)

# The workflow reader the guards in scripts/ci share, for the github-actions
# checks at the foot of this file: they ask what the workflows use, and a
# second walk of .github/workflows here would be a second place to fix when
# that answer changes. scripts/ci is not on the path of a script run from
# scripts/, and workflow_files imports yaml itself, so this follows the check
# above rather than sitting with the imports at the top.
sys.path.insert(0, os.path.join(repo_root, "scripts", "ci"))
from workflow_files import load_workflow, relative, workflow_paths  # noqa: E402

# The lock reader pip-audit's gate uses, for the Python lock checks: the locks
# this file asks Dependabot to cover are the ones that gate audits, found and
# parsed the same way.
from audit_requirements import discover_locks, normalize, parse_pins  # noqa: E402

# The coordinates each gradle entry's manifests declare, and which of them are
# Google-hosted, for the cooldown, grouping and limit checks. The classification
# is a prefix model that scripts/ci/test_gradle_coordinates.py holds to what
# https://maven.google.com actually serves (issue #914).
from gradle_coordinates import covered_directories, entry_declarations, is_google_hosted  # noqa: E402

results = []


def check(ok, msg):
    results.append(bool(ok))
    print(("  PASS: " if ok else "  FAIL: ") + msg)
    return ok


try:
    with open(config_path) as f:
        doc = yaml.safe_load(f)
except yaml.YAMLError as err:
    check(False, "dependabot.yml is valid YAML (%s)" % str(err).replace("\n", " "))
    sys.exit(1)

if not check(isinstance(doc, dict) and "updates" in doc, "dependabot.yml parses as a mapping with an updates key"):
    sys.exit(1)

registries = doc.get("registries") or {}
updates = doc.get("updates") or []

if not check(isinstance(registries, dict), "the top-level registries key is a mapping of name to registry"):
    sys.exit(1)
if not check(isinstance(updates, list), "the top-level updates key is a list of update entries"):
    sys.exit(1)

# The two URLs Dependabot resolves Google-hosted coordinates against. See the
# file header for why this is an exact match rather than a hostname match.
GOOGLE_MAVEN_URLS = ("https://maven.google.com", "https://dl.google.com/dl/android/maven2")


def normalized_url(raw):
    url = str(raw or "").strip().rstrip("/")
    if url and "://" not in url:
        # Dependabot assumes https:// when the protocol is omitted.
        url = "https://" + url
    return url


def is_maven_registry(registry):
    return isinstance(registry, dict) and registry.get("type") == "maven-repository"


def is_google_maven(registry):
    return is_maven_registry(registry) and normalized_url(registry.get("url")) in GOOGLE_MAVEN_URLS


def entry_directories(index, entry):
    """The directory paths an update entry covers, or None if it declares none.

    gradle_coordinates.covered_directories decides which key counts, so the
    live host check in scripts/ci reads the same directories; this reports why
    an entry names none.
    """
    ecosystem = entry.get("package-ecosystem")
    directories = covered_directories(entry)
    if "directories" in entry:
        check(directories is not None, "%s update entry %d's directories key is a list" % (ecosystem, index))
    elif directories is None:
        check(False, "%s update entry %d declares a directory or directories key" % (ecosystem, index))
    return directories


def entry_label(index, entry, directories):
    """Name one update entry in a check message, by its ecosystem and directories."""
    return "%s update entry %d (%s)" % (
        entry.get("package-ecosystem"),
        index,
        ", ".join(str(d) for d in directories),
    )


referenced = set()
gradle_entries = []

for index, entry in enumerate(updates):
    if not check(isinstance(entry, dict), "updates entry %d is a mapping" % index):
        continue

    names = entry.get("registries")
    if names is None:
        names = []
    elif not isinstance(names, list):
        check(False, "update entry %d's registries key is a list of registry names (found %r)" % (index, names))
        names = []

    # Collected for every ecosystem, not just gradle, so the "declared
    # registry is referenced" check below cannot fail on a registry that a
    # non-gradle entry legitimately uses.
    referenced.update(str(name) for name in names)

    for name in names:
        check(
            name in registries,
            "update entry %d references registry %r, which is declared under the top-level registries key"
            % (index, name),
        )

    if entry.get("package-ecosystem") == "gradle":
        directories = entry_directories(index, entry)
        if directories is not None:
            gradle_entries.append((index, entry, names, directories))

# replaces-base applies to every referenced maven-repository registry,
# whatever ecosystem references it and whatever directory that entry covers:
# Dependabot's RepositoriesFinder takes the first such credential and returns
# its url in place of Maven Central's.
for name in sorted(referenced):
    registry = registries.get(name)
    if is_maven_registry(registry):
        check(
            registry.get("replaces-base") is not True,
            "registry %r does not set replaces-base, so Maven Central still serves the coordinates that "
            "live there (junit, mockito, robolectric, kotlinx-coroutines-test, org.json, "
            "subsampling-scale-image-view)" % name,
        )

for index, entry, names, directories in gradle_entries:
    label = entry_label(index, entry, directories)

    # A root-scoped entry reads settings.gradle.kts itself, so it finds
    # google() there without a registry; anything narrower cannot.
    if all(str(d) == "/" for d in directories):
        continue

    check(
        any(is_google_maven(registries.get(name)) for name in names),
        "%s references a maven-repository registry for Google's Maven repository, without which no "
        "androidx.* or com.google.android.material coordinate is ever queried (issue #897)" % label,
    )

# Dependabot::Job::DEFAULT_COOLDOWN_DAYS. An entry with no `cooldown` block
# gets ReleaseCooldownOptions.new(default_days: 3); an entry whose block omits
# `default-days` gets to_options(default_days: 3), which substitutes it for the
# nil field. Either way the delay is three days, the default GitHub turned on
# 2026-07-14. ReleaseCooldownOptions' own constructor does default default_days
# to 0, but a `cooldown` block in this file never reaches it that way.
DEFAULT_COOLDOWN_DAYS = 3

# Dependabot's open-pull-requests-limit when an entry omits the key.
DEFAULT_OPEN_PULL_REQUESTS_LIMIT = 5

# The Gradle configurations whose dependencies never ship in an APK. Matched
# by prefix so testFixturesImplementation and the debug/release variants of
# each (testDebugImplementation, androidTestDebugImplementation) count too.
#
# `debugImplementation` is deliberately not here: it ships in the debug APK,
# and a coordinate declared there is scored as shipping, so it must keep its
# own pull request. This manifest declares two test-support artifacts that
# way (androidx.compose.ui:ui-tooling and ui-test-manifest), both versionless
# and therefore invisible to every check here. A versioned debug-only tool
# (leakcanary is the usual one) would be scored as shipping and would fail
# the no-shipping-coordinate-in-a-group check if a pattern reached it. That
# is the loud direction, and the fix then is a decision recorded here, not a
# pattern quietly absorbing it.
TEST_CONFIGURATION_PREFIXES = ("test", "androidTest")

def ships_in_the_apk(configurations):
    """Whether any of the configurations declaring a coordinate is a shipping one."""
    return any(not c.startswith(TEST_CONFIGURATION_PREFIXES) for c in configurations)


def group_model_error(definition):
    """Why this group's membership cannot be modelled from patterns alone, or "".

    Over-counting what a group takes is the unsafe direction for two of the
    three checks that consume this model, so a definition that cannot be read
    from `patterns` and `exclude-patterns` alone is rejected here rather than
    approximated:

    - the every-test-only-coordinate-is-grouped check passes when a coordinate
      is scored as grouped, so over-counting hides a coordinate Dependabot
      would leave in its own pull request;
    - the limit check counts one stream per coordinate no group takes, so
      over-counting lowers the stream count and lets a limit through that
      cannot in fact cover what the entry wants open.

    `dependency-type` is the key that does it: it narrows a group to a subset
    of what its patterns select, which nothing here can see.

    `applies-to` is not an error. It defaults to `version-updates`, and a
    group scoped to `security-updates` groups no version update at all, so it
    is modelled as taking nothing (see group_members). That is the safe
    direction for all three checks.
    """
    if not isinstance(definition, dict):
        return "is not a mapping"
    for key in ("patterns", "exclude-patterns"):
        if key in definition and not isinstance(definition[key], list):
            return "declares %s as %r rather than a list of patterns" % (key, definition[key])
    if "dependency-type" in definition:
        return "narrows by dependency-type, which selects a subset of its patterns that this check cannot see"
    return ""


def group_members(coordinates, definition):
    """The coordinates a group definition takes, for version updates.

    Mirrors Dependabot's grouping: absent `patterns` selects everything, and
    `exclude-patterns` subtracts from whatever `patterns` selected. Call only
    on a definition group_model_error accepts.
    """
    if str(definition.get("applies-to", "version-updates")) != "version-updates":
        return set()
    patterns = definition.get("patterns")
    if not isinstance(patterns, list):
        patterns = ["*"]
    excluded = definition.get("exclude-patterns") or []

    def selects(coordinate, pattern_list):
        return any(fnmatch.fnmatchcase(coordinate, str(p)) for p in pattern_list)

    return {c for c in coordinates if selects(c, patterns) and not selects(c, excluded)}


def cooldown_holds(coordinate, cooldown):
    """Whether cooldown delays a proposed update to this coordinate.

    Mirrors ReleaseCooldownOptions: a coordinate is in cooldown when the
    include list is empty or matches it and the exclude list does not, and
    when the delay is a positive number of days. The semver-specific keys are
    not consulted; each falls back to default-days when unset, so a positive
    default-days is what keeps the delay in force for a coordinate that has
    no per-semver override.

    An absent default-days is read as DEFAULT_COOLDOWN_DAYS, not as zero,
    because that is what dependabot-core substitutes. Reading it as zero would
    model an exclude-only block as cooldown-off and report every Central-hosted
    coordinate as wrongly exempted, a failure that does not happen.
    """
    if not isinstance(cooldown, dict):
        return False

    def matches(key):
        patterns = cooldown.get(key) or []
        if not isinstance(patterns, list):
            return False
        return any(fnmatch.fnmatchcase(coordinate, str(pattern)) for pattern in patterns)

    days = cooldown.get("default-days")
    if days is None:
        days = DEFAULT_COOLDOWN_DAYS
    if not isinstance(days, int) or isinstance(days, bool) or days <= 0:
        return False
    if (cooldown.get("include") or []) and not matches("include"):
        return False
    return not matches("exclude")


# One walk of each entry's manifests, consumed by every per-entry check below,
# and made by gradle_coordinates.entry_declarations so that the live host check
# in scripts/ci walks the same manifests.
#
# That walk does not descend into Gradle subprojects, so a "/"-scoped entry
# finds no Google-hosted coordinate and falls through the cooldown branch below
# unchecked. Today's entry is scoped to /app, so that path is unreached, but a
# "/" entry would hit issue #905 identically and would need the walk widened
# rather than trusted.
declarations = {}
for index, entry, names, directories in gradle_entries:
    label = entry_label(index, entry, directories)
    declared, manifests = entry_declarations(repo_root, directories)
    for directory, paths in manifests:
        check(bool(paths), "%s covers a directory containing a Gradle manifest (%s)" % (label, directory))
    declarations[index] = declared


for index, entry, names, directories in gradle_entries:
    label = entry_label(index, entry, directories)

    google_hosted = set()
    central_hosted = set()
    for coordinate in declarations[index]:
        (google_hosted if is_google_hosted(coordinate) else central_hosted).add(coordinate)

    # An entry declaring no Google-hosted coordinate cannot hit the bug, so it
    # is under no obligation to configure cooldown at all.
    if not google_hosted:
        continue

    cooldown = entry.get("cooldown")
    if not check(
        isinstance(cooldown, dict),
        "%s declares a cooldown block, without which GitHub's three-day default applies and silently hides "
        "every Google-hosted bump whose newest published version is a prerelease (issue #905)" % label,
    ):
        continue

    check(
        "default-days" in cooldown,
        "%s's cooldown sets default-days explicitly, pinning the delay here rather than inheriting GitHub's "
        "default (3 days today), so a change to that default cannot move this repo's cooldown silently "
        "(issue #905)" % label,
    )

    still_held = sorted(c for c in google_hosted if cooldown_holds(c, cooldown))
    check(
        not still_held,
        "%s's cooldown exempts all %d of its Google-hosted coordinates, for which Dependabot resolves a "
        "release date only for the version named by `<latest>`%s (issue #905)"
        % (label, len(google_hosted), ("; still held: " + ", ".join(still_held)) if still_held else ""),
    )

    exempted = sorted(c for c in central_hosted if not cooldown_holds(c, cooldown))
    check(
        not exempted,
        "%s's cooldown still holds all %d of its Central-hosted coordinates, where release dates resolve and "
        "the delay does real work%s (issue #905)"
        % (label, len(central_hosted), ("; wrongly exempted: " + ", ".join(exempted)) if exempted else ""),
    )

# Grouping and the pull request limit (issue #873). See the file header.
for index, entry, names, directories in gradle_entries:
    label = entry_label(index, entry, directories)

    declared = declarations[index]

    # An entry whose manifests declare no versioned coordinate has nothing to
    # group and nothing to spend a pull request slot on. The walk above
    # already reports a directory with no manifest at all.
    if not declared:
        continue

    groups = entry.get("groups") or {}
    if not check(isinstance(groups, dict), "%s's groups key is a mapping of group name to definition" % label):
        continue

    # A group this model cannot read is reported and then modelled as taking
    # nothing, which is the direction that fails loudly: its coordinates count
    # as ungrouped, so the two checks below tighten rather than relax.
    grouped = {}
    for name in sorted(groups):
        error = group_model_error(groups[name])
        check(
            not error,
            "%s's %r group selects its members by patterns alone, which is what the checks below model%s "
            "(issue #873)" % (label, name, ("; it " + error) if error else ""),
        )
        grouped[name] = set() if error else group_members(declared, groups[name])

    test_only = sorted(c for c in declared if not ships_in_the_apk(declared[c]))
    ungrouped_test_only = [c for c in test_only if not any(c in members for members in grouped.values())]
    check(
        not ungrouped_test_only,
        "%s groups all %d of its test-only coordinates, so a run of test-only bumps costs one "
        "gradle/verification-metadata.xml regeneration instead of one per package%s (issue #873)"
        % (label, len(test_only), ("; ungrouped: " + ", ".join(ungrouped_test_only)) if ungrouped_test_only else ""),
    )

    # No group may take a shipping coordinate at all, which is stronger than
    # forbidding a mixture of the two kinds and is stronger for two reasons.
    # A shipping dependency in any group loses the review of its own that
    # .github/dependabot.yml promises it. And a group of shipping coordinates
    # collapses them into one stream, which lowers the count the limit check
    # below has to cover: grouping this entry's eight app-runtime coordinates
    # takes it from 9 to 2, so the limit check would pass on a limit that
    # starves the ungrouped file this guard is for.
    for name in sorted(grouped):
        shipping = sorted(c for c in grouped[name] if ships_in_the_apk(declared[c]))
        check(
            not shipping,
            "%s's %r group takes only test-only coordinates, so every shipping dependency keeps its own pull "
            "request and review%s (issue #873)"
            % (label, name, ("; also taken: " + ", ".join(shipping)) if shipping else ""),
        )

    # What the entry can want open at once: one pull request per group that
    # takes anything, plus one for each coordinate no group takes.
    group_streams = len([name for name in grouped if grouped[name]])
    ungrouped = [c for c in declared if not any(c in members for members in grouped.values())]
    streams = group_streams + len(ungrouped)
    limit = entry.get("open-pull-requests-limit", DEFAULT_OPEN_PULL_REQUESTS_LIMIT)
    if check(
        isinstance(limit, int) and not isinstance(limit, bool),
        "%s's open-pull-requests-limit is a number (found %r)" % (label, limit),
    ):
        # Strictly above, not merely covering (issue #937): a limit equal to
        # the count covers exactly what today's manifest can want and starves
        # the next ungrouped coordinate added to it.
        #
        # Each failing side appends why it failed and the limit that fixes it,
        # as the grouping checks above append the members they object to. The
        # person who trips the parity failure is adding a dependency to
        # app/build.gradle.kts against a configuration that works, and the
        # claim alone would tell them nothing they can act on.
        composition = "%d ungrouped coordinate(s) plus %d non-empty group(s)" % (len(ungrouped), group_streams)
        if limit < streams:
            detail = "; %d of them cannot be proposed at all, which is the starvation #871 reported" % (
                streams - limit,
            )
        elif limit == streams:
            detail = "; it covers exactly what today's manifest can want, so the next ungrouped coordinate added is starved"
        else:
            detail = ""
        if detail:
            detail += ", and raising the limit to %d or more is what fixes it" % (streams + 1)
        check(
            limit > streams,
            "%s's open-pull-requests-limit of %d is above the %d pull requests its coordinates can want open at "
            "once (%s), leaving a slot for the next coordinate added rather than starving it%s "
            "(issues #873, #937)" % (label, limit, streams, composition, detail),
        )

# GitHub Actions pins (issue #1135). See the file header.

# The band #1135 settled on: at least one group, because ungrouped is a pull
# request per action, and at most five. This five is that decision's and
# DEFAULT_OPEN_PULL_REQUESTS_LIMIT is GitHub's; the limit check below reads one
# against the other rather than either being derived from the other.
GITHUB_ACTIONS_MAX_GROUPS = 5


def workflow_action_references():
    """Every GitHub-hosted action the workflows use, mapped to the files using it.

    Reads `uses:` at both levels that carry one: a step, and a job calling a
    reusable workflow.

    A local action (`./.github/actions/...`) is this repository's own code and
    a `docker://` reference names no GitHub repository, so Dependabot's
    github-actions parser tracks neither and neither needs a group.
    """
    references = {}
    for path in workflow_paths():
        jobs = load_workflow(path).get("jobs")
        if not isinstance(jobs, dict):
            continue
        for job in jobs.values():
            if not isinstance(job, dict):
                continue
            uses_values = [job.get("uses")]
            steps = job.get("steps")
            if isinstance(steps, list):
                uses_values.extend(step.get("uses") for step in steps if isinstance(step, dict))
            for uses in uses_values:
                if not isinstance(uses, str):
                    continue
                reference = uses.split("@", 1)[0].strip()
                if not reference or reference.startswith(".") or "://" in reference:
                    continue
                references.setdefault(reference, set()).add(relative(path))
    return references


def dependency_names(reference):
    """The two names Dependabot can give one `uses:` reference by its path.

    `owner/repo` normally, and `owner/repo/path` when the reference carries a
    subpath and is pinned to a SHA or names a reusable workflow. Which one a
    reference gets therefore follows from how it is pinned, so both are
    checked rather than the pin style being read here.

    A third form is not returned: a ref that is itself path-based, such as the
    `release/v1.2.3` tag a monorepo gives one action, names the dependency
    after the whole `uses:` string (`Version.path_based?`, in dependabot-core's
    github_actions version class). Nothing here carries such a ref, and one
    that did is read below under `owner/repo`, which a prefix pattern such as
    `actions/*` matches alike and an exact-name pattern does not.
    """
    parts = reference.split("/")
    names = {"/".join(parts[:2])}
    if len(parts) > 2:
        names.add(reference)
    return names


actions_entries = [
    (index, entry)
    for index, entry in enumerate(updates)
    if isinstance(entry, dict) and entry.get("package-ecosystem") == "github-actions"
]

if check(
    bool(actions_entries),
    "an update entry covers the github-actions ecosystem, without which nothing in this repository watches an "
    "action pin for ageing (issue #1135)",
):
    references = workflow_action_references()

    # Every check below passes vacuously on a repository whose workflows use
    # no action, so the set they read is asserted to be non-empty first.
    check(bool(references), "the workflows use at least one action for that entry to cover (issue #1135)")

    names = set()
    for reference in references:
        names |= dependency_names(reference)

    for index, entry in actions_entries:
        directories = entry_directories(index, entry)
        if directories is None:
            continue
        label = entry_label(index, entry, directories)

        check(
            any(str(directory) == "/" for directory in directories),
            '%s covers "/", the one directory Dependabot reads workflows from (it scans .github/workflows, and a '
            "root action.yml, from there alone), so a narrower directory scans no workflow at all (issue #1135)"
            % label,
        )

        groups = entry.get("groups") or {}
        if not check(isinstance(groups, dict), "%s's groups key is a mapping of group name to definition" % label):
            continue

        check(
            1 <= len(groups) <= GITHUB_ACTIONS_MAX_GROUPS,
            "%s declares between 1 and %d groups (found %d): ungrouped, Dependabot opens a pull request per "
            "action, and a group per action relocates that rather than removing it (issue #1135)"
            % (label, GITHUB_ACTIONS_MAX_GROUPS, len(groups)),
        )

        # Modelled exactly as the gradle groups above are, including reporting
        # a definition this cannot read as taking nothing: its members then
        # count as ungrouped, which fails the check below rather than passing it.
        grouped = {}
        for name in sorted(groups):
            error = group_model_error(groups[name])
            check(
                not error,
                "%s's %r group selects its members by patterns alone, which is what the checks below model%s "
                "(issue #1135)" % (label, name, ("; it " + error) if error else ""),
            )
            grouped[name] = set() if error else group_members(names, groups[name])

        taken_by = {name: frozenset(group for group in grouped if name in grouped[group]) for name in names}

        ungrouped = sorted(
            "%s (%s)" % (reference, ", ".join(sorted(references[reference])))
            for reference in references
            if not all(taken_by[name] for name in dependency_names(reference))
        )
        check(
            not ungrouped,
            "%s groups every action the workflows use, so none of them gets a pull request of its own%s "
            "(issue #1135)" % (label, ("; ungrouped: " + ", ".join(ungrouped)) if ungrouped else ""),
        )

        split = sorted(
            reference for reference in references if len({taken_by[name] for name in dependency_names(reference)}) > 1
        )
        check(
            not split,
            "%s takes each action into the same group under either name Dependabot can give it, so changing how "
            "one is pinned does not move it between pull requests%s (issue #1135)"
            % (label, ("; split: " + ", ".join(split)) if split else ""),
        )

        # One pull request per group the entry declares, plus one per action no
        # group takes. Declared, not populated: a group whose patterns match
        # nothing yet fills as soon as a workflow adds an action they reach.
        #
        # Coverage, not the strict margin the gradle entry above requires: with
        # every action grouped, the streams are the declared groups, and that
        # count rises only by an edit to the groups block, which the band check
        # above reads.
        streams = len(groups) + len(ungrouped)
        limit = entry.get("open-pull-requests-limit", DEFAULT_OPEN_PULL_REQUESTS_LIMIT)
        if check(
            isinstance(limit, int) and not isinstance(limit, bool),
            "%s's open-pull-requests-limit is a number (found %r)" % (label, limit),
        ):
            check(
                limit >= streams,
                "%s's open-pull-requests-limit of %d covers the %d pull requests its %d group(s) and %d ungrouped "
                "action(s) can want open at once%s (issues #1135, #937)"
                % (
                    label,
                    limit,
                    streams,
                    len(groups),
                    len(ungrouped),
                    (
                        "; %d of them cannot be proposed at all, and raising the limit to %d is what fixes it"
                        % (streams - limit, streams)
                    )
                    if limit < streams
                    else "",
                ),
            )

# Python locks (issue #1191). See the file header.


def scanned_directories(directory):
    """The repo-relative directories the uv file fetcher reads for one entry directory.

    The directory itself and each directory immediately below it
    (`req_txt_and_in_files` and `req_files_for_dir` in dependabot-core's
    python/lib/dependabot/python/shared_file_fetcher.rb); nothing deeper.
    """
    base = str(directory).strip("/")
    root = os.path.join(repo_root, base)
    found = {base}
    if os.path.isdir(root):
        for name in os.listdir(root):
            if os.path.isdir(os.path.join(root, name)):
                found.add(os.path.join(base, name) if base else name)
    return found


def lock_pins(path):
    with open(path) as handle:
        return {pin.name for pin in parse_pins(handle.read(), path)}


# A pin line with its environment marker, if it has one:
# `typing-extensions==4.16.0 ; python_full_version < '3.13' \`.
PIN_WITH_MARKER_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)\s*(?:;\s*(.*?))?\s*\\?\s*$")


def lock_entries(path):
    """Every `name==version` line in a lock, as (normalized name, version, marker or "")."""
    entries = []
    with open(path) as handle:
        for line in handle:
            match = PIN_WITH_MARKER_RE.match(line.rstrip("\n"))
            if match:
                entries.append((normalize(match.group(1)), match.group(2), match.group(3) or ""))
    return entries


def parser_keeps(marker):
    """Whether Dependabot's uv parser keeps an `==` pin carrying this marker.

    Mirrors `blocking_marker?` (dependabot-core uv/lib/dependabot/uv/file_parser.rb:281-295).
    A marker naming `python_version` is evaluated against the Dependabot
    runner's own interpreter, which nothing here can know, so it is scored as
    dropped. Any other marker containing "<" is dropped outright, which is what
    happens to `python_full_version < '3.13'`: that spelling does not contain
    the substring `python_version`.
    """
    if not marker:
        return True
    if "python_version" in marker:
        return False
    return "<" not in marker


# A version as far as the locks and the `ignore` ranges spell one: a release
# number, an optional pre-release (`0.58b0`), an optional `.postN`. Anything
# else is not ordered here, and a check that meets one says so.
VERSION_RE = re.compile(r"^(\d+(?:\.\d+)*)(?:(a|b|rc)(\d+))?(?:\.post(\d+))?$")
PRE_RELEASE_RANK = {"a": -3, "b": -2, "rc": -1}


def version_key(text):
    """Order a version as PEP 440 does, or return None when it is spelled some other way."""
    match = VERSION_RE.match(text)
    if not match:
        return None
    release = [int(part) for part in match.group(1).split(".")]
    while len(release) > 1 and release[-1] == 0:
        release.pop()
    return (tuple(release), PRE_RELEASE_RANK.get(match.group(2), 0), int(match.group(3) or 0), int(match.group(4) or 0))


# One bound of a range: `>=2.14`, `<2.17`.
BOUND_RE = re.compile(r"^(>=|>|<=|<)\s*(\S+)$")


def closed_range(text):
    """Split `>=2.14, <2.17` into ((op, version), (op, version)), or return None.

    A closed range has exactly one lower bound and one upper bound, comma
    separated, the form Dependabot's uv requirement parser reads. Anything else
    (an open end, an `==`, a `~=`, a bound it cannot order) is not one.
    """
    if not isinstance(text, str):
        return None
    clauses = [clause.strip() for clause in text.split(",")]
    if len(clauses) != 2:
        return None
    bounds = [BOUND_RE.match(clause) for clause in clauses]
    if not all(bounds):
        return None
    lower, upper = ((m.group(1), m.group(2)) for m in bounds)
    if lower[0] not in (">", ">=") or upper[0] not in ("<", "<="):
        return None
    if version_key(lower[1]) is None or version_key(upper[1]) is None:
        return None
    if version_key(lower[1]) >= version_key(upper[1]):
        return None
    return lower, upper


def satisfies_bound(version, bound):
    """Whether `version` satisfies one (op, version) bound; both must be orderable."""
    op, limit = bound
    have, want = version_key(version), version_key(limit)
    return {">=": have >= want, ">": have > want, "<=": have <= want, "<": have < want}[op]


# The flags the uv updater reads back out of a lock to regenerate it as it was
# made (uv_compile_options_from_compiled_file in dependabot-core's
# uv/lib/dependabot/uv/file_updater/compile_file_updater.rb). A lock missing
# one is regenerated without it.
UV_HEADER_FLAGS = ("--universal", "--python-version")

locks = [os.path.relpath(str(path), repo_root) for path in discover_locks(Path(repo_root))]
check(bool(locks), "scripts/ holds at least one Python lock for the uv entry to cover (issue #1191)")

pip_entries = [
    (index, entry)
    for index, entry in enumerate(updates)
    if isinstance(entry, dict) and entry.get("package-ecosystem") == "pip"
]
for index, entry in pip_entries:
    directories = entry_directories(index, entry) or []
    reached = set()
    for directory in directories:
        reached |= scanned_directories(directory)
    claimed = sorted(lock for lock in locks if os.path.dirname(lock) in reached)
    check(
        not claimed,
        "%s covers none of the uv-compiled locks, which its pip-compile would re-resolve for the Dependabot "
        "runner's interpreter alone, dropping their marker-gated entries%s (issue #1191)"
        % (entry_label(index, entry, directories), ("; it reaches " + ", ".join(claimed)) if claimed else ""),
    )

uv_entries = [
    (index, entry)
    for index, entry in enumerate(updates)
    if isinstance(entry, dict) and entry.get("package-ecosystem") == "uv"
]

if check(
    bool(uv_entries),
    "an update entry covers the uv ecosystem, without which nothing proposes an update to the Python locks and "
    "an advisory surfaces only as a red pip-audit on an unrelated pull request (issue #1191)",
):
    reached_by = {}
    for index, entry in uv_entries:
        directories = entry_directories(index, entry) or []
        for directory in directories:
            for scanned in scanned_directories(directory):
                reached_by.setdefault(scanned, []).append(index)

    unreached = sorted(lock for lock in locks if os.path.dirname(lock) not in reached_by)
    check(
        not unreached,
        "every lock pip-audit gates on sits in a directory a uv entry names or directly below one, the depth "
        "Dependabot's fetcher reads%s (issue #1191)" % (("; unreached: " + ", ".join(unreached)) if unreached else ""),
    )

    for lock in locks:
        source = lock[: -len(".txt")] + ".in"
        if not check(
            os.path.isfile(os.path.join(repo_root, source)),
            "%s has its input beside it as %s, the basename pairing Dependabot uses to find what to recompile "
            "(issue #1191)" % (lock, source),
        ):
            continue
        with open(os.path.join(repo_root, lock)) as handle:
            header = "".join(line for line in handle if line.startswith("#"))
        missing = [flag for flag in UV_HEADER_FLAGS if flag not in header]
        check(
            not missing,
            "%s's header records %s, which the uv updater reads back to regenerate it as it was made%s "
            "(issue #1191)"
            % (lock, " and ".join(UV_HEADER_FLAGS), ("; missing: " + ", ".join(missing)) if missing else ""),
        )

    for index, entry in uv_entries:
        directories = entry_directories(index, entry)
        if directories is None:
            continue
        label = entry_label(index, entry, directories)
        reached = set()
        for directory in directories:
            reached |= scanned_directories(directory)
        covered = [lock for lock in locks if os.path.dirname(lock) in reached]

        # Every pin in a covered `.in` is direct; every other locked package
        # is indirect, which is how the uv parser reads a compiled lock
        # (file_parser.rb gives a compiled file's entries no requirements).
        direct = set()
        locked = set()
        for lock in covered:
            source = os.path.join(repo_root, lock[: -len(".txt")] + ".in")
            if os.path.isfile(source):
                direct |= lock_pins(source)
            locked |= lock_pins(os.path.join(repo_root, lock))
        transitive = locked - direct

        allow = entry.get("allow") or []
        check(
            isinstance(allow, list)
            and any(isinstance(rule, dict) and rule == {"dependency-type": "all"} for rule in allow),
            "%s allows dependency-type all, without which a version update skips every package the `.in` files do "
            "not pin, pyjwt among them (issues #1189, #1191)" % label,
        )

        # A package a lock pins at more than one version (under complementary
        # markers) cannot be moved: the updater pins it with -P NAME==VERSION,
        # which uv applies to every fork of the universal resolution, so the
        # fork that needs the other version has no solution. Those are named
        # in name-only `ignore` rules, and only those, so the gap is stated in
        # the config and a rule goes when its fork does.
        forked = {}
        visible = set()
        for lock in covered:
            versions = {}
            for name, version, marker in lock_entries(os.path.join(repo_root, lock)):
                versions.setdefault(name, set()).add(version)
                if parser_keeps(marker):
                    visible.add(name)
            for name, pinned in versions.items():
                if len(pinned) > 1:
                    forked.setdefault(name, []).append(lock)

        ignore = entry.get("ignore") or []
        ignored = {
            normalize(str(rule["dependency-name"]))
            for rule in ignore
            if isinstance(rule, dict) and set(rule) == {"dependency-name"}
        }
        unignored = sorted("%s (%s)" % (name, ", ".join(forked[name])) for name in forked if name not in ignored)
        check(
            not unignored,
            "%s ignores by name every package a lock pins at more than one version, which -P NAME==VERSION cannot "
            "reproduce, so the gap is written in the config rather than logged as a weekly error%s (issue #1191)"
            % (label, ("; not ignored: " + ", ".join(unignored)) if unignored else ""),
        )
        stale = sorted(ignored - set(forked))
        check(
            not stale,
            "%s ignores by name only packages some lock pins at more than one version, so a rule goes once its "
            "fork does%s (issue #1191)" % (label, ("; stale: " + ", ".join(stale)) if stale else ""),
        )

        # The rest of `ignore`: a package named with a closed range of versions
        # (issue #1195). The updater's per-package resolution cannot move a
        # package past a cap in semgrep's own requirements, and the range hides
        # exactly the releases that fail so the run log does not record an update
        # error for each; pydantic-core's range hides the releases only a pydantic
        # pre-release asks for (PR #1203). Closed, so a rule nobody revisits stops
        # hiding anything once the package publishes a version above it.
        ranged = [
            rule
            for rule in ignore
            if isinstance(rule, dict) and set(rule) == {"dependency-name", "versions"}
        ]
        other_rules = [
            rule
            for rule in ignore
            if not isinstance(rule, dict)
            or set(rule) not in ({"dependency-name"}, {"dependency-name", "versions"})
        ]
        check(
            not other_rules,
            "%s writes each `ignore` rule as a package name alone or as a package name with `versions`, the two "
            "shapes the checks here model%s (issue #1195)"
            % (label, ("; found: " + ", ".join(repr(rule) for rule in other_rules)) if other_rules else ""),
        )

        locked_versions = {}
        for lock in covered:
            for name, version, _marker in lock_entries(os.path.join(repo_root, lock)):
                locked_versions.setdefault(name, set()).add(version)

        unlocked = sorted(
            normalize(str(rule["dependency-name"]))
            for rule in ranged
            if normalize(str(rule["dependency-name"])) not in locked_versions
        )
        check(
            not unlocked,
            "%s gives a range of versions only for packages a lock pins, so a rule goes once its package does%s "
            "(issue #1195)" % (label, ("; not locked: " + ", ".join(unlocked)) if unlocked else ""),
        )

        pinned_by = {}
        for lock in covered:
            for name in {name for name, _version, _marker in lock_entries(os.path.join(repo_root, lock))}:
                pinned_by.setdefault(name, []).append(lock)
        shared = sorted(
            "%s (%s)" % (normalize(str(rule["dependency-name"])), ", ".join(pinned_by[normalize(str(rule["dependency-name"]))]))
            for rule in ranged
            if len(pinned_by.get(normalize(str(rule["dependency-name"])), ())) > 1
        )
        check(
            not shared,
            "%s gives a range of versions only for packages one lock pins, since `ignore` applies to every lock "
            "in the entry and each range is sized against the one lock whose resolution it concerns%s (issue #1195)"
            % (label, ("; pinned by more than one lock: " + ", ".join(shared)) if shared else ""),
        )

        open_ended = []
        below_lock = []
        for rule in ranged:
            name = normalize(str(rule["dependency-name"]))
            versions = rule["versions"]
            if not isinstance(versions, list) or not versions:
                open_ended.append("%s (versions is not a list of ranges)" % name)
                continue
            for text in versions:
                parsed = closed_range(text)
                if parsed is None:
                    open_ended.append("%s %r" % (name, text))
                    continue
                for version in sorted(locked_versions.get(name, ())):
                    if version_key(version) is None:
                        below_lock.append("%s (locked %s cannot be ordered)" % (name, version))
                    elif satisfies_bound(version, parsed[0]):
                        below_lock.append("%s %s (locked %s)" % (name, text, version))
        check(
            not open_ended,
            "%s closes every range of ignored versions, one lower bound and one upper bound (for example "
            "'>=2.14, <2.17'), so a rule nobody revisits expires once the package publishes a version above it "
            "instead of hiding its updates for good%s (issue #1195)"
            % (label, ("; not closed: " + ", ".join(open_ended)) if open_ended else ""),
        )
        check(
            not below_lock,
            "%s starts every range above each version its lock pins, so it hides only releases the lock has not "
            "reached and a range the lock has caught up with is named as stale%s (issue #1195)"
            % (label, ("; at or below the lock: " + ", ".join(below_lock)) if below_lock else ""),
        )

        invisible = sorted(locked - visible - ignored)
        check(
            not invisible,
            "%s can see every locked package: each has at least one entry Dependabot's uv parser keeps, which "
            "drops an entry whose marker contains '<'%s (issue #1191)"
            % (label, ("; unseen: " + ", ".join(invisible)) if invisible else ""),
        )

        groups = entry.get("groups") or {}
        if not check(isinstance(groups, dict), "%s's groups key is a mapping of group name to definition" % label):
            continue

        grouped = {}
        for name in sorted(groups):
            error = group_model_error(groups[name])
            check(
                not error,
                "%s's %r group selects its members by patterns alone, which is what the checks below model%s "
                "(issue #1191)" % (label, name, ("; it " + error) if error else ""),
            )
            grouped[name] = set() if error else group_members(locked, groups[name])

        # Patterns are compared in normalized form on both sides, the form the
        # uv parser gives a dependency's name.
        for name in sorted(groups):
            definition = groups[name] if isinstance(groups[name], dict) else {}
            for key in ("patterns", "exclude-patterns"):
                unnormalized = sorted(str(p) for p in definition.get(key) or [] if normalize(str(p)) != str(p))
                check(
                    not unnormalized,
                    "%s's %r group writes its %s PEP 503-normalized%s (issue #1191)"
                    % (label, name, key, ("; not normalized: " + ", ".join(unnormalized)) if unnormalized else ""),
                )

        ungrouped = sorted(package for package in locked if not any(package in grouped[g] for g in grouped))
        check(
            not ungrouped,
            "%s groups every locked package, so none of them gets a pull request of its own%s (issue #1191)"
            % (label, ("; ungrouped: " + ", ".join(ungrouped)) if ungrouped else ""),
        )

        mixed = sorted(g for g in grouped if grouped[g] & direct and grouped[g] & transitive)
        check(
            not mixed,
            "%s keeps the `.in` pins and the packages they pull in in separate groups, so a tool bump that needs "
            "work cannot hold a transitive security fix back%s (issue #1191)"
            % (
                label,
                "".join(
                    "; %r takes direct %s and %d transitive package(s)"
                    % (g, ", ".join(sorted(grouped[g] & direct)), len(grouped[g] & transitive))
                    for g in mixed
                ),
            ),
        )

        streams = len([g for g in grouped if grouped[g]]) + len(ungrouped)
        limit = entry.get("open-pull-requests-limit", DEFAULT_OPEN_PULL_REQUESTS_LIMIT)
        if check(
            isinstance(limit, int) and not isinstance(limit, bool),
            "%s's open-pull-requests-limit is a number (found %r)" % (label, limit),
        ):
            check(
                limit >= streams,
                "%s's open-pull-requests-limit of %d covers the %d pull requests its groups and ungrouped packages "
                "can want open at once (issue #1191)" % (label, limit, streams),
            )

for name in registries:
    check(name in referenced, "declared registry %r is referenced by an update entry" % name)

sys.exit(0 if all(results) else 1)
PY
)"
PY_STATUS=$?
set -e

echo "$OUTPUT"
PY_PASS=$(grep -c '^  PASS:' <<<"$OUTPUT" || true)
PY_FAIL=$(grep -c '^  FAIL:' <<<"$OUTPUT" || true)
PASS=$((PASS + PY_PASS))
FAIL=$((FAIL + PY_FAIL))
if [ "$PY_STATUS" -ne 0 ] && [ "$PY_FAIL" -eq 0 ]; then
    fail "dependabot.yml checks errored before reporting individual checks (exit $PY_STATUS)"
fi

echo
echo "test_dependabot_config.sh: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
