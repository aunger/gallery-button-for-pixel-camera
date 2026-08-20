#!/usr/bin/env python3
"""Guard: a privileged pull-request job must not check out pull request code.

Five label/byline workflows used to check out the pull request's own merge ref
(the `actions/checkout` default on a `pull_request` event) and then run a
script out of that checkout, in a job declaring `issues: write` and
`pull-requests: write` (issue #882).
Two jobs in build.yml, `file-issues` and `link-pr-to-ci-summary`, had the same
shape.
That is not exploitable today, because GitHub hands a `pull_request` run from a
fork a read-only `GITHUB_TOKEN` no matter what the workflow's `permissions:`
block asks for, and a same-repository pull request author already has write
access.
It becomes exploitable the moment the repository or organization setting "Send
write tokens to workflows from fork pull requests" is turned on, since a fork
pull request could then rewrite the very script the privileged job executes.

Those jobs act only through the GitHub API, on titles, labels, comment bodies
and downloaded artifacts, so none of them needs a pull request's file contents.
Each now pins `ref:` to the base branch.
This guard keeps that from being undone by a later edit that quietly restores
the default checkout, which would break nothing visible and pass every other
check in the tree.

The rule enforced here:

    A job that a `pull_request` (or `pull_request_target`) event can trigger,
    and whose effective permissions grant `issues: write` or
    `pull-requests: write`, must pin the `ref:` of every `actions/checkout`
    step to a ref the pull request does not control.

Only those two scopes are in scope, deliberately.
A job holding `security-events: write` (codeql.yml, semgrep.yml) exists
precisely to analyze the pull request's code and must check out the merge ref,
and the same is true of the build and test jobs.
`issues: write` and `pull-requests: write`, by contrast, are held only by jobs
that manipulate issue and pull request metadata, and no such job in this
repository has any reason to read pull-request-controlled files.
`dependabot-verification-metadata-regen.yml` checks out `pull_request.head.sha`
on purpose and is untouched by this rule: it holds `contents: read` only, which
is the correct shape for running untrusted code.

A job that declares no permissions at all, at either the workflow or the job
level, is treated as privileged.
Its token then carries whatever the repository's default workflow permissions
happen to be, which this guard cannot read and which can include write scopes,
so an explicit declaration is required before the job is judged.

The accepted refs are listed exactly, in ACCEPTED_REFS, rather than matched by
a pattern.
A pattern would have to decide what "not controlled by the pull request" means
for an arbitrary expression, and the subtle cases are the ones that matter:
`github.sha` alone is the merge ref on a `pull_request` event and so is
pull-request-controlled, while `... || github.sha` is safe, because the
fallback is only reached on the events that carry no pull request context.
Adding a genuinely new form here should be a deliberate edit to that list.
"""

import glob
import os
import unittest

import yaml

_CI_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_CI_DIR))
_WORKFLOW_GLOBS = (".github/workflows/*.yml", ".github/workflows/*.yaml")

# Event types that let a pull request's own head branch supply the code a job
# runs. `pull_request_target` is included so that introducing it does not
# silently escape this rule; it is more dangerous than `pull_request`, not less.
PULL_REQUEST_TRIGGERS = frozenset({"pull_request", "pull_request_target"})

# Write scopes that no job in this repository needs alongside a checkout of
# pull request code. See the module docstring for why the list is this short.
PRIVILEGED_SCOPES = frozenset({"issues", "pull-requests"})

# The `ref:` expressions a privileged job may check out.
ACCEPTED_REFS = frozenset(
    {
        "${{ github.event.pull_request.base.sha }}",
        "${{ github.event.pull_request.base.sha || github.sha }}",
    }
)


def workflow_paths() -> list[str]:
    """Return every workflow file in the repository, sorted."""
    paths: list[str] = []
    for pattern in _WORKFLOW_GLOBS:
        paths.extend(glob.glob(os.path.join(_REPO_ROOT, pattern)))
    return sorted(paths)


def triggers(workflow: dict) -> frozenset[str]:
    """Return the event names a workflow is triggered by.

    YAML 1.1 resolves the bare key `on` to the boolean True, which is what
    PyYAML hands back, so the key is looked up both ways.
    """
    on = workflow.get("on", workflow.get(True))
    if isinstance(on, str):
        return frozenset({on})
    if isinstance(on, (dict, list)):
        return frozenset(on)
    return frozenset()


def privileged_scopes(permissions) -> frozenset[str]:
    """Return the PRIVILEGED_SCOPES granted as write by a permissions value.

    `permissions` is a job's effective block: a mapping of scope to level, one
    of the shorthand strings, or None when nothing was declared. None means the
    token carries the repository's default workflow permissions, which cannot
    be read from the tree, so every privileged scope is assumed granted.
    """
    if permissions is None or permissions == "write-all":
        return PRIVILEGED_SCOPES
    if isinstance(permissions, str):
        # "read-all", or any other shorthand that grants no write scope.
        return frozenset()
    return frozenset(
        scope
        for scope, level in permissions.items()
        if scope in PRIVILEGED_SCOPES and level == "write"
    )


def checkout_refs(job: dict) -> list:
    """Return the `ref:` input of each actions/checkout step in a job.

    None marks a checkout step that sets no ref, which is the unpinned default
    this guard exists to catch.
    """
    refs = []
    for step in job.get("steps") or []:
        uses = step.get("uses")
        if not isinstance(uses, str) or not uses.startswith("actions/checkout"):
            continue
        ref = (step.get("with") or {}).get("ref")
        refs.append(ref.strip() if isinstance(ref, str) else ref)
    return refs


