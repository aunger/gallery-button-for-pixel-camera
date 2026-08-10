#!/usr/bin/env bash
# test_dependabot_verification_metadata_workflows.sh: guard tests for the
# Dependabot verification-metadata automation added in issue #842.
#
# .github/workflows/dependabot-verification-metadata-regen.yml and
# dependabot-verification-metadata-push.yml are deliberately split so the
# half with write access never executes code from a Dependabot PR, and the
# push half authenticates with a dedicated PAT rather than the default
# GITHUB_TOKEN so the pushed commit actually re-triggers CI (see both
# workflows' header comments for the full rationale). Both properties are
# easy to lose in a well-meaning simplification--folding the two workflows
# back into one `pull_request_target` workflow, or swapping the PAT for
# `secrets.GITHUB_TOKEN`--without anything else in the repo noticing, since
# neither mistake breaks the workflow's own YAML validity or its happy-path
# behavior on the next Dependabot PR (a GITHUB_TOKEN push still succeeds; it
# just never shows up as a re-triggered check). These checks assert the
# security- and correctness-relevant shape of both files, not their runtime
# behavior, which only a live Dependabot PR can exercise (see the manual
# test-plan items on the PR that added this).
#
# Always exits 0 on success, non-zero on failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
GENERATE_WF="$REPO_ROOT/.github/workflows/dependabot-verification-metadata-regen.yml"
PUSH_WF="$REPO_ROOT/.github/workflows/dependabot-verification-metadata-push.yml"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

# (a) both halves exist.
for f in "$GENERATE_WF" "$PUSH_WF"; do
    if [ -f "$f" ]; then
        pass "$(basename "$f") exists"
    else
        fail "$(basename "$f") is missing"
    fi
done

if [ -f "$GENERATE_WF" ]; then
    # (b) the untrusted half uses the unprivileged `pull_request` trigger,
    # never `pull_request_target`, which would execute a Dependabot bump's
    # dependency graph (a full Gradle build) under a privileged token.
    if grep -qE '^[[:space:]]*pull_request:' "$GENERATE_WF"; then
        pass "generate workflow triggers on pull_request"
    else
        fail "generate workflow does not trigger on pull_request"
    fi
    if grep -q 'pull_request_target' "$GENERATE_WF"; then
        fail "generate workflow uses pull_request_target (runs PR code under a privileged token; issue #842's two-workflow split exists specifically to avoid this)"
    else
        pass "generate workflow does not use pull_request_target"
    fi

    # (c) gates on the PR's original author (pull_request.user.login), which
    # a later event on the same PR cannot change, not on github.actor (who
    # triggered THIS event), which a "@dependabot recreate" comment can set
    # to dependabot[bot] on a PR someone else actually opened and controls.
    # See https://labs.boostsecurity.io/articles/weaponizing-dependabot-pwn-request-at-its-finest.
    if grep -Eq "pull_request\.user\.login[[:space:]]*==[[:space:]]*'dependabot\[bot\]'" "$GENERATE_WF"; then
        pass "generate workflow gates on pull_request.user.login"
    else
        fail "generate workflow does not gate on pull_request.user.login (see the confused-deputy note in its header comment)"
    fi
    if grep -Eq "github\.actor[[:space:]]*==[[:space:]]*'dependabot\[bot\]'" "$GENERATE_WF"; then
        fail "generate workflow trusts github.actor for its dependabot gate (spoofable on a same-repo PR via '@dependabot recreate'; gate on pull_request.user.login instead)"
    else
        pass "generate workflow does not gate on github.actor alone"
    fi

    # (d) stays read-only. Only the push workflow may hold contents: write.
    if grep -qE '^[[:space:]]*contents:[[:space:]]*write' "$GENERATE_WF"; then
        fail "generate workflow declares contents: write (it must stay read-only; only the push workflow may write)"
    else
        pass "generate workflow declares no contents: write permission"
    fi
fi

