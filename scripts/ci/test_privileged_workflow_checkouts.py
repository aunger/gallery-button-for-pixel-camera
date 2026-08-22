#!/usr/bin/env python3
"""Guard: a privileged pull-request job must not check out pull request code.

Five label/byline workflows used to check out the pull request's own merge ref
(the `actions/checkout` default on a `pull_request` event) and then run a
script out of that checkout, in a job declaring `issues: write` and
`pull-requests: write` (issue #882).
Two jobs in build.yml, `file-issues` and `link-pr-to-ci-summary`, had the same
shape.
Each now pins `ref:` to the base branch.
This guard keeps that from being undone by a later edit that quietly restores
the default checkout, which would break nothing visible and would pass every
other check in the tree.

What this achieves, and what it does not
----------------------------------------

Read this before concluding that these jobs are now safe against the setting
below, because they are not.

The exposure issue #882 describes is real but latent.
GitHub hands a fork's `pull_request` run a read-only `GITHUB_TOKEN` no matter
what the workflow's `permissions:` block asks for, so the declared write scopes
never materialize for untrusted code.
Turning on the repository or organization setting "Send write tokens to
workflows from fork pull requests" is what would make them materialize.

Pinning the checkout narrows that exposure.
It does not close it.
On a `pull_request` event the workflow file itself is read from the pull
request's merge ref, not from the base branch, which is exactly why these pins
and this guard took effect on the pull request that introduced them.
A fork pull request after that setting was enabled would therefore not need to
touch `label_by_title.py` at all.
It could edit `.github/workflows/label-by-title.yml` directly: add a `run:`
step, restore the default `ref:`, or delete the pinning outright.
It could edit this file too, so that the guard reports a clean tree.

What the pins buy is that such an attack has to appear in a workflow diff,
where it gets read, rather than in a script buried under `scripts/ci/labels/`,
and that the rule is now written down and enforced rather than assumed.
That is worth having.
It is not a security boundary, and nobody should enable that setting on the
strength of it.

The real boundary in this repository is the one
`dependabot-verification-metadata-push.yml` relies on, and its header states
the property exactly: "Workflow files must live on the base branch to run at
all, so what actually executes here is always this reviewed file on main,
never anything from a Dependabot branch."
That holds because it triggers on `workflow_run`.
No `pull_request`-triggered job has that property, which is what makes that
split a boundary and this one a speed bump.

The rule enforced here
----------------------

    A job that a `pull_request` (or `pull_request_target`) event can trigger,
    and whose effective permissions grant any write scope other than
    `security-events`, must pin the `ref:` of every `actions/checkout` step to
    a ref the pull request does not control.

`security-events: write` is the one exemption, because codeql.yml and
semgrep.yml exist precisely to analyze the pull request's code and have to
check out the merge ref to do it.
`dependabot-verification-metadata-regen.yml` also checks out
`pull_request.head.sha` on purpose and needs no exemption: it holds
`contents: read` only, which is the correct shape for running untrusted code.

Every other write scope counts, not only the `issues` and `pull-requests` that
issue #882 happened to involve.
`contents: write` over pull-request-controlled code is a full compromise, and
`actions: write`, `packages: write`, `deployments: write` and `id-token: write`
are each at least as serious as `issues: write`.
No `pull_request`-triggerable job holds any of them today, so the wider rule
flags nothing extra now; the point is that adding such a job later trips this
guard instead of passing it in silence.

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
Adding a genuinely new form should be a deliberate edit to that list.

What this guard does not inspect
--------------------------------

Only `actions/checkout` steps.
A privileged job can still reach pull request code with `gh pr checkout`, a
plain `git fetch origin pull/N/head`, a local composite action, or an artifact
downloaded from an untrusted run, and nothing here would notice.
Given the section above, that is a limit worth stating rather than a hole worth
plugging: the workflow file is pull-request-controlled anyway, so this guard
raises the cost of a quiet regression rather than making one impossible.
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

# The only write scope a pull-request-triggerable job may hold while checking
# out pull request code. Every other write scope makes the job privileged for
# the purposes of this guard. See the module docstring.
EXEMPT_WRITE_SCOPES = frozenset({"security-events"})

# The `ref:` expressions a privileged job may check out.
ACCEPTED_REFS = frozenset(
    {
        "${{ github.event.pull_request.base.sha }}",
        "${{ github.event.pull_request.base.sha || github.sha }}",
    }
)

# The action whose `ref:` this guard inspects, matched on the part before `@`
# so that a hypothetical `actions/checkout-something` is not mistaken for it.
CHECKOUT_ACTION = "actions/checkout"


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


def privileged_write_scopes(permissions) -> frozenset[str] | None:
    """Return the non-exempt write scopes a permissions value grants.

    `permissions` is a job's effective block: a mapping of scope to level, or
    one of the shorthand strings. None is returned for a job that declared no
    permissions at all, meaning the scopes are unknowable from the tree and the
    caller must treat the job as privileged.
    """
    if permissions is None:
        return None
    if permissions == "write-all":
        return frozenset({"write-all"})
    if isinstance(permissions, str):
        # "read-all", or any other shorthand that grants no write scope.
        return frozenset()
    return frozenset(
        scope
        for scope, level in permissions.items()
        if level == "write" and scope not in EXEMPT_WRITE_SCOPES
    )


def checkout_refs(job: dict) -> list:
    """Return the `ref:` input of each actions/checkout step in a job.

    None marks a checkout step that sets no ref, which is the unpinned default
    this guard exists to catch.
    """
    refs = []
    for step in job.get("steps") or []:
        uses = step.get("uses")
        if not isinstance(uses, str) or uses.split("@", 1)[0] != CHECKOUT_ACTION:
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
        scopes = privileged_write_scopes(permissions)
        if scopes is None:
            held = "no declared"
        elif scopes:
            held = ", ".join(sorted(scopes))
        else:
            continue
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

    def _with_permissions(self, block: str) -> str:
        return self.UNPINNED.replace("permissions:\n  issues: write\n  pull-requests: write", block)

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

    def test_every_write_scope_is_privileged(self):
        # Not just the issues/pull-requests pair issue #882 happened to
        # involve: contents: write over pull request code is strictly worse.
        for scope in ("contents", "actions", "packages", "deployments", "id-token"):
            with self.subTest(scope=scope):
                found = self._violations(self._with_permissions(f"permissions:\n  {scope}: write"))
                self.assertEqual(1, len(found), found)
                self.assertIn(scope, found[0])

    def test_security_events_write_is_exempt(self):
        # codeql.yml and semgrep.yml exist to analyze the pull request's code
        # and must check out the merge ref to do it.
        analyzer = self._with_permissions(
            "permissions:\n  contents: read\n  security-events: write"
        )
        self.assertEqual([], self._violations(analyzer))

    def test_security_events_does_not_exempt_a_job_holding_more(self):
        mixed = self._with_permissions("permissions:\n  security-events: write\n  contents: write")
        found = self._violations(mixed)
        self.assertEqual(1, len(found), found)
        self.assertIn("contents", found[0])
        self.assertNotIn("security-events", found[0])

    def test_read_scopes_are_ignored(self):
        reader = self._with_permissions("permissions:\n  contents: read\n  issues: read")
        self.assertEqual([], self._violations(reader))

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
        self.assertEqual(1, len(self._violations(self._with_permissions("permissions: write-all"))))
        self.assertEqual([], self._violations(self._with_permissions("permissions: read-all")))

    def test_job_without_a_checkout_is_ignored(self):
        no_checkout = self.UNPINNED.replace("      - uses: actions/checkout@v6\n", "")
        self.assertEqual([], self._violations(no_checkout))

    def test_a_similarly_named_action_is_not_mistaken_for_checkout(self):
        other = self.UNPINNED.replace(
            "      - uses: actions/checkout@v6\n", "      - uses: actions/checkout-foo@v1\n"
        )
        self.assertEqual([], self._violations(other))


if __name__ == "__main__":
    unittest.main()
