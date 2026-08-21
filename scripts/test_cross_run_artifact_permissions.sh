#!/usr/bin/env bash
# test_cross_run_artifact_permissions.sh: every workflow job that reads a
# workflow run's artifacts must hold an actions: read grant.
#
# actions/download-artifact reaches another run's artifacts through the Actions
# API (that is what `run-id:` selects), and its docs require an `actions: read`
# token for that case. A job's own run needs no such grant, so a workflow can
# add a cross-run download and declare nothing, which is what
# .github/workflows/detect-launch-retry.yml did until issue #860 and what
# .github/workflows/dependabot-verification-metadata-push.yml did until issue
# #842's review (PR #859).
#
# Neither turned out to be broken in practice: this repository is public, and
# the pre-fix run 32352974182 shows detect-launch-retry.yml downloading its
# artifact and scanning it with `actions` implicitly none. That undocumented
# allowance is the entire reason the omission is worth guarding rather than
# shrugging at. It is not ours to rely on, it stops applying if this repository
# ever goes private, and it stops without a red run: such downloads carry
# `continue-on-error: true` (there may legitimately be no artifact), so the job
# goes green having silently done nothing. A guard that fires at edit time is
# the only cheap way to catch a scope whose absence is invisible until the
# conditions change.
#
# Scope of the claim, precisely: this checks every job for a *step* that
#
#   - uses actions/download-artifact and passes `run-id:`, or
#   - hits an Actions API artifacts endpoint (.../actions/runs/<id>/artifacts),
#     which needs actions: read whichever run the id names, or
#   - runs `gh run download`,
#
# and requires that job's effective permissions to grant actions: read, with a
# job-level block counted as replacing the workflow-level one rather than
# merging with it. A job that reaches artifacts some other way (a helper script
# that hides the URL, a third-party action) is outside what this can see; the
# three spellings above are the ones this repository actually uses.
#
# This is a check on file shape. Only a live workflow_run event exercises a
# download, so the last section runs the detector over fixtures with known-good
# and known-bad shapes: a detector that stopped detecting fails here instead of
# passing over an empty set.
#
# Always exits 0 on success, non-zero on failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKFLOW_DIR="$REPO_ROOT/.github/workflows"

# shellcheck source=lib/workflow_yaml.sh
. "$REPO_ROOT/scripts/lib/workflow_yaml.sh"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