if [ -f "$PUSH_WF" ]; then
    # (e) triggers on workflow_run, naming the generate workflow by its
    # exact `name:`, so the handoff actually wires up.
    if grep -q 'workflow_run:' "$PUSH_WF"; then
        pass "push workflow triggers on workflow_run"
    else
        fail "push workflow does not trigger on workflow_run"
    fi
    GENERATE_NAME="$(sed -n '1s/^name:[[:space:]]*//p' "$GENERATE_WF")"
    if [ -n "$GENERATE_NAME" ] && grep -qF "$GENERATE_NAME" "$PUSH_WF"; then
        pass "push workflow's workflow_run names the generate workflow ($GENERATE_NAME)"
    else
        fail "push workflow's workflow_run does not name the generate workflow's exact 'name:' ($GENERATE_NAME)"
    fi

    # (f) the checkout/push step must authenticate with a dedicated PAT
    # secret, never the default GITHUB_TOKEN. A GITHUB_TOKEN-authored push
    # never triggers a new workflow run (GitHub's own anti-recursion rule),
    # which would leave the PR's other checks stuck against the
    # pre-regeneration commit forever--defeating issue #842's "go green
    # without a manual step" goal just as surely as never pushing at all.
    # Anchored to a bare `token:` key (leading whitespace only) so this does
    # not also match the unrelated `github-token:` input the artifact-download
    # step legitimately passes secrets.GITHUB_TOKEN to.
    if grep -qE '^[[:space:]]*token:[[:space:]]*\$\{\{[[:space:]]*secrets\.GITHUB_TOKEN[[:space:]]*\}\}' "$PUSH_WF"; then
        fail "push workflow authenticates its checkout with secrets.GITHUB_TOKEN (never retriggers CI on the pushed commit; use a dedicated PAT secret instead)"
    else
        pass "push workflow's checkout does not authenticate with secrets.GITHUB_TOKEN"
    fi
    if grep -q 'secrets\.DEPENDABOT_VERIFICATION_PAT' "$PUSH_WF"; then
        pass "push workflow references the DEPENDABOT_VERIFICATION_PAT secret"
    else
        fail "push workflow does not reference a dedicated PAT secret for its checkout/push"
    fi

    # (g) refuses to push over a branch that moved since the file it is
    # about to apply was generated (see that workflow step's own comment).
    if grep -q 'head_sha' "$PUSH_WF"; then
        pass "push workflow compares against workflow_run.head_sha before pushing"
    else
        fail "push workflow has no staleness check against workflow_run.head_sha"
    fi

    # (h) never checks out the Dependabot branch. An earlier revision used
    # actions/checkout on the branch inside this job, the one job in the
    # pair holding contents: write, and CodeQL correctly flagged that as
    # "Checkout of untrusted code in a privileged context": it materialized
    # the untrusted branch's full tree under the PAT. The fix reads and
    # writes the single named file through the GitHub Contents API instead,
    # so nothing here ever checks out PR code. Anchored to a `uses:` step
    # invocation (leading whitespace only) so this does not also match
    # prose mentions of actions/checkout, such as this comment's own.
    if grep -qE '^[[:space:]]*uses:[[:space:]]*actions/checkout' "$PUSH_WF"; then
        fail "push workflow uses actions/checkout (materializes the untrusted Dependabot branch under the contents:write PAT; write the single file through the Contents API instead)"
    else
        pass "push workflow does not check out the Dependabot branch"
    fi

    # (i) the push job's permissions block grants actions: read. It downloads
    # an artifact from the *generate* workflow's run (a different run than
    # its own, via workflow_run.id), which actions/download-artifact's docs
    # say requires an actions:read-scoped token. A job-level `permissions:`
    # block fully replaces the workflow-level one rather than merging with
    # it, so this job would silently have `actions: none` without an
    # explicit grant here, even though the workflow-level block above does
    # not need one. Without it, the download 403s, continue-on-error
    # swallows that, and the job reports a false "nothing to push" on every
    # run: the exact silent-failure mode this whole automation exists to
    # avoid.
    if awk '/^jobs:/{injobs=1} injobs && /^  push:/{inpush=1} inpush && /^  [a-z]/ && !/^  push:/{inpush=0} inpush' "$PUSH_WF" | grep -qE '^[[:space:]]*actions:[[:space:]]*read'; then
        pass "push job's permissions block grants actions: read"
    else
        fail "push job's permissions block is missing actions: read (actions/download-artifact needs it to pull the generate workflow's cross-run artifact; without it the download 403s and continue-on-error silently reports nothing to push, every time)"
    fi
fi

echo
echo "test_dependabot_verification_metadata_workflows.sh: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
