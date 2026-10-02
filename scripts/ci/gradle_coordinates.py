"""The Gradle coordinates an update entry in .github/dependabot.yml covers, and their host.

scripts/test_dependabot_config.sh reads app/build.gradle.kts through this module
to check the gradle entry's cooldown, grouping and pull request limit against
the coordinates it actually declares. scripts/ci/test_gradle_coordinates.py
checks the host classification it uses against Google's Maven repository.

defusedxml is imported only where a group index is parsed, so the guard, which
never fetches one, needs nothing beyond PyYAML.
"""

import glob
import os
import re
import urllib.error
import urllib.request

# A coordinate's host follows from its group, not from its artifact name:
# com.davemorrissey.labs:subsampling-scale-image-view-androidx is served by
# Maven Central despite ending in "androidx". That is the case a loose
# "*androidx*" exclude pattern would wrongly exempt, and the reason this
# classification keys off the group alone.
#
# Each entry is matched against the start of "group:artifact". The list is a
# model of what Google's Maven repository serves, small enough to read, and it
# is not the authority: scripts/ci/test_gradle_coordinates.py asks
# https://maven.google.com about every coordinate the gradle entries declare,
# and fails when this list classes one differently (issue #914).
#
# That check is what stops the model failing quietly. A Google-hosted group
# outside these prefixes (com.google.firebase, say) would be classed
# Central-hosted, and the last cooldown check in
# scripts/test_dependabot_config.sh would then insist cooldown keep holding
# it, which is the configuration that hides it (issue #905). Instead, the pull
# request that declares it fails that test, which names the coordinate. The
# fix is an entry here and a matching pattern in the cooldown exclude list in
# .github/dependabot.yml.
#
# The guard itself does not ask the repository, so it stays an offline,
# deterministic check that the harnesses beside it can run many times over.
GOOGLE_MAVEN_COORDINATE_PREFIXES = ("androidx.", "com.google.android.")

# The Google Maven repository Dependabot's maven-google registry points at. It
# lists what it serves in one group-index.xml per group.
GOOGLE_MAVEN_URL = "https://maven.google.com"

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

# The top-level `dependencies { ... }` block, up to the first line that is a
# closing brace in column 0.
DEPENDENCIES_BLOCK_RE = re.compile(r"^dependencies\s*\{\s*$(.*?)^\}\s*$", re.M | re.S)

GRADLE_MANIFESTS = ("build.gradle.kts", "build.gradle")


def manifest_paths(repo_root, directory):
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


def covered_directories(entry):
    """The directories an update entry covers, or None if it names none that can be read.

    Its `directories` list when it has that key, and otherwise its one
    `directory`. A `directories` key that is not a list names none.
    """
    if "directories" in entry:
        directories = entry["directories"]
        return directories if isinstance(directories, list) else None
    if "directory" in entry:
        return [entry["directory"]]
    return None


def entry_declarations(repo_root, directories):
    """Every versioned coordinate the manifests in an update entry's directories declare.

    Returns (declared, manifests). `declared` maps "group:artifact" to the Gradle
    configurations declaring it, merged across every manifest found. `manifests`
    pairs each directory with the manifests found in it, so a caller can report
    a directory that has none.

    This is the one walk both consumers of this module make, so a coordinate
    the Dependabot config guard classifies is one the live host check sees.

    Only the manifests in the directories the entry itself names. Gradle
    subprojects are not walked, so a "/"-scoped entry sees the root
    build.gradle.kts, which declares no dependencies block, and nothing of
    app/. Today's entry is scoped to /app, so that path is unreached, but a "/"
    entry would need this widened rather than trusted.
    """
    declared = {}
    manifests = []
    for directory in directories:
        paths = manifest_paths(repo_root, directory)
        manifests.append((directory, paths))
        for path in paths:
            for coordinate, configurations in declared_coordinates(path).items():
                declared.setdefault(coordinate, set()).update(configurations)
    return declared, manifests


def is_google_hosted(coordinate):
    """Whether GOOGLE_MAVEN_COORDINATE_PREFIXES classes "group:artifact" as Google-hosted."""
    return coordinate.startswith(GOOGLE_MAVEN_COORDINATE_PREFIXES)


def group_index_url(group):
    """The group-index.xml listing what Google's Maven repository serves under a group."""
    return "%s/%s/group-index.xml" % (GOOGLE_MAVEN_URL, group.replace(".", "/"))


def parse_group_index(document):
    """The artifact names a group-index.xml lists, one child element of the root each."""
    import defusedxml.ElementTree as ET

    return {child.tag for child in ET.fromstring(document)}


def google_maven_artifacts(group, timeout=30):
    """The artifacts Google's Maven repository serves under a group.

    An empty set when it serves no such group, which it answers with a 404.
    This names the artifacts rather than answering for the group as a whole,
    because some groups are split between hosts: maven.google.com lists
    org.jetbrains.kotlin, for a few experimental builds, while kotlin-stdlib and
    the rest of that group are served by Maven Central.

    Raises urllib.error.URLError (an HTTPError for any status but 404) when the
    repository cannot be reached or does not answer with an index.
    """
    try:
        # The URL is built from GOOGLE_MAVEN_URL; the file:// risk does not apply.
        url = group_index_url(group)
        with urllib.request.urlopen(url, timeout=timeout) as response:  # nosemgrep
            return parse_group_index(response.read())
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return set()
        raise
