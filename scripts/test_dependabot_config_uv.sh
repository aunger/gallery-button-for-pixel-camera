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
# The fork checks are the exception (issue #1196). No real lock pins a package
# at two versions at the current Python floor, so those are driven from a copy
# of the repository in which one lock does, the script under test being run from
# that copy so that it reads the copy's locks.
#
# The ranged `ignore` rules (issue #1195) are driven the same way, from the
# real config with pyjwt's rule (or a new one) rewritten, since the locks they are
# checked against do not need to change.
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



def pyjwt(entry):
    """The ranged `ignore` rule for pyjwt, the package #1189 concerned."""
    return next(rule for rule in entry["ignore"] if rule["dependency-name"] == "pyjwt")


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
elif mutation == "stale-ignore":
    entry.setdefault("ignore", []).append({"dependency-name": "requests"})
elif mutation == "open-ended-range":
    pyjwt(entry)["versions"] = [">=2.14"]
elif mutation == "inverted-range":
    pyjwt(entry)["versions"] = [">=2.17, <2.14"]
elif mutation == "range-covers-lock":
    pyjwt(entry)["versions"] = [">=2.13, <2.17"]
elif mutation == "range-behind-lock":
    pyjwt(entry)["versions"] = [">=2.0, <2.10"]
elif mutation == "range-for-unlocked-package":
    entry["ignore"].append({"dependency-name": "nonesuch", "versions": [">=1, <2"]})
elif mutation == "unmodeled-ignore-rule":
    entry["ignore"].append({"dependency-name": "pyjwt", "update-types": ["version-update:semver-minor"]})
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

# Build a copy of what the script under test reads (the config, the workflows,
# the app's dependency list and scripts/), with `requests` locked a second time
# under a complementary marker, and print the copy's root. $2 is "ignored" to
# name `requests` in the copy's uv entry `ignore`, as the check requires.
make_forked_repo() {
    local name="$1" ignored="$2" root="$TMP_DIR/$1"
    mkdir -p "$root/app"
    cp -r "$REPO_ROOT/scripts" "$REPO_ROOT/.github" "$root/"
    cp "$REPO_ROOT/app/build.gradle.kts" "$root/app/"
    printf "requests==2.0.0 ; python_full_version < '3.11' \\\n    --hash=sha256:00\n" \
        >> "$root/scripts/requirements.txt"
    if [ "$ignored" = "ignored" ]; then
        python3 - "$root/.github/dependabot.yml" <<'PY' || return 1
import sys

import yaml

path = sys.argv[1]
with open(path) as handle:
    doc = yaml.safe_load(handle)
entry = next(e for e in doc["updates"] if e.get("package-ecosystem") == "uv")
entry["ignore"] = [{"dependency-name": "requests"}]
with open(path, "w") as handle:
    yaml.safe_dump(doc, handle, sort_keys=False)
PY
    fi
    printf '%s\n' "$root"
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
expect_failure stale-ignore "stale: requests"
expect_failure open-ended-range "not closed: pyjwt '>=2.14'"
expect_failure inverted-range "not closed: pyjwt '>=2.17, <2.14'"
expect_failure range-covers-lock "at or below the lock: pyjwt >=2.13, <2.17 (locked 2.13.0)"
expect_failure range-behind-lock "at or below the lock: pyjwt >=2.0, <2.10 (locked 2.13.0)"
expect_failure range-for-unlocked-package "not locked: nonesuch"
expect_failure unmodeled-ignore-rule "the two shapes the checks here model"

# A lock that pins a package at two versions needs that package in `ignore`.
if root="$(make_forked_repo forked-package-not-ignored unignored)"; then
    output="$(bash "$root/scripts/test_dependabot_config.sh" 2>&1)"
    status=$?
    if [ "$status" -ne 0 ] \
        && grep -F -- "  FAIL: " <<< "$output" | grep -qF -- "not ignored: requests (scripts/requirements.txt)"; then
        pass "forked-package-not-ignored fails the check containing \"not ignored: requests (scripts/requirements.txt)\""
    else
        fail "forked-package-not-ignored: expected a FAIL naming requests, got status $status; output was: $output"
    fi
else
    fail "forked-package-not-ignored: the fixture repository could not be written"
fi

# The same fork passes once `requests` is named in `ignore`: the check does not
# reject a fork outright, and an ignore entry for a package that has one is not stale.
if root="$(make_forked_repo forked-package-ignored ignored)"; then
    if output="$(bash "$root/scripts/test_dependabot_config.sh" 2>&1)"; then
        pass "a forked package named in ignore passes, and its entry is not stale"
    else
        fail "forked-package-ignored: the script under test failed; output was: $output"
    fi
else
    fail "forked-package-ignored: the fixture repository could not be written"
fi

echo
echo "test_dependabot_config_uv.sh: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