# Print one line per step in the given job that reads a run's artifacts, naming
# how it does so. Empty output means the job reads none.
#
# The question is asked per step, not per job, so a job that downloads its own
# run's artifacts (no `run-id:`) is not implicated by an unrelated step
# elsewhere in it that happens to mention a run id.
job_artifact_reads() {
    workflow_job_block "$1" "$2" | awk '
        function indent_of(s,   n) { n = match(s, /[^ ]/); return n == 0 ? -1 : n - 1 }
        function evaluate() {
            if (step == "") return
            if (step ~ /uses:[ \t]*actions\/download-artifact/ && step ~ /run-id:/)
                print "actions/download-artifact with run-id:"
            else if (step ~ /actions\/runs\/[^ \t\"]*\/artifacts/)
                print "an Actions API read of a run artifacts endpoint"
            else if (step ~ /gh[ \t]+run[ \t]+download/)
                print "gh run download"
            step = ""
        }
        BEGIN { dash_indent = -1 }
        /^[ \t]*$/ || /^[ \t]*#/ { next }
        # A list item at the shallowest dash indentation in the job body starts
        # a new step. Deeper dashes belong to a step key (a with: list, say).
        /^ *- / {
            if (dash_indent == -1 || indent_of($0) < dash_indent) dash_indent = indent_of($0)
            if (indent_of($0) == dash_indent) evaluate()
        }
        { step = step "\n" $0 }
        END { evaluate() }
    '
}

# Audit one workflow file. Prints a tab-separated verdict line per job that
# reads run artifacts: "ok<TAB>job<TAB>how" or "bad<TAB>job<TAB>diagnosis".
# Silent for a workflow with no such job.
audit_workflow() {
    local wf="$1" job reads how perms scope
    while IFS= read -r job; do
        [ -n "$job" ] || continue
        reads="$(job_artifact_reads "$wf" "$job")"
        [ -n "$reads" ] || continue
        # One line for the message even when several steps qualify.
        how="$(awk 'NR == 1' <<<"$reads")"

        perms="$(workflow_job_permissions "$wf" "$job")"
        scope="its job-level permissions block"
        if [ -z "$perms" ]; then
            perms="$(workflow_permissions "$wf")"
            scope="the workflow-level permissions block it inherits"
        fi

        if [ -z "$perms" ]; then
            printf 'bad\t%s\t%s\n' "$job" \
                "reads run artifacts ($how) but no permissions block applies to it; the default token's scopes depend on a repository setting, so grant actions: read explicitly"
        elif ! permissions_grant_actions_read "$perms"; then
            printf 'bad\t%s\t%s\n' "$job" \
                "reads run artifacts ($how) but $scope does not grant actions: read; that read is documented to need the scope, and today it only works because this repository is public, which is not something to depend on and not something that fails loudly when it stops"
        else
            printf 'ok\t%s\t%s\n' "$job" "$how"
        fi
    done < <(workflow_job_names "$wf")
}

# ── (a) every workflow in the repo ───────────────────────────────────────────
echo "=== (a) every job reading run artifacts grants actions: read ==="
READERS=0
VIOLATIONS=0
for wf in "$WORKFLOW_DIR"/*.yml "$WORKFLOW_DIR"/*.yaml; do
    [ -f "$wf" ] || continue
    while IFS="$(printf '\t')" read -r verdict job message; do
        READERS=$((READERS + 1))
        if [ "$verdict" = "bad" ]; then
            fail "$(basename "$wf") job '$job' $message"
            VIOLATIONS=$((VIOLATIONS + 1))
        fi
    done < <(audit_workflow "$wf")
done
if [ "$READERS" -eq 0 ]; then
    # Not a pass: a check that examined nothing has not established anything.
    # Whether that is a detector regression or a real absence is (b)'s call.
    echo "  NOTE: no job in any workflow was seen reading run artifacts, so this check asserted nothing; (b) below decides whether that is a detector regression"
elif [ "$VIOLATIONS" -eq 0 ]; then
    pass "all $READERS job(s) reading run artifacts grant actions: read"
fi

# ── (b) the known readers are still seen ─────────────────────────────────────
# If a refactor renames a job, moves the read, or breaks an assumption the
# helpers make about workflow layout, (a) would quietly go back to checking
# nothing. These two jobs are the reason this file exists; they must stay
# visible to it.
echo ""
echo "=== (b) the known artifact-reading jobs are still detected ==="
while IFS=" " read -r known_wf known_job; do
    [ -n "$known_wf" ] || continue
    if [ ! -f "$WORKFLOW_DIR/$known_wf" ]; then
        fail "$known_wf is missing; if it was removed on purpose, drop it from this list"
        continue
    fi
    if [ -n "$(job_artifact_reads "$WORKFLOW_DIR/$known_wf" "$known_job")" ]; then
        pass "$known_wf job '$known_job' is recognized as reading run artifacts"
    else
        fail "$known_wf job '$known_job' is no longer recognized as reading run artifacts; either the workflow changed shape or the detector stopped working, and in the latter case check (a) is now vacuous"
    fi
done <<'KNOWN'
detect-launch-retry.yml detect
dependabot-verification-metadata-push.yml push
KNOWN

# ── (c) the detector itself, against fixtures ────────────────────────────────
echo ""
echo "=== (c) detector flags a missing grant and accepts a present one ==="
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# A cross-run download under a workflow-level block with no actions: read:
# detect-launch-retry.yml's shape before issue #860.
cat > "$TMP/bad-download.yml" <<'YAML'
on:
  workflow_run:
    workflows: ["Build"]
    types: [completed]

permissions:
  issues: write

jobs:
  detect:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v7
        with:
          name: feed
          run-id: ${{ github.event.workflow_run.id }}
        continue-on-error: true
YAML

# The same read spelled as an API call, which no check keyed to
# actions/download-artifact would see.
cat > "$TMP/bad-api.yml" <<'YAML'
permissions:
  contents: read

jobs:
  inspect:
    runs-on: ubuntu-latest
    steps:
      - name: Count the artifacts the other run produced
        run: |
          curl -sf "https://api.github.com/repos/$REPO/actions/runs/$RUN_ID/artifacts?name=x" | jq -r .total_count
YAML

# A job-level block that omits the grant. A job-level block replaces the
# workflow-level one rather than merging with it, so this is a violation even
# though the workflow level grants the scope.
cat > "$TMP/bad-job-override.yml" <<'YAML'
permissions:
  actions: read

jobs:
  detect:
    runs-on: ubuntu-latest
    permissions:
      issues: write
    steps:
      - uses: actions/download-artifact@v7
        with:
          run-id: ${{ github.event.workflow_run.id }}
YAML

# Grant present at the job level, absent at the workflow level: allowed.
cat > "$TMP/good-job.yml" <<'YAML'
permissions:
  issues: write

jobs:
  detect:
    runs-on: ubuntu-latest
    permissions:
      issues: write
      actions: read
    steps:
      - uses: actions/download-artifact@v7
        with:
          run-id: ${{ github.event.workflow_run.id }}
YAML

# A flow mapping is the same grant written differently.
cat > "$TMP/good-flow-mapping.yml" <<'YAML'
permissions: { actions: read, issues: write }

jobs:
  detect:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v7
        with:
          run-id: ${{ github.event.workflow_run.id }}
YAML

# Mapping keys have no required order, so a top-level permissions block may
# follow jobs:. Reading it by column rather than by position keeps this from
# looking like a workflow that declares nothing.
cat > "$TMP/good-permissions-after-jobs.yml" <<'YAML'
jobs:
  detect:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v7
        with:
          run-id: ${{ github.event.workflow_run.id }}

permissions:
  actions: read
YAML

# A same-run download needs no grant, and a run id mentioned by an unrelated
# step in the same job must not make one look cross-run.
cat > "$TMP/good-same-run.yml" <<'YAML'
permissions:
  contents: read

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v7
        with:
          name: apk
      - name: Report
        run: echo "run-id: ${{ github.run_id }}"
YAML

expect_verdict() {
    local want="$1" fixture="$2" out
    out="$(audit_workflow "$TMP/$fixture" | grep -c "^$want")"
    if [ "$out" -gt 0 ]; then
        pass "$fixture is judged '$want'"
    else
        fail "$fixture is not judged '$want': $(audit_workflow "$TMP/$fixture")"
    fi
}

expect_verdict bad bad-download.yml
expect_verdict bad bad-api.yml
expect_verdict bad bad-job-override.yml
expect_verdict ok good-job.yml
expect_verdict ok good-flow-mapping.yml
expect_verdict ok good-permissions-after-jobs.yml

# The same-run fixture must produce no verdict at all: nothing to judge.
if [ -z "$(audit_workflow "$TMP/good-same-run.yml")" ]; then
    pass "good-same-run.yml is not treated as reading another run's artifacts"
else
    fail "good-same-run.yml was picked up as an artifact reader: $(audit_workflow "$TMP/good-same-run.yml")"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed."
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
