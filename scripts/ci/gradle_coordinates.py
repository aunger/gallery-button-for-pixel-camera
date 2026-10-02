"""The Gradle coordinates an update entry in .github/dependabot.yml covers, and their host.

scripts/test_dependabot_config.sh reads app/build.gradle.kts through this module
to check the gradle entry's cooldown, grouping and pull request limit against
the coordinates it actually declares.
"""

import glob
import os
import re

# A coordinate's host follows from its group, not from its artifact name:
# com.davemorrissey.labs:subsampling-scale-image-view-androidx is served by
# Maven Central despite ending in "androidx". That is the case a loose
# "*androidx*" exclude pattern would wrongly exempt, and the reason this
# classification keys off the group alone.
#
# These two prefixes cover what app/build.gradle.kts declares today, not every
# group maven.google.com serves. A Google-hosted group outside them
# (com.google.firebase, say) is classed Central-hosted, and the last cooldown
# check in scripts/test_dependabot_config.sh then insists cooldown keep holding
# it, which is the configuration that hides it. Adding such a dependency means
# adding its prefix here as well as to the exclude list in .github/dependabot.yml.
GOOGLE_MAVEN_GROUP_PREFIXES = ("androidx.", "com.google.android.")

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


def is_google_hosted(coordinate):
    return coordinate.startswith(GOOGLE_MAVEN_GROUP_PREFIXES)
