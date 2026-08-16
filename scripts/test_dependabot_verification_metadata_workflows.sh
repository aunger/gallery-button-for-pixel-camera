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

    # (j) budgets enough time for the uncached Gradle invocation. The
    # regenerate job runs the identical uncached build+test invocation as
    # regenerate-gradle-toolchain.yml (both scripts share the same
    # always-fresh mktemp GRADLE_USER_HOME), and that sibling workflow
    # documents a 20-45 minute cold run for it while budgeting 90 minutes.
    # A too-short timeout here does not fail loudly; it cancels a normal
    # run, which the push workflow's `if: ... == 'success'` gate correctly
    # declines to act on, so the PR just never goes green, silently
    # defeating issue #842's whole purpose.
    TIMEOUT="$(grep -E '^[[:space:]]*timeout-minutes:' "$GENERATE_WF" | head -1 | grep -oE '[0-9]+')"
    if [ -n "$TIMEOUT" ] && [ "$TIMEOUT" -ge 90 ]; then
        pass "generate workflow's timeout-minutes ($TIMEOUT) is at least 90, matching regenerate-gradle-toolchain.yml's budget for the identical uncached Gradle invocation"
    else
        fail "generate workflow's timeout-minutes (${TIMEOUT:-unset}) is below 90; regenerate-gradle-toolchain.yml documents a 20-45 minute cold run for the identical script, so anything below that risks cancelling a normal run"
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

    # (k) the base64'd file content reaches jq through a file (--rawfile),
    # never through argv (--arg). A base64'd gradle/verification-metadata.xml
    # is well past Linux's 131072-byte MAX_ARG_STRLEN, so an --arg carrying it
    # makes execve fail with E2BIG before jq even starts (issue #875).
    if grep -q -- '--rawfile content' "$PUSH_WF"; then
        pass "push workflow reads the file content into jq via --rawfile, not argv"
    else
        fail "push workflow does not use 'jq --rawfile content' (the base64'd file content must reach jq through a file, not argv, or a realistic-size run hits Linux's per-argument MAX_ARG_STRLEN; see issue #875)"
    fi
    if grep -q -- '--arg content' "$PUSH_WF"; then
        fail "push workflow still passes file content to jq via --arg (argv), which fails with 'Argument list too long' at the file's current size; use --rawfile instead"
    else
        pass "push workflow does not pass file content to jq via --arg"
    fi

    # (l) the assembled request body reaches curl through a file
    # (--data-binary @file), never through argv (-d). The same argv-limit
    # problem in (k) applies here to the whole assembled JSON body, which is
    # even larger than the base64 payload alone.
    if grep -qE -- '--data-binary[[:space:]]+@' "$PUSH_WF"; then
        pass "push workflow sends the request body via --data-binary @<file>, not argv"
    else
        fail "push workflow does not use 'curl --data-binary @<file>' (the assembled request body must reach curl through a file, not argv, or a realistic-size run hits Linux's per-argument MAX_ARG_STRLEN; see issue #875)"
    fi
    if grep -qE -- '-d[[:space:]]+"\$BODY"' "$PUSH_WF"; then
        fail "push workflow still passes the assembled body to curl via -d \"\$BODY\" (argv), which fails with 'Argument list too long' at the file's current size; use --data-binary @<file> instead"
    else
        pass "push workflow does not pass the assembled body to curl via -d \"\$BODY\""
    fi

    # (m) the push job can write commit statuses. Running on workflow_run
    # attaches this job's own check runs to the base branch, so the affected
    # PR shows no trace of a failure here (issue #877); the outcome is
    # mirrored onto the PR's head commit as a commit status instead, which
    # needs statuses: write in this job's permissions block. As with (i),
    # a job-level block fully replaces the workflow-level one, so the grant
    # has to be listed here.
    if awk '/^jobs:/{injobs=1} injobs && /^  push:/{inpush=1} inpush && /^  [a-z]/ && !/^  push:/{inpush=0} inpush' "$PUSH_WF" | grep -qE '^[[:space:]]*statuses:[[:space:]]*write'; then
        pass "push job's permissions block grants statuses: write"
    else
        fail "push job's permissions block is missing statuses: write (without it the push half cannot report its outcome onto the pull request, and a failure leaves no signal there at all; see issue #877)"
    fi

    # (m) behavioral: run the push step's real "Push the regenerated file
    # through the Contents API" script against a realistic-size artifact
    # (issue #875's file was 266562 bytes, base64ing to about 2.7 times the
    # 131072-byte MAX_ARG_STRLEN). This does not simulate the bug: it runs
    # the real jq and curl binaries (curl mocked only to avoid a network
    # call and to let the test inspect the assembled request), so an
    # argv-limit regression fails here exactly as it failed on PR #874.
    # Also confirms the pushed content, once base64-decoded, is
    # byte-identical to the regenerated artifact.
    set +e
    M_OUTPUT="$(python3 - "$PUSH_WF" <<'PY'
