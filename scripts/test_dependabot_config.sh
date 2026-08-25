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
# date only for the one version named by `<release>` in maven-metadata.xml.
# Every other candidate is undated, and cooldown filters an undated release
# out, so a Google-hosted coordinate whose newest published version is a
# prerelease has its whole candidate set emptied: the run succeeds, opens
# nothing, and says nothing. androidx.lifecycle sat at 2.8.7 against a
# published 2.11.0 this way, because 2.12.0-alpha01 held `<release>`, and
# AndroidX publishes alphas continuously, so which coordinates this hides
# moves around over time. The same applies to any coordinate an `ignore` rule
# caps to a version line, since a capped target is never `<release>`.
#
# The fix is a `cooldown` block that excludes the Google-hosted coordinates
# and keeps the delay for the Central-hosted ones, where dates resolve
# correctly. These checks assert both halves of that against the coordinates
# actually declared in each entry's Gradle manifest, rather than against a
# list duplicated here, so adding a Google-hosted dependency that the patterns
# do not cover fails the check instead of going quiet.
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
# them. A warning band above the failure was tried and dropped: this script
# reports through stdout, build.yml's shell-tests job reads the exit status
# alone, and nothing collects the rest, so a report that does not fail the job
# arrives in a log nobody reads, which is the delivery this file's own header
# condemns in the paragraph motivating the check.
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
import glob
import os
import re
import sys

config_path = sys.argv[1]
repo_root = sys.argv[2]

try:
    import yaml
except ImportError:
    print("  FAIL: PyYAML is not installed (see scripts/requirements.txt); cannot check dependabot.yml")
    sys.exit(1)

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
    """The directory paths an update entry covers, or None if it declares none."""
    if "directories" in entry:
        directories = entry["directories"]
        if not check(isinstance(directories, list), "gradle update entry %d's directories key is a list" % index):
            return None
        return directories
    if "directory" in entry:
        return [entry["directory"]]
    check(False, "gradle update entry %d declares a directory or directories key" % index)
    return None


def entry_label(index, directories):
    return "gradle update entry %d (%s)" % (index, ", ".join(str(d) for d in directories))


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
    label = entry_label(index, directories)

    # A root-scoped entry reads settings.gradle.kts itself, so it finds
    # google() there without a registry; anything narrower cannot.
    if all(str(d) == "/" for d in directories):
        continue

    check(
        any(is_google_maven(registries.get(name)) for name in names),
        "%s references a maven-repository registry for Google's Maven repository, without which no "
        "androidx.* or com.google.android.material coordinate is ever queried (issue #897)" % label,
    )

# A coordinate's host follows from its group, not from its artifact name:
# com.davemorrissey.labs:subsampling-scale-image-view-androidx is served by
# Maven Central despite ending in "androidx". That is the case a loose
# "*androidx*" exclude pattern would wrongly exempt, and the reason this
# classification keys off the group alone.
#
# These two prefixes cover what app/build.gradle.kts declares today, not every
# group maven.google.com serves. A Google-hosted group outside them
# (com.google.firebase, say) is classed Central-hosted, and the last check
# below then insists cooldown keep holding it, which is the configuration that
# hides it. Adding such a dependency means adding its prefix here as well as to
# the exclude list in .github/dependabot.yml.
GOOGLE_MAVEN_GROUP_PREFIXES = ("androidx.", "com.google.android.")

# Dependabot::Job::DEFAULT_COOLDOWN_DAYS. An entry with no `cooldown` block
# gets ReleaseCooldownOptions.new(default_days: 3); an entry whose block omits
# `default-days` gets to_options(default_days: 3), which substitutes it for the
# nil field. Either way the delay is three days, the default GitHub turned on
# 2026-07-14. ReleaseCooldownOptions' own constructor does default default_days
# to 0, but a `cooldown` block in this file never reaches it that way.
DEFAULT_COOLDOWN_DAYS = 3

