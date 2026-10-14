#!/usr/bin/env python3
"""Write the dependency version checks' comments and labels onto a pull request (issue #1284).

The write half of the split described in
`.github/workflows/dependency-version-checks.yml`'s header: it runs from
`dependency-version-checks-report.yml`, on `workflow_run`, so the code here is
always the reviewed copy on main.
What it reads from the pull request's run is the findings file, which
`dependency_checks.load_findings` validates before anything is rendered.

Which pull request: every open one whose head is HEAD_SHA, the commit the
checks ran for.
None is found when the pull request has closed, or when a newer push has moved
its head, in which case the newer push's run reports instead and this one
writes nothing rather than overwrite it with stale findings.

Per check:

- Its comment is the one github-actions[bot] wrote whose first line is the
  check's marker. It is created, or edited in place when its text changes.
  When the pull request no longer changes a build file, an existing comment
  is updated to say so, and a pull request that never had one gets none.
- The comment records the labels it calls for. The check adds those, and
  removes a label only when the previous comment recorded applying it and the
  new findings no longer call for it. So a label someone else applied, such
  as the `dependencies` label Dependabot puts on its own pull requests, is
  never removed unless this check applied it too.
- Labels change through the delta endpoints, never a replace-all (#710).
- The comment is written before the labels, and the labels are left alone
  when that write fails, so no label is ever applied without a record that
  lets a later run remove it.

Environment:
    GITHUB_TOKEN        token with issues: write and pull-requests: write
    GITHUB_REPOSITORY   "owner/repo"
    HEAD_SHA            the pull request head the checks ran for
    FINDINGS            path of the findings file
    GITHUB_OUTPUT       optional; receives "status=checked" or "status=skipped"
                        once the findings validate

Exit code: 0 when every write succeeded or there was nothing to write, 1 when
the findings are invalid or any GitHub call failed.
"""

import os
import sys
import urllib.error

_CI_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_CI_DIR, "labels"))

import dependency_checks as dc  # noqa: E402
import enforce_mutually_exclusive_labels as emxl  # noqa: E402

BOT_LOGIN = "github-actions[bot]"

COMMENTS_PER_PAGE = 100

_API_ERRORS = (urllib.error.HTTPError, urllib.error.URLError)


def pulls_with_head(repo: str, token: str, head_sha: str) -> list[dict]:
    """Return every open pull request whose head commit is *head_sha*."""
    return [
        pr
        for pr in emxl.fetch_open_pull_requests(repo, token)
        if (pr.get("head") or {}).get("sha") == head_sha
    ]


def list_comments(repo: str, token: str, number: int) -> list[dict]:
    """Return every issue comment on pull request *number*, following pagination."""
    comments: list[dict] = []
    page = 1
    while True:
        batch = emxl.gh_api(
            f"repos/{repo}/issues/{number}/comments?per_page={COMMENTS_PER_PAGE}&page={page}",
            token=token,
        )
        if not batch:
            break
        comments.extend(batch)
        if len(batch) < COMMENTS_PER_PAGE:
            break
        page += 1
    return comments


def find_check_comment(comments: list[dict], check: str) -> dict | None:
    """Return the comment this automation wrote for *check*, or None."""
    marker = dc.comment_marker(check)
    for comment in comments:
        body = comment.get("body") or ""
        if (comment.get("user") or {}).get("login") == BOT_LOGIN and (
            body.split("\n", 1)[0] == marker
        ):
            return comment
    return None


def report_check(
    repo: str, token: str, pr: dict, comments: list[dict], check: str, findings: dict
) -> bool:
    """Bring *check*'s comment and labels on *pr* up to date; return False on any failure."""
    number = pr["number"]
    existing = find_check_comment(comments, check)
    if findings["status"] != dc.STATUS_CHECKED and existing is None:
        return True

    body = dc.render_comment(check, findings)
    try:
        if existing is None:
            emxl.gh_api(
                f"repos/{repo}/issues/{number}/comments",
                token=token,
                method="POST",
                body={"body": body},
            )
            print(f"Posted the {check} comment on #{number}.")
        elif existing.get("body") != body:
            emxl.gh_api(
                f"repos/{repo}/issues/comments/{existing['id']}",
                token=token,
                method="PATCH",
                body={"body": body},
            )
            print(f"Updated the {check} comment on #{number}.")
        else:
            print(f"The {check} comment on #{number} is already current.")
    except _API_ERRORS as exc:
        print(
            f"::error::Could not write the {check} comment on #{number}: {exc}. "
            "Leaving its labels unchanged, since the comment is their record.",
            file=sys.stderr,
        )
        return False

    wanted = dc.labels_for(check, findings)
    current = {label.get("name") for label in pr.get("labels") or []}
    previous = set(dc.recorded_labels(existing.get("body") or "")) if existing else set()
    ok = True

    to_add = [label for label in wanted if label not in current]
    if to_add:
        try:
            emxl.gh_api(
                f"repos/{repo}/issues/{number}/labels",
                token=token,
                method="POST",
                body={"labels": to_add},
            )
            print(f"Added {to_add} to #{number}.")
        except _API_ERRORS as exc:
            print(f"::error::Could not add {to_add} to #{number}: {exc}", file=sys.stderr)
            ok = False

    to_remove = sorted((previous & set(dc.CHECK_LABELS[check]) & current) - set(wanted))
    if to_remove and not emxl.remove_labels(number, to_remove, repo, token, reason="cleared"):
        ok = False
    return ok


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    head_sha = os.environ.get("HEAD_SHA", "")
    findings_path = os.environ.get("FINDINGS", "")
    if not (token and repo and head_sha and findings_path):
        print(
            "::error::GITHUB_TOKEN, GITHUB_REPOSITORY, HEAD_SHA and FINDINGS are all required.",
            file=sys.stderr,
        )
        return 1

    try:
        findings = dc.load_findings(findings_path)
    except dc.FindingsError as exc:
        print(f"::error::The findings are not usable: {exc}", file=sys.stderr)
        return 1
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as f:
            f.write(f"status={findings['status']}\n")

    try:
        pulls = pulls_with_head(repo, token, head_sha)
    except _API_ERRORS as exc:
        print(f"::error::Could not list the open pull requests: {exc}", file=sys.stderr)
        return 1
    if not pulls:
        print(
            f"No open pull request has {head_sha} as its head: it has closed, or a newer "
            "push moved it and that push's run reports instead. Nothing to write."
        )
        return 0

    ok = True
    for pr in pulls:
        try:
            comments = list_comments(repo, token, pr["number"])
        except _API_ERRORS as exc:
            print(
                f"::error::Could not list the comments on #{pr['number']}: {exc}",
                file=sys.stderr,
            )
            ok = False
            continue
        for check in dc.CHECKS:
            ok = report_check(repo, token, pr, comments, check, findings) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