import base64
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap

push_wf_path = sys.argv[1]

try:
    import yaml
except ImportError:
    print("  FAIL: PyYAML is not installed (see scripts/requirements.txt); cannot run the behavioral push-body test")
    sys.exit(1)

results = []


def check(ok, msg):
    results.append(ok)
    print(("  PASS: " if ok else "  FAIL: ") + msg)
    return ok


with open(push_wf_path) as f:
    doc = yaml.safe_load(f)

steps = doc["jobs"]["push"]["steps"]


def extract_step(name):
    """Return the named step, or report a failure and stop the whole block."""
    found = next((s for s in steps if s.get("name") == name), None)
    if found is None:
        check(False, "could not find the %r step to extract for the behavioral test" % name)
        sys.exit(1)
    return found


push_step = extract_step("Push the regenerated file through the Contents API")
report_step = extract_step("Report this job's outcome onto the pull request")

# Mock curl for the behavioral checks in
# scripts/test_dependabot_verification_metadata_workflows.sh (issue #875).
# Records every invocation's argv so the test can inspect the real push
# request, and answers the two GET calls the push step makes before it,
# without any real network access. It honours -o (write the body to a file
# instead of stdout), -w (append the write-out format, with %{http_code}
# substituted) and the -f family (exit 22 on a >=400 status), so a step that
# reads the status code itself is exercised the same way curl would exercise
# it. MOCK_REF_STATUS drives the branch-tip lookup's status code (issue
# #863).
MOCK_CURL = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json
    import os
    import sys

    args = sys.argv[1:]
    calls_dir = os.environ["MOCK_CALLS_DIR"]
    existing = [n for n in os.listdir(calls_dir) if n.startswith("call_")]
    call_path = os.path.join(calls_dir, "call_{}.json".format(len(existing) + 1))
    with open(call_path, "w") as f:
        json.dump(args, f)

    out_path = None
    write_out = None
    for i, a in enumerate(args):
        if a == "-o" and i + 1 < len(args):
            out_path = args[i + 1]
        elif a == "-w" and i + 1 < len(args):
            write_out = args[i + 1]

    url = args[-1] if args else ""
    status = "200"
    if url.endswith("/jobs"):
        body = os.environ.get("MOCK_JOBS_JSON", '{"jobs": []}')
    elif "/statuses/" in url:
        status = os.environ.get("MOCK_STATUS_POST_CODE", "201")
        body = json.dumps({"state": "recorded"})
    elif "/git/ref/heads/" in url:
        status = os.environ.get("MOCK_REF_STATUS", "200")
        if status == "200":
            body = json.dumps({"object": {"sha": os.environ["MOCK_EXPECTED_SHA"]}})
        else:
            body = json.dumps({"message": "mock curl: answering HTTP " + status})
    elif "?ref=" in url:
        # Read through a file: the committed file's base64 is far past
        # MAX_ARG_STRLEN, which applies to environment strings too.
        with open(os.environ["MOCK_EXISTING_CONTENT_B64_FILE"]) as f:
            existing_content = f.read()
        body = json.dumps({
            "sha": os.environ["MOCK_EXISTING_BLOB_SHA"],
            "content": existing_content,
        })
    elif "/contents/" in url:
        body = json.dumps({"content": {"sha": "0" * 40}})
    else:
        sys.stderr.write("mock curl: unrecognized URL: {}\\n".format(url))
        sys.exit(99)

    if out_path:
        with open(out_path, "w") as f:
            f.write(body)
    else:
        sys.stdout.write(body + "\\n")
    if write_out:
        sys.stdout.write(write_out.replace("%{http_code}", status))

    fail_fast = any(
        a.startswith("-") and not a.startswith("--") and "f" in a for a in args
    )
    if fail_fast and int(status) >= 400:
        sys.exit(22)
    """
)


def put_call_of(calls):
    """Return the argv of the Contents API PUT, or None if it never ran."""
    for args in calls:
        url = args[-1] if args else ""
        if "/git/ref/heads/" not in url and "?ref=" not in url and "/contents/" in url:
            return args
    return None


with tempfile.TemporaryDirectory(prefix="dependabot-push-body-test-") as tmp:
    push_script = os.path.join(tmp, "push_step.sh")
    with open(push_script, "w") as f:
        f.write(push_step["run"])
    report_script = os.path.join(tmp, "report_step.sh")
    with open(report_script, "w") as f:
        f.write(report_step["run"])

    artifact_dir = os.path.join(tmp, "artifact")
    os.makedirs(artifact_dir)
    # Vary the bytes (not a repeated pattern) so a truncation or reordering
    # bug cannot hide behind compressibility.
    body = bytearray()
    chunk = b"seed"
    while len(body) < 270000:
        chunk = hashlib.sha256(chunk).digest()
        body += chunk
    artifact_bytes = (
        b'<?xml version="1.0"?>\n<verification-metadata>\n'
        + bytes(body)
        + b"\n</verification-metadata>\n"
    )
    artifact_path = os.path.join(artifact_dir, "verification-metadata.xml")
    with open(artifact_path, "wb") as f:
        f.write(artifact_bytes)

    b64_len = len(base64.b64encode(artifact_bytes))
    if not check(
        b64_len > 131072,
        "test artifact's base64 encoding (%d bytes) exceeds Linux's 131072-byte "
        "MAX_ARG_STRLEN, so this test actually exercises the argv limit" % b64_len,
    ):
        sys.exit(1)

    bin_dir = os.path.join(tmp, "bin")
    os.makedirs(bin_dir)
    mock_curl_path = os.path.join(bin_dir, "curl")
    with open(mock_curl_path, "w") as f:
        f.write(MOCK_CURL)
    st = os.stat(mock_curl_path)
    os.chmod(mock_curl_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    # The mock reads the committed file's base64 from a file rather than an
    # environment variable: at this file's real size it is past
    # MAX_ARG_STRLEN, which bounds environment strings just as it bounds argv.
    stale_b64_path = os.path.join(tmp, "committed-stale.b64")
    with open(stale_b64_path, "w") as f:
        f.write(base64.b64encode(b"stale placeholder content, not the regenerated artifact").decode())
    current_b64_path = os.path.join(tmp, "committed-current.b64")
    with open(current_b64_path, "w") as f:
        f.write(base64.b64encode(artifact_bytes).decode())

    runs = [0]

    def run_step(script, step_env):
        """Run one extracted step, in its own scratch directories.

        Returns the completed process, the argv of every curl invocation it
        made in order, and the step outputs it wrote to $GITHUB_OUTPUT.
        """
        runs[0] += 1
        run_dir = os.path.join(tmp, "run_%d" % runs[0])
        calls_dir = os.path.join(run_dir, "calls")
        runner_temp = os.path.join(run_dir, "runner_temp")
        os.makedirs(calls_dir)
        os.makedirs(runner_temp)
        github_output = os.path.join(run_dir, "github_output")
        open(github_output, "w").close()

        env = dict(os.environ)
        env["PATH"] = bin_dir + os.pathsep + env["PATH"]
        env["RUNNER_TEMP"] = runner_temp
        env["GITHUB_OUTPUT"] = github_output
        env["MOCK_CALLS_DIR"] = calls_dir
        env.update(step_env)

        proc = subprocess.run(
            ["bash", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

        calls = []
        for name in sorted(
            os.listdir(calls_dir), key=lambda n: int(n.split("_")[1].split(".")[0])
        ):
            with open(os.path.join(calls_dir, name)) as f:
                calls.append(json.load(f))

        with open(github_output) as f:
            outputs = dict(
                line.split("=", 1) for line in f.read().splitlines() if "=" in line
            )

        return proc, calls, outputs

    def run_push_step(**overrides):
        """Run the push step with the workflow's own env, as GitHub sets it."""
        step_env = {
            "PAT": "test-pat-not-a-real-secret",
            "REPO": "octocat/example-repo",
            "HEAD_BRANCH": "dependabot/gradle/app/some-dependency-1.2.3",
            "EXPECTED_SHA": "1" * 40,
            "ARTIFACT_DIR": artifact_dir,
            "FILE_PATH": "gradle/verification-metadata.xml",
            "MOCK_EXPECTED_SHA": "1" * 40,
            "MOCK_EXISTING_BLOB_SHA": "d" * 40,
            "MOCK_EXISTING_CONTENT_B64_FILE": stale_b64_path,
        }
        step_env.update(overrides)
        return run_step(push_script, step_env)

    def run_report_step(**overrides):
        """Run the outcome-reporting step with the workflow's own env."""
        step_env = {
            "GH_TOKEN": "test-token-not-a-real-secret",
            "REPO": "octocat/example-repo",
            "HEAD_SHA": "1" * 40,
            "JOB_STATUS": "success",
            "PUSH_RESULT": "pushed",
            "RUN_ID": "31584288707",
            "RUN_ATTEMPT": "1",
            "RUN_URL": "https://github.com/octocat/example-repo/actions/runs/31584288707",
        }
        step_env.update(overrides)
        return run_step(report_script, step_env)

    def posted_status(calls):
        """Return the body of the commit-status POST, or None if never made."""
        for args in calls:
            url = args[-1] if args else ""
            if "/statuses/" not in url:
                continue
            for i, a in enumerate(args):
                if a == "--data-binary" and i + 1 < len(args):
                    with open(args[i + 1].lstrip("@")) as f:
                        return url, json.load(f)
        return None, None

    result, calls, outputs = run_push_step()

    ok = check(
        result.returncode == 0,
        "push step's body-construction script exits 0 against a realistic-size payload "
        "(exit %d; stderr: %s)" % (result.returncode, result.stderr.strip()[-800:]),
    )
    if not ok:
        sys.exit(1)

    put_call = put_call_of(calls)

    if not check(put_call is not None, "the push step made a PUT request to the Contents API"):
        sys.exit(1)

    huge_inline_arg = next((a for a in put_call if len(a) > 8192), None)
    check(
        huge_inline_arg is None,
        "no argument in the PUT curl invocation carries the file content inline (the "
        "largest is %d bytes)" % max((len(a) for a in put_call), default=0),
    )

    check("--data-binary" in put_call, "the PUT request uses --data-binary, not -d, to send the body")
    check("-d" not in put_call, "the PUT request does not also pass -d")

    body_arg = None
    for i, a in enumerate(put_call):
        if a == "--data-binary" and i + 1 < len(put_call):
            body_arg = put_call[i + 1]
            break

    if not check(
        bool(body_arg) and body_arg.startswith("@"),
        "found a --data-binary @<file> argument naming the assembled request body",
    ):
        sys.exit(1)

    body_file = body_arg[1:]
    with open(body_file) as f:
        pushed_body = json.load(f)

    check(
        pushed_body.get("branch") == "dependabot/gradle/app/some-dependency-1.2.3",
        "pushed body's branch matches the workflow_run's head branch",
    )
    check(
        pushed_body.get("sha") == "d" * 40,
        "pushed body's sha matches the existing file's blob sha",
    )
    check(
        "Regenerate gradle/verification-metadata.xml" in pushed_body.get("message", ""),
        "pushed body's commit message names the regenerated file",
    )

    # Dependabot stops rebasing a pull request once a commit it did not author
    # lands on the branch, unless the message carries one of its skip markers
    # (issue #883). Without one, the automation built to help these pull
    # requests silently ends Dependabot's maintenance of every branch it
    # succeeds on.
    skip_markers = (
        "[dependabot skip]",
        "[skip dependabot]",
        "[dependabot-skip]",
        "[skip-dependabot]",
    )
    message = pushed_body.get("message", "")
    check(
        any(m in message.lower() for m in skip_markers),
        "pushed body's commit message carries a Dependabot skip marker, so the branch keeps "
        "being rebased automatically (got %r)" % message,
    )

    # The ID-prefixed noreply address is what links the commit to the
    # github-actions[bot] account's profile; the bare form renders as an
    # unlinked name and email pair instead (issue #864).
    bot_email = "41898282+github-actions[bot]@users.noreply.github.com"
    for role in ("author", "committer"):
        check(
            pushed_body.get(role, {}).get("email") == bot_email,
            "pushed body's %s email is the ID-prefixed github-actions[bot] noreply address "
            "(got %r)" % (role, pushed_body.get(role, {}).get("email")),
        )

    pushed_content_b64 = pushed_body.get("content", "")
    pushed_bytes = None
    try:
        pushed_bytes = base64.b64decode(pushed_content_b64, validate=True)
    except Exception as exc:
        check(False, "pushed body's content is not valid base64: %s" % exc)

    if pushed_bytes is not None:
        check(
            pushed_bytes == artifact_bytes,
            "pushed content, once base64-decoded, is byte-identical to the regenerated artifact",
        )

    # The step's `result` output is what the reporting step below keys on to
    # tell the outcomes apart, so each path has to declare the right one
    # (issue #877).
    check(
        outputs.get("result") == "pushed",
        "push step reports result=pushed after a successful push (got %r)"
        % outputs.get("result"),
    )

    # (n) behavioral: the Dependabot branch was deleted between generation and
    # this run, so the branch-tip lookup 404s (issue #863). That is a "nothing
    # to push" outcome, not a fault: the step must skip cleanly rather than
    # hard-fail under `set -e` and leave a red run carrying no real signal.
    gone, gone_calls, gone_outputs = run_push_step(MOCK_REF_STATUS="404")
    check(
        gone.returncode == 0,
        "push step exits 0 when the Dependabot branch no longer exists (exit %d; stderr: %s)"
        % (gone.returncode, gone.stderr.strip()[-400:]),
    )
    check(
        put_call_of(gone_calls) is None,
        "push step makes no Contents API PUT when the Dependabot branch no longer exists",
    )
    check(
        "::warning::" in gone.stdout,
        "push step warns, rather than staying silent, when the Dependabot branch no longer exists",
    )
    check(
        gone_outputs.get("result") == "branch-gone",
        "push step reports result=branch-gone when the Dependabot branch no longer exists (got %r)"
        % gone_outputs.get("result"),
    )

    # (o) behavioral: any other failure on that same lookup is a genuine API
    # error, not a deleted branch, and must still fail loudly. Treating it as
    # "the branch is gone" would silently drop a regeneration the pull request
    # is waiting on.
    broken, broken_calls, _ = run_push_step(MOCK_REF_STATUS="500")
    check(
        broken.returncode != 0,
        "push step fails when the branch-tip lookup returns an unexpected status (exit %d)"
        % broken.returncode,
    )
    check(
        put_call_of(broken_calls) is None,
        "push step makes no Contents API PUT when the branch-tip lookup returns an unexpected status",
    )
    check(
        "::error::" in broken.stdout,
        "push step reports an error annotation when the branch-tip lookup returns an unexpected status",
    )

    # (p) behavioral: the two remaining no-op paths. Both are successes with
    # nothing to push, and the reporting step tells them apart by `result`.
    moved, moved_calls, moved_outputs = run_push_step(MOCK_EXPECTED_SHA="9" * 40)
    check(
        moved.returncode == 0 and put_call_of(moved_calls) is None,
        "push step skips without pushing when the branch tip moved during regeneration",
    )
    check(
        moved_outputs.get("result") == "branch-moved",
        "push step reports result=branch-moved when the branch tip moved during regeneration (got %r)"
        % moved_outputs.get("result"),
    )

    current, current_calls, current_outputs = run_push_step(
        MOCK_EXISTING_CONTENT_B64_FILE=current_b64_path
    )
    check(
        current.returncode == 0 and put_call_of(current_calls) is None,
        "push step commits nothing when the committed file already matches the regenerated one",
    )
    check(
        current_outputs.get("result") == "already-current",
        "push step reports result=already-current when the committed file already matches (got %r)"
        % current_outputs.get("result"),
    )

    # (q) behavioral: the outcome-reporting step (issue #877). Running on
    # workflow_run puts this job's own check runs on the base branch, so the
    # commit status it writes onto the head SHA is the only trace the pull
    # request ever sees.
    failed_jobs_json = json.dumps(
        {
            "jobs": [
                {
                    "steps": [
                        {"name": "Download the regenerated file, if one was produced", "conclusion": "success"},
                        {"name": "Confirm the push token is configured", "conclusion": "failure"},
                    ]
                }
            ]
        }
    )
    failed, failed_calls, _ = run_report_step(
        JOB_STATUS="failure", PUSH_RESULT="", MOCK_JOBS_JSON=failed_jobs_json
    )
    check(
        failed.returncode == 0,
        "reporting step exits 0 after reporting a failure (exit %d; stderr: %s)"
        % (failed.returncode, failed.stderr.strip()[-400:]),
    )
    status_url, status_body = posted_status(failed_calls)
    if not check(
        status_body is not None,
        "reporting step posts a commit status when the job failed",
    ):
        sys.exit(1)
    check(
        status_url.endswith("/statuses/" + "1" * 40),
        "the failure status is posted against the pull request's head SHA (got %r)" % status_url,
    )
    check(
        status_body.get("state") == "failure",
        "the reported state is 'failure' (got %r)" % status_body.get("state"),
    )
    check(
        "Confirm the push token is configured" in status_body.get("description", ""),
        "the failure status names the step that failed (got %r)" % status_body.get("description"),
    )
    check(
        status_body.get("target_url", "").endswith("/actions/runs/31584288707"),
        "the failure status links this workflow run (got %r)" % status_body.get("target_url"),
    )
    check(
        bool(status_body.get("context")),
        "the failure status carries a context, so a later run replaces it rather than piling up",
    )

    # A failure the jobs API cannot attribute to a named step, such as a
    # cancelled job, still has to reach the pull request.
    cancelled, cancelled_calls, _ = run_report_step(JOB_STATUS="cancelled", PUSH_RESULT="")
    _, cancelled_body = posted_status(cancelled_calls)
    check(
        cancelled.returncode == 0
        and cancelled_body is not None
        and cancelled_body.get("state") == "failure"
        and bool(cancelled_body.get("description")),
        "an unattributable failure is still reported, with a description (got %r)" % cancelled_body,
    )

    # A commit status description is capped at 140 characters; a long step
    # name must be truncated rather than rejected by the API.
    long_name = "Confirm " + ("a very long step name " * 12)
    long_jobs_json = json.dumps(
        {"jobs": [{"steps": [{"name": long_name, "conclusion": "failure"}]}]}
    )
    long_run, long_calls, _ = run_report_step(
        JOB_STATUS="failure", PUSH_RESULT="", MOCK_JOBS_JSON=long_jobs_json
    )
    _, long_body = posted_status(long_calls)
    check(
        long_body is not None and len(long_body.get("description", "")) <= 140,
        "a long failed-step name is truncated to the 140-character status description cap",
    )

    # The success report is not decoration: posted to the same context, it is
    # what clears a failure a previous attempt left on this same commit.
    for push_result, label in (("pushed", "a push"), ("already-current", "a no-op")):
        ok_run, ok_calls, _ = run_report_step(JOB_STATUS="success", PUSH_RESULT=push_result)
        _, ok_body = posted_status(ok_calls)
        check(
            ok_run.returncode == 0
            and ok_body is not None
            and ok_body.get("state") == "success",
            "the reporting step posts a success status after %s (got %r)" % (label, ok_body),
        )

    # The commit can be gone by the time the status is written (the branch was
    # deleted, the pull request closed). That is not worth failing over, and
    # it must not mask the job's real outcome.
    unreachable, _, _ = run_report_step(MOCK_STATUS_POST_CODE="422")
    check(
        unreachable.returncode == 0 and "::warning::" in unreachable.stdout,
        "the reporting step warns, rather than failing, when the head commit is unreachable",
    )

    # Any other API failure means the pull request silently lost its only
    # signal, which is the whole defect this step exists to fix, so it fails
    # loudly and says the job's own outcome is unaffected.
    unreported, _, _ = run_report_step(MOCK_STATUS_POST_CODE="500")
    check(
        unreported.returncode != 0 and "::error::" in unreported.stdout,
        "the reporting step fails loudly when the commit status cannot be posted at all",
    )