# Dependabot's open-pull-requests-limit when an entry omits the key.
DEFAULT_OPEN_PULL_REQUESTS_LIMIT = 5

# One dependency declaration: the Gradle configuration it is declared in, then
# a quoted "group:artifact:version" literal. A `platform(...)` wrapper is
# unwrapped, so the Compose BOM reads as a declaration of the configuration
# around it rather than of `platform`.
#
# The group is not required to contain a dot: junit:junit has none, and
# demanding one dropped it from the Central-hosted set silently, which left an
# over-broad exclude pattern such as "junit*" free to exempt it with every
# check still passing.
#
# Versionless coordinates (androidx.compose.ui:ui and friends, whose versions
# come from the Compose BOM) are the one deliberate omission: Dependabot
# proposes no update for them, so they say nothing about whether cooldown,
# grouping or the pull request limit is configured correctly.
DECLARATION_RE = re.compile(
    r'^[ \t]*(\w+)\s*\(\s*(?:platform\s*\(\s*)?"([A-Za-z][\w.-]*):([\w.-]+):([^"\s]+)"',
    re.M,
)

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

# The top-level `dependencies { ... }` block, up to the first line that is a
# closing brace in column 0.
DEPENDENCIES_BLOCK_RE = re.compile(r"^dependencies\s*\{\s*$(.*?)^\}\s*$", re.M | re.S)

GRADLE_MANIFESTS = ("build.gradle.kts", "build.gradle")


def manifest_paths(directory):
    """Existing Gradle manifests in one of an update entry's directories.

    Dependabot's `directories` key accepts globs, so the path is expanded
    rather than tested literally; a path with no wildcard expands to itself.
    """
    base = os.path.join(repo_root, str(directory).strip("/"))
    paths = []
    for name in GRADLE_MANIFESTS:
        paths.extend(glob.glob(os.path.join(base, name), recursive=True))
    return sorted(paths)


def declared_coordinates(manifest_path):
    """Every versioned dependency the manifest declares.

    Maps "group:artifact" to the set of Gradle configurations declaring it. A
    coordinate can appear under more than one: androidx.compose:compose-bom is
    declared identically under `implementation` and `androidTestImplementation`.
    """
    with open(manifest_path) as manifest:
        source = manifest.read()
    coordinates = {}
    for block in DEPENDENCIES_BLOCK_RE.findall(source):
        for configuration, group, artifact, _version in DECLARATION_RE.findall(block):
            coordinates.setdefault("%s:%s" % (group, artifact), set()).add(configuration)
    return coordinates


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


def is_google_hosted(coordinate):
    return coordinate.startswith(GOOGLE_MAVEN_GROUP_PREFIXES)


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


# One walk of each entry's manifests, consumed by every per-entry check below.
#
# Only the manifests in the directories the entry itself names. Gradle
# subprojects are not walked, so a "/"-scoped entry sees the root
# build.gradle.kts, which declares no dependencies block, and nothing of app/;
# it finds no Google-hosted coordinate and falls through the cooldown branch
# below unchecked. Today's entry is scoped to /app, so that path is unreached,
# but a "/" entry would hit issue #905 identically and would need this widened
# rather than trusted.
declarations = {}
for index, entry, names, directories in gradle_entries:
    label = entry_label(index, directories)
    declared = {}
    for directory in directories:
        paths = manifest_paths(directory)
        if not check(bool(paths), "%s covers a directory containing a Gradle manifest (%s)" % (label, directory)):
            continue
        for path in paths:
            for coordinate, configurations in declared_coordinates(path).items():
                declared.setdefault(coordinate, set()).update(configurations)
    declarations[index] = declared


for index, entry, names, directories in gradle_entries:
    label = entry_label(index, directories)

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
        "%s's cooldown exempts all %d of its Google-hosted coordinates, whose release dates maven.google.com "
        "cannot supply per version%s (issue #905)"
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
    label = entry_label(index, directories)

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
