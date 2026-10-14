#!/usr/bin/env bash
# collect_dependency_reports.sh -- save Gradle's dependency report for each
# configuration the dependency version checks read (issue #1284).
#
# Usage: collect_dependency_reports.sh TREE OUT_DIR
#
# Runs TREE's own ./gradlew once per configuration that
# `dependency_checks.py configurations` lists, and writes each report to
# OUT_DIR under the file name that command gives. TREE is a checkout of the
# side being read: the pull request's head or its base. The configuration list
# comes from this script's own directory, so both sides are read by the same
# list whichever side TREE is.
#
# Dependency verification is off. The checks only read versions, and a
# Dependabot pull request still waiting for its verification-metadata
# regeneration would otherwise fail to resolve instead of being reported.
#
# A configuration fails when Gradle exits non-zero, and also when its report
# marks a dependency FAILED: the dependencies task still exits 0 when it cannot
# download one, which is what a registry's rate limit produces. Either way it
# is retried after each delay in RETRY_DELAYS (seconds, space-separated;
# default "30 120"). Each retry resumes from Gradle's cache rather than
# starting over. A configuration that still fails ends the script non-zero,
# naming it, so the checks fail rather than pass without looking.
# dependency_checks.py rejects a FAILED node too; this check only decides
# whether to retry.

set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 TREE OUT_DIR" >&2
    exit 2
fi

TREE="$1"
OUT_DIR="$2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
read -r -a DELAYS <<< "${RETRY_DELAYS-30 120}"

mkdir -p "$OUT_DIR"

collect() {
    local project="$1" configuration="$2" file="$3"
    local attempt=1 delay
    while true; do
        if (cd "$TREE" && ./gradlew --dependency-verification off --quiet \
            "$project:dependencies" --configuration "$configuration") \
            > "$OUT_DIR/$file" 2> "$OUT_DIR/$file.stderr" \
            && ! grep -q ' FAILED$' "$OUT_DIR/$file"; then
            echo "Saved $project $configuration to $OUT_DIR/$file"
            return 0
        fi
        if (( attempt > ${#DELAYS[@]} )); then
            echo "::error::Gradle could not report $project $configuration after $attempt attempts; the unresolved dependencies and its last error output follow." >&2
            grep ' FAILED$' "$OUT_DIR/$file" >&2 || true
            tail -n 40 "$OUT_DIR/$file.stderr" >&2
            return 1
        fi
        delay="${DELAYS[attempt - 1]}"
        echo "::warning::Gradle could not report $project $configuration (attempt $attempt); retrying in ${delay}s." >&2
        sleep "$delay"
        attempt=$((attempt + 1))
    done
}

CONFIGURATIONS="$(python3 "$SCRIPT_DIR/dependency_checks.py" configurations)"
while read -r project configuration file; do
    collect "$project" "$configuration" "$file"
done <<< "$CONFIGURATIONS"
