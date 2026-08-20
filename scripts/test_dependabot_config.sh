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
# What this cannot check is whether GitHub's Dependabot service accepts the
# file and whether a run actually opens pull requests. Only a live run on
# the default branch shows that; it starts within about three minutes of any
# change to this file landing.
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
OUTPUT="$(python3 - "$CONFIG" <<'PY'
import sys

config_path = sys.argv[1]

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