def violations(workflow: dict) -> list[str]:
    """Return a message for each privileged job checking out unpinned code."""
    if not triggers(workflow) & PULL_REQUEST_TRIGGERS:
        return []

    found: list[str] = []
    workflow_permissions = workflow.get("permissions")
    for job_name, job in (workflow.get("jobs") or {}).items():
        permissions = job.get("permissions", workflow_permissions)
        scopes = privileged_scopes(permissions)
        if not scopes:
            continue
        held = ", ".join(sorted(scopes)) if permissions is not None else "no declared"
        for ref in checkout_refs(job):
            if ref in ACCEPTED_REFS:
                continue
            shown = "no ref (the pull request's merge ref)" if ref is None else ref
            found.append(
                f"job {job_name!r} holds {held} write permissions and checks out "
                f"{shown}; pin it to one of {sorted(ACCEPTED_REFS)}"
            )
    return found


class PrivilegedWorkflowCheckoutTest(unittest.TestCase):
    """Every workflow in this repository obeys the rule."""

    def test_no_privileged_job_checks_out_pull_request_code(self):
        paths = workflow_paths()
        self.assertTrue(paths, "found no workflow files to check")
        for path in paths:
            relative = os.path.relpath(path, _REPO_ROOT)
            with self.subTest(workflow=relative):
                with open(path, encoding="utf-8") as f:
                    workflow = yaml.safe_load(f) or {}
                found = violations(workflow)
                self.assertEqual([], found, f"{relative}: " + "; ".join(found))


class ViolationDetectionTest(unittest.TestCase):
    """The rule itself, exercised against synthetic workflows.

    Without these, a guard that had stopped detecting anything would still
    report a clean tree.
    """

    UNPINNED = """
on:
  pull_request:
permissions:
  issues: write
  pull-requests: write
jobs:
  label:
    steps:
      - uses: actions/checkout@v6
      - run: python3 scripts/ci/labels/label_by_files.py
"""

    def _violations(self, text: str) -> list[str]:
        return violations(yaml.safe_load(text))

    def _with_ref(self, ref: str) -> str:
        return self.UNPINNED.replace(
            "      - uses: actions/checkout@v6\n",
            "      - uses: actions/checkout@v6\n        with:\n          ref: " + ref + "\n",
        )

    def test_unpinned_privileged_checkout_is_reported(self):
        found = self._violations(self.UNPINNED)
        self.assertEqual(1, len(found), found)
        self.assertIn("merge ref", found[0])

    def test_pinned_privileged_checkout_is_accepted(self):
        pinned = self._with_ref("${{ github.event.pull_request.base.sha }}")
        self.assertEqual([], self._violations(pinned))

    def test_head_sha_is_reported(self):
        found = self._violations(self._with_ref("${{ github.event.pull_request.head.sha }}"))
        self.assertEqual(1, len(found), found)
        self.assertIn("head.sha", found[0])

    def test_bare_github_sha_is_reported(self):
        # github.sha is the merge ref on a pull_request event, so on its own it
        # is exactly the ref this guard rejects.
        found = self._violations(self._with_ref("${{ github.sha }}"))
        self.assertEqual(1, len(found), found)

    def test_pull_request_target_is_covered(self):
        target = self.UNPINNED.replace("  pull_request:", "  pull_request_target:")
        self.assertEqual(1, len(self._violations(target)))

    def test_non_pull_request_workflow_is_ignored(self):
        scheduled = self.UNPINNED.replace("  pull_request:", "  schedule:\n    - cron: '0 0 * * *'")
        self.assertEqual([], self._violations(scheduled))

    def test_unprivileged_scopes_are_ignored(self):
        # A job that must read pull request code, such as a CodeQL analysis or
        # the Dependabot verification-metadata regeneration, is out of scope.
        analyzer = self.UNPINNED.replace(
            "  issues: write\n  pull-requests: write",
            "  contents: read\n  security-events: write",
        )
        self.assertEqual([], self._violations(analyzer))

    def test_job_permissions_replace_workflow_permissions(self):
        # A job-level block replaces the workflow-level one outright, so a
        # privileged workflow default does not make a read-only job privileged.
        narrowed = self.UNPINNED.replace(
            "  label:\n    steps:",
            "  label:\n    permissions:\n      contents: read\n    steps:",
        )
        self.assertEqual([], self._violations(narrowed))

        widened = self.UNPINNED.replace(
            "permissions:\n  issues: write\n  pull-requests: write\n", ""
        ).replace(
            "  label:\n    steps:",
            "  label:\n    permissions:\n      issues: write\n    steps:",
        )
        self.assertEqual(1, len(self._violations(widened)))

    def test_undeclared_permissions_are_treated_as_privileged(self):
        undeclared = self.UNPINNED.replace(
            "permissions:\n  issues: write\n  pull-requests: write\n", ""
        )
        found = self._violations(undeclared)
        self.assertEqual(1, len(found), found)
        self.assertIn("no declared", found[0])

    def test_write_all_is_privileged_and_read_all_is_not(self):
        write_all = self.UNPINNED.replace(
            "permissions:\n  issues: write\n  pull-requests: write", "permissions: write-all"
        )
        self.assertEqual(1, len(self._violations(write_all)))

        read_all = self.UNPINNED.replace(
            "permissions:\n  issues: write\n  pull-requests: write", "permissions: read-all"
        )
        self.assertEqual([], self._violations(read_all))

    def test_job_without_a_checkout_is_ignored(self):
        no_checkout = self.UNPINNED.replace("      - uses: actions/checkout@v6\n", "")
        self.assertEqual([], self._violations(no_checkout))


if __name__ == "__main__":
    unittest.main()
