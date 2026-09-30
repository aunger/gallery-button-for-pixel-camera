#!/usr/bin/env bash
# test_dependabot_config_uv.sh: tests for the Python lock checks in
# scripts/test_dependabot_config.sh (issue #1191).
#
# Those checks pass against today's .github/dependabot.yml, which says nothing
# about whether they can fail. Each case here rewrites one property of the real
# config's uv entry, runs the script under test against the result, and
# requires it to exit non-zero with a FAIL line naming that property.
#
# The fixtures are the real config, loaded and re-dumped with one change, so a
# check that went red on something other than the mutation would still show up
# as the wrong FAIL line rather than passing unnoticed.
#
# The checks that read the lock files rather than the config (the sibling
# `.in` and the header flags) are not driven from here: the script reads the
# locks from the repository it sits in, and a fixture config cannot move them.
#
# Always exits 0 on success, non-zero on failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET="$SCRIPT_DIR/test_dependabot_config.sh"
CONFIG="$REPO_ROOT/.github/dependabot.yml"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

# The script under test needs PyYAML (scripts/requirements.txt) to read the
# config at all. Skip rather than fail, as test_dependabot_config_limit_margin.sh does.
if ! python3 -c "import yaml" > /dev/null 2>&1; then
    echo "SKIP: PyYAML is not installed (see scripts/requirements.txt)"
    exit 0
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

# Write a copy of the real config with one mutation applied to its uv entry.
# $1 names the fixture and the mutation; see the cases in the Python below.
make_fixture() {
    local mutation="$1"
    python3 - "$CONFIG" "$TMP_DIR/$mutation.yml" "$mutation" <<'PY'
import sys

import yaml

source, target, mutation = sys.argv[1:]
with open(source) as handle:
    doc = yaml.safe_load(handle)

updates = doc["updates"]
index = next(i for i, e in enumerate(updates) if e.get("package-ecosystem") == "uv")
entry = updates[index]
groups = entry.get("groups", {})

if mutation == "no-uv-entry":
    del updates[index]
elif mutation == "pip-ecosystem":
    entry["package-ecosystem"] = "pip"
elif mutation == "root-directory":
    entry["directory"] = "/"
elif mutation == "no-allow":
    del entry["allow"]
elif mutation == "direct-pin-in-transitive-group":
    groups["python-transitive"]["exclude-patterns"].remove("ruff")
elif mutation == "direct-pin-ungrouped":
    groups["python-direct"]["patterns"].remove("pyyaml")
elif mutation == "unnormalized-name":
    for name in groups:
        for key in ("patterns", "exclude-patterns"):
            groups[name][key] = ["PyYAML" if p == "pyyaml" else p for p in groups[name].get(key, [])]
elif mutation == "no-groups":
    del entry["groups"]
elif mutation == "forked-package-not-ignored":
    del entry["ignore"]
elif mutation == "stale-ignore":
    entry["ignore"].append({"dependency-name": "requests"})
else:
    sys.exit("unknown mutation " + mutation)

with open(target, "w") as handle:
    yaml.safe_dump(doc, handle, sort_keys=False)
PY
}

# $1 mutation, $2 a fixed string the FAIL line must contain.
expect_failure() {
    local mutation="$1" fragment="$2" output status
    if ! make_fixture "$mutation"; then
        fail "$mutation: the fixture could not be written"
        return
    fi
    output="$(bash "$TARGET" "$TMP_DIR/$mutation.yml" 2>&1)"
    status=$?
    if [ "$status" -eq 0 ]; then
        fail "$mutation: the script under test exited 0; output was: $output"
    elif grep -F -- "  FAIL: " <<< "$output" | grep -qF -- "$fragment"; then
        pass "$mutation fails the check containing \"$fragment\""
    else
        fail "$mutation: no FAIL line contains \"$fragment\"; output was: $output"
    fi
}

echo "Checking the Python lock checks in $TARGET"

if bash "$TARGET" "$CONFIG" > "$TMP_DIR/baseline.txt" 2>&1; then
    pass "the real config passes, so each failure below is the mutation's"
else
    fail "the real config does not pass; output was: $(cat "$TMP_DIR/baseline.txt")"
fi

expect_failure no-uv-entry "an update entry covers the uv ecosystem"
expect_failure pip-ecosystem "covers none of the uv-compiled locks"
expect_failure root-directory "every lock pip-audit gates on sits in a directory a uv entry names"
expect_failure no-allow "allows dependency-type all"
expect_failure direct-pin-in-transitive-group "keeps the \`.in\` pins and the packages they pull in in separate groups"
expect_failure direct-pin-ungrouped "a pull request of its own; ungrouped: pyyaml"
expect_failure unnormalized-name "not normalized: PyYAML"
expect_failure no-groups "pull requests its groups and ungrouped packages can want open at once"
expect_failure forked-package-not-ignored "not ignored: rpds-py (scripts/ci/requirements-semgrep.txt)"
expect_failure stale-ignore "stale: requests"

echo
echo "test_dependabot_config_uv.sh: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
