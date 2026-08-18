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
# checks assert it stays wired up: declared, referenced, pointed at Google,
# and additive rather than replacing Maven Central (which still serves
# junit, mockito, robolectric, kotlinx-coroutines-test, org.json and
# subsampling-scale-image-view).
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
CONFIG="$REPO_ROOT/.github/dependabot.yml"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

echo "Checking .github/dependabot.yml"

if [ ! -f "$CONFIG" ]; then
    fail ".github/dependabot.yml exists"
    echo
    echo "test_dependabot_config.sh: $PASS passed, $FAIL failed"
    exit 1
fi
pass ".github/dependabot.yml exists"

set +e
OUTPUT="$(python3 - "$CONFIG" <<'PY'
import sys
from urllib.parse import urlparse

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


with open(config_path) as f:
    doc = yaml.safe_load(f)

if not check(isinstance(doc, dict) and "updates" in doc, "dependabot.yml parses as a mapping with an updates key"):
    sys.exit(1)

registries = doc.get("registries") or {}
updates = doc.get("updates") or []

# Hosts that serve Google's Maven repository. Dependabot's own constant for a
# google() declaration is https://maven.google.com, which routes the lookup
# through its group-index.xml handling; dl.google.com is where that host
# redirects and works through the ordinary maven-metadata.xml path.
GOOGLE_HOSTS = ("maven.google.com", "dl.google.com")


def is_google_maven(registry):
    if not isinstance(registry, dict):
        return False
    if registry.get("type") != "maven-repository":
        return False
    url = str(registry.get("url", ""))
    if "://" not in url:
        url = "https://" + url
    return urlparse(url).hostname in GOOGLE_HOSTS


referenced = set()

for index, entry in enumerate(updates):
    if entry.get("package-ecosystem") != "gradle":
        continue

    directories = entry.get("directories") or [entry.get("directory")]
    label = "gradle update entry %d (%s)" % (index, ", ".join(str(d) for d in directories))
    names = entry.get("registries") or []
    referenced.update(names)

    for name in names:
        check(
            name in registries,
            "%s references registry %r, which is declared under the top-level registries key" % (label, name),
        )

    # A root-scoped entry reads settings.gradle.kts itself, so it finds
    # google() there without a registry; anything narrower cannot.
    if all(str(d) == "/" for d in directories):
        continue

    google = [name for name in names if is_google_maven(registries.get(name))]
    check(
        bool(google),
        "%s references a maven-repository registry for Google's Maven repository, without which no "
        "androidx.* or com.google.android.material coordinate is ever queried (issue #897)" % label,
    )

    for name in google:
        check(
            registries[name].get("replaces-base") is not True,
            "registry %r does not set replaces-base, so Maven Central still serves the coordinates that "
            "live there (junit, mockito, robolectric, kotlinx-coroutines-test, org.json, "
            "subsampling-scale-image-view)" % name,
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