# The reporting step's own `if` decides which runs report at all: every
# unsuccessful one, plus the successful ones that acted on an artifact. A run
# that found no artifact is the ordinary outcome on any pull request that does
# not move the dependency graph, including a non-Dependabot one, and must
# leave no status behind.
report_if = str(report_step.get("if", ""))
check(
    "always()" in report_if and "job.status" in report_if,
    "the reporting step runs on every unsuccessful outcome, cancellations included (if: %r)"
    % report_if,
)
check(
    "steps.push.outputs.result" in report_if,
    "the reporting step's success path is gated on the push step's result, so a run with no "
    "artifact reports nothing (if: %r)" % report_if,
)

sys.exit(0 if all(results) else 1)
PY
)"
    M_STATUS=$?
    set -e
    echo "$M_OUTPUT"
    M_PASS=$(grep -c '^  PASS:' <<<"$M_OUTPUT" || true)
    M_FAIL=$(grep -c '^  FAIL:' <<<"$M_OUTPUT" || true)
    PASS=$((PASS + M_PASS))
    FAIL=$((FAIL + M_FAIL))
    if [ "$M_STATUS" -ne 0 ] && [ "$M_FAIL" -eq 0 ]; then
        fail "push step's body-construction behavioral test errored before reporting individual checks (exit $M_STATUS)"
    fi
fi

echo
echo "test_dependabot_verification_metadata_workflows.sh: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
