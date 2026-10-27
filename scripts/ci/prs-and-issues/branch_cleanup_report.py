#!/usr/bin/env python3
"""branch_cleanup_report.py: Sort the repository's branches into delete, keep and review.

`report` prints a Markdown report and deletes nothing.
With `--checklist` it also writes the report to a tracking issue as a checklist: every Delete
branch ticked, every Review branch unticked, Keep branches left out.
With `--choices-output` it appends the branches it offered on that checklist, with their tips
and sections, to a file of `key=value` lines such as `$GITHUB_OUTPUT`.
`delete` deletes the branches ticked on that issue, out of the ones `report` offered, and is
meant to run only after a person has approved it; see "Deleting" below.
.github/workflows/branch-cleanup-report.yml runs both weekly.

A branch goes under "Delete" only on mechanical evidence that deleting it loses no commit:

- `in default branch`: its tip is reachable from the default branch.
- `merged in #N`: its tip is reachable from the head of pull request N, which was merged into
  the default branch.
  A pull request counts as merged into the default branch when GitHub reports it merged and
  either its base is the default branch, or its merge commit is reachable from the default
  branch (a stacked pull request whose base branch was later merged with a merge commit).
- `same tip as X`: its tip is the tip of X, which this report keeps or leaves for review.
  Of two review branches that share a tip, the one first in name order stays for review.

These checks never read a pull request's comments, an issue's state, or a diff.
A branch whose work landed in the default branch some other way (cherry-picked, or redone in
a different pull request) goes under "Review", as does every branch with commits that no
merged pull request carried.

A branch goes under "Keep", whatever the evidence above, when it is the default branch, when
GitHub reports it protected, when it is the head or base of an open pull request, or when its
name matches a `--keep` pattern (`fnmatch` syntax, e.g. `release/*`).

A branch that would be deleted, but whose tip commit is newer than `--grace-days`, goes under
"Review" as `recent` instead.
A branch created from the default branch for new work has its tip in the default branch until
its first push, and this keeps it from being listed for deletion in that window.

Limits:

- Git does not record when a branch was created, so the tip's committer date stands in for it.
  A branch created today from an old commit counts as old.
- `merged in #N` trusts the pull request as merged: a commit on the branch that the same pull
  request later reverted counts as merged.

Deleting:

`delete` refuses to delete anything unless the workflow run it is part of (`--run-id`) has an
approval for the environment named by `--environment`.
GitHub creates an environment named in a workflow, without protection rules, the first time a
job runs in it; until someone adds required reviewers, that job runs unapproved, and this
check is what stops it from deleting.
It then sorts every branch afresh, and deletes a ticked branch only when all of these hold:

- `report` offered it, as `--choices` says.
  Anyone who can edit the issue can add a line to it, so the issue only says which offered
  branches were picked; `--choices` comes from the report job's output, which no one edits.
- No rule above keeps it now.
- It still sorts under "Delete", if `report` offered it there.
  A branch ticked because `report` found it safe to delete is spared once that evidence is gone,
  as when the branch it shared a tip with was since deleted or force-pushed.
- Its tip is still the commit `report` offered.

Each deletion is a `git push --force-with-lease` against that commit, so a branch pushed to
after the check is left alone too.

The script fetches the remote's branches and every `refs/pull/*/head` into a namespace of its
own, `refs/branch-report/`, and unshallows a shallow clone first, so its reachability checks
see the whole history.
Pass `--no-fetch` to reuse what an earlier run fetched.

Usage:
    python3 scripts/ci/prs-and-issues/branch_cleanup_report.py report [--checklist]
        [--choices-output PATH] [--keep PATTERN]
    python3 scripts/ci/prs-and-issues/branch_cleanup_report.py delete --environment NAME
        --choices JSON [--keep PATTERN]

Environment variables:
    GITHUB_REPOSITORY   Owner/repo, unless --repo is given
    GITHUB_TOKEN        Optional for `report` alone, whose API calls can be unauthenticated;
                        `--checklist` needs issues: write, and `delete` also needs actions: read
                        and a git remote it can push to
    GITHUB_RUN_ID       The run `delete` checks for approval, unless --run-id is given
    GITHUB_SERVER_URL   With GITHUB_RUN_ID, links the checklist to the run awaiting approval
"""

import argparse
import datetime
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from github_headers import github_headers
from https_only import urlopen_https

HEADS = "refs/branch-report/heads/"
PULLS = "refs/branch-report/pulls/"

DELETE = "Delete"
REVIEW = "Review"
KEEP = "Keep"
SECTIONS = (
    (DELETE, "Deleting any of these loses no commit."),
    (REVIEW, "Nothing mechanical shows that these are safe to delete; a person decides."),
    (KEEP, "Kept by rule, whatever their contents."),
)

TITLE = "Branch cleanup checklist"
MARKER = "<!-- branch-cleanup-checklist -->"
CHECKLIST_AUTHOR = "github-actions[bot]"
ISSUE_BODY_LIMIT = 65536
# A checklist item records its branch, percent-encoded so no name can end the comment, in a
# comment GitHub does not render.
ITEM = re.compile(r"^- \[(?P<mark>[ xX])\] .*<!-- branch=(?P<name>\S+) -->$")


@dataclass(frozen=True)
class Branch:
    name: str
    sha: str
    committed: datetime.datetime


@dataclass(frozen=True)
class Reach:
    """Where a commit is reachable from: the default branch, and which pull request heads."""

    in_default: bool
    pulls: frozenset[int]


@dataclass(frozen=True)
class Verdict:
    branch: Branch
    section: str
    reason: str


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------


def git(repo_dir: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run git in *repo_dir* and return the completed process, with text output."""
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("git is not on PATH")
    return subprocess.run(
        [executable, "-C", repo_dir, *args], check=check, capture_output=True, text=True
    )


def fetch_refs(repo_dir: str, remote: str) -> None:
    """Fetch *remote*'s branches and pull request heads into `refs/branch-report/`."""
    args = ["fetch", "--quiet", "--no-tags", "--prune"]
    if git(repo_dir, "rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        args.append("--unshallow")
    git(repo_dir, *args, remote, f"+refs/heads/*:{HEADS}*", f"+refs/pull/*/head:{PULLS}*")


def read_branches(repo_dir: str) -> list[Branch]:
    """Return every fetched branch, in name order."""
    out = git(
        repo_dir,
        "for-each-ref",
        "--format=%(objectname) %(committerdate:unix) %(refname)",
        HEADS,
    ).stdout
    branches = []
    for line in out.splitlines():
        sha, stamp, ref = line.split(" ", 2)
        committed = datetime.datetime.fromtimestamp(int(stamp), datetime.UTC)
        branches.append(Branch(ref.removeprefix(HEADS), sha, committed))
    return sorted(branches, key=lambda b: b.name)


def reach(repo_dir: str, default: str, sha: str) -> Reach:
    """Return where *sha* is reachable from, among the fetched refs."""
    refs = git(
        repo_dir, "for-each-ref", f"--contains={sha}", "--format=%(refname)", HEADS, PULLS
    ).stdout.split()
    pulls = frozenset(
        int(ref.removeprefix(PULLS))
        for ref in refs
        if ref.startswith(PULLS) and ref.removeprefix(PULLS).isdigit()
    )
    return Reach(HEADS + default in refs, pulls)


def is_ancestor(repo_dir: str, sha: str, ref: str) -> bool:
    """Return whether *sha* is reachable from *ref*; False when *sha* was never fetched."""
    return git(repo_dir, "merge-base", "--is-ancestor", sha, ref, check=False).returncode == 0


def delete_branch(repo_dir: str, remote: str, name: str, sha: str) -> str | None:
    """Delete branch *name* on *remote* if its tip is still *sha*; return git's error, if any."""
    ref = f"refs/heads/{name}"
    result = git(
        repo_dir,
        "push",
        "--quiet",
        f"--force-with-lease={ref}:{sha}",
        remote,
        f":{ref}",
        check=False,
    )
    if result.returncode == 0:
        return None
    return result.stderr.strip() or "git push failed"


# ---------------------------------------------------------------------------
# GitHub API
# ---------------------------------------------------------------------------


def gh_api(path: str, token: str, method: str = "GET", body: object = None) -> object:
    """Call *path* on the GitHub REST API and return the parsed JSON."""
    headers = github_headers(token)
    if not token:
        del headers["Authorization"]
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    url = f"https://api.github.com/{path}"
    with urlopen_https(url, data, headers=headers, method=method, timeout=30) as r:
        return json.loads(r.read())


def gh_api_pages(path: str, token: str) -> list[dict]:
    """GET every page of the list at *path* (which already carries a query string)."""
    items: list[dict] = []
    page = 1
    while batch := gh_api(f"{path}&per_page=100&page={page}", token):
        items.extend(batch)
        page += 1
    return items


def find_checklist_issue(repo: str, token: str) -> dict | None:
    """Return the open tracking issue this script wrote, or None when there is none."""
    creator = urllib.parse.quote(CHECKLIST_AUTHOR)
    issues = [
        issue
        for issue in gh_api_pages(f"repos/{repo}/issues?state=open&creator={creator}", token)
        if "pull_request" not in issue and (issue.get("body") or "").startswith(MARKER)
    ]
    return min(issues, key=lambda issue: issue["number"], default=None)


def write_checklist_issue(repo: str, token: str, body: str) -> dict:
    """Replace the tracking issue's body with *body*, opening the issue if there is none."""
    if len(body) > ISSUE_BODY_LIMIT:
        raise RuntimeError(f"checklist is {len(body)} characters, over GitHub's limit")
    issue = find_checklist_issue(repo, token)
    if issue is None:
        return gh_api(f"repos/{repo}/issues", token, "POST", {"title": TITLE, "body": body})
    return gh_api(f"repos/{repo}/issues/{issue['number']}", token, "PATCH", {"body": body})


def run_approved(repo: str, token: str, run_id: str, environment: str) -> bool:
    """Return whether a person approved workflow run *run_id* for *environment*."""
    return any(
        review["state"] == "approved"
        and any(env["name"] == environment for env in review["environments"])
        for review in gh_api(f"repos/{repo}/actions/runs/{run_id}/approvals", token)
    )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def merged_into_default(
    pulls: Iterable[dict], default: str, reachable_from_default: Callable[[str], bool]
) -> set[int]:
    """Return the numbers of the pull requests whose work reached the default branch."""
    return {
        pr["number"]
        for pr in pulls
        if pr.get("merged_at")
        and (
            pr["base"]["ref"] == default
            or (pr.get("merge_commit_sha") and reachable_from_default(pr["merge_commit_sha"]))
        )
    }


def keep_reason(
    branch: Branch,
    default: str,
    protected: set[str],
    open_pulls: list[dict],
    repo: str,
    keep_patterns: list[str],
) -> str | None:
    """Return why *branch* is kept by rule, or None when no rule keeps it."""
    if branch.name == default:
        return "default branch"
    if branch.name in protected:
        return "protected"
    for pr in open_pulls:
        head_repo = (pr["head"].get("repo") or {}).get("full_name")
        if pr["head"]["ref"] == branch.name and head_repo == repo:
            return f"head of open #{pr['number']}"
        if pr["base"]["ref"] == branch.name:
            return f"base of open #{pr['number']}"
    for pattern in keep_patterns:
        if fnmatch.fnmatchcase(branch.name, pattern):
            return f"matches `{pattern}`"
    return None


def classify(
    branches: list[Branch],
    *,
    default: str,
    protected: set[str],
    pulls: list[dict],
    repo: str,
    keep_patterns: list[str],
    reach_of: Callable[[str], Reach],
    merged: set[int],
    now: datetime.datetime,
    grace: datetime.timedelta,
) -> list[Verdict]:
    """Sort *branches* into the report's sections; see the module docstring for the rules."""
    open_pulls = [pr for pr in pulls if pr["state"] == "open"]
    verdicts: dict[str, Verdict] = {}
    for branch in branches:
        reason = keep_reason(branch, default, protected, open_pulls, repo, keep_patterns)
        if reason:
            verdicts[branch.name] = Verdict(branch, KEEP, reason)
            continue
        where = reach_of(branch.sha)
        landed = sorted(where.pulls & merged)
        if where.in_default:
            verdicts[branch.name] = Verdict(branch, DELETE, "in default branch")
        elif landed:
            verdicts[branch.name] = Verdict(branch, DELETE, f"merged in #{landed[0]}")
        else:
            verdicts[branch.name] = Verdict(branch, REVIEW, "unmerged commits")

    # A review branch whose tip another branch holds loses nothing when deleted.
    holder: dict[str, str] = {}
    for v in verdicts.values():
        if v.section == KEEP:
            holder.setdefault(v.branch.sha, v.branch.name)
    for v in sorted(verdicts.values(), key=lambda v: v.branch.name):
        if v.section != REVIEW:
            continue
        other = holder.setdefault(v.branch.sha, v.branch.name)
        if other != v.branch.name:
            verdicts[v.branch.name] = Verdict(v.branch, DELETE, f"same tip as `{other}`")

    for name, v in verdicts.items():
        if v.section == DELETE and now - v.branch.committed < grace:
            verdicts[name] = Verdict(v.branch, REVIEW, f"recent; otherwise {v.reason}")
    return [verdicts[b.name] for b in branches]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def cell(text: str) -> str:
    """Escape *text* for a Markdown table cell."""
    return text.replace("\\", "\\\\").replace("|", "\\|")


def branch_link(repo: str, name: str) -> str:
    """Return a Markdown link to branch *name*, escaped for a table cell."""
    url = f"https://github.com/{repo}/tree/{urllib.parse.quote(name, safe='/')}"
    text = cell(name).replace("]", "\\]")
    return f"[{text}]({url})"


def render(verdicts: list[Verdict], *, repo: str, default: str, pulls: list[dict]) -> str:
    """Return the Markdown report."""
    by_head: dict[str, list[dict]] = {}
    for pr in pulls:
        if (pr["head"].get("repo") or {}).get("full_name") == repo:
            by_head.setdefault(pr["head"]["ref"], []).append(pr)

    def pr_states(name: str) -> str:
        prs = sorted(by_head.get(name, []), key=lambda pr: pr["number"])
        return ", ".join(
            f"#{pr['number']} {'merged' if pr.get('merged_at') else pr['state']}" for pr in prs
        )

    lines = [f"# Branch cleanup report for `{repo}`", ""]
    lines.append(f"{len(verdicts)} branches; default branch `{default}`.")
    for section, blurb in SECTIONS:
        rows = [v for v in verdicts if v.section == section]
        lines += ["", f"## {section} ({len(rows)})", "", blurb]
        if not rows:
            continue
        lines += [
            "",
            "| Branch | Why | Tip committed | Pull requests from it |",
            "| --- | --- | --- | --- |",
        ]
        lines += [
            f"| {branch_link(repo, v.branch.name)} | {cell(v.reason)} | {v.branch.committed:%Y-%m-%d} "
            f"| {pr_states(v.branch.name)} |"
            for v in rows
        ]
    return "\n".join(lines) + "\n"


def render_checklist(verdicts: list[Verdict], *, repo: str, run_url: str) -> str:
    """Return the tracking issue's body: Delete branches ticked, Review branches unticked."""
    approve = f"[the run awaiting approval]({run_url})" if run_url else "the run awaiting approval"
    kept = sum(v.section == KEEP for v in verdicts)
    lines = [
        MARKER,
        f"Tick the branches to delete and untick the ones to keep, then approve {approve}.",
        (
            "After approval it deletes each ticked branch that no keep rule protects by then "
            "and whose tip has not moved since this list was written."
        ),
        "The next scheduled run rewrites this list and cancels a run still awaiting approval.",
        "",
        f"{kept} branches kept by rule are not listed; the run's job summary has the full report.",
    ]
    for section, blurb in SECTIONS[:2]:
        rows = [v for v in verdicts if v.section == section]
        lines += ["", f"## {section} ({len(rows)})", "", blurb, ""]
        mark = "x" if section == DELETE else " "
        lines += [
            f"- [{mark}] {branch_link(repo, v.branch.name)}: {v.reason} "
            f"<!-- branch={urllib.parse.quote(v.branch.name, safe='/')} -->"
            for v in rows
        ]
    return "\n".join(lines) + "\n"


def ticked(body: str) -> set[str]:
    """Return the branches ticked on the checklist *body*."""
    found = set()
    for line in body.splitlines():
        m = ITEM.match(line.strip())
        if m and m["mark"] in "xX":
            found.add(urllib.parse.unquote(m["name"]))
    return found


def offered(verdicts: list[Verdict]) -> dict[str, dict[str, str]]:
    """Return the branches a checklist of *verdicts* offers, each with its tip and section."""
    return {
        v.branch.name: {"sha": v.branch.sha, "section": v.section}
        for v in verdicts
        if v.section != KEEP
    }


def plan_deletions(
    chosen: set[str], choices: dict[str, dict[str, str]], verdicts: list[Verdict]
) -> dict[str, str | None]:
    """Map each chosen branch to why it must not be deleted now, or to None if it may be.

    *choices* is what the checklist offered, by branch, with the tip and section it saw;
    *verdicts* is the branches as they stand now.
    """
    current = {v.branch.name: v for v in verdicts}
    plan: dict[str, str | None] = {}
    for name in sorted(chosen):
        v = current.get(name)
        if name not in choices:
            plan[name] = "not offered on the checklist"
        elif v is None:
            plan[name] = "no longer exists"
        elif v.section == KEEP:
            plan[name] = f"kept: {v.reason}"
        elif v.branch.sha != choices[name]["sha"]:
            plan[name] = "tip moved since the checklist was written"
        elif choices[name]["section"] == DELETE and v.section != DELETE:
            plan[name] = f"no longer under {DELETE}: {v.reason}"
        else:
            plan[name] = None
    return plan


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def gather(args: argparse.Namespace, token: str) -> tuple[str, list[dict], list[Verdict]]:
    """Fetch, read the repository's pull requests, and return (default, pulls, verdicts)."""
    if not args.no_fetch:
        fetch_refs(args.git_dir, args.remote)
    default = gh_api(f"repos/{args.repo}", token)["default_branch"]
    protected = {
        b["name"] for b in gh_api_pages(f"repos/{args.repo}/branches?protected=true", token)
    }
    pulls = gh_api_pages(f"repos/{args.repo}/pulls?state=all", token)
    merged = merged_into_default(
        pulls, default, lambda sha: is_ancestor(args.git_dir, sha, HEADS + default)
    )
    verdicts = classify(
        read_branches(args.git_dir),
        default=default,
        protected=protected,
        pulls=pulls,
        repo=args.repo,
        keep_patterns=args.keep,
        reach_of=lambda sha: reach(args.git_dir, default, sha),
        merged=merged,
        now=datetime.datetime.now(datetime.UTC),
        grace=datetime.timedelta(days=args.grace_days),
    )
    return default, pulls, verdicts


def report(args: argparse.Namespace, token: str) -> int:
    default, pulls, verdicts = gather(args, token)
    out = render(verdicts, repo=args.repo, default=default, pulls=pulls)
    if args.checklist:
        run_url = ""
        if args.run_id and os.environ.get("GITHUB_SERVER_URL"):
            run_url = f"{os.environ['GITHUB_SERVER_URL']}/{args.repo}/actions/runs/{args.run_id}"
        body = render_checklist(verdicts, repo=args.repo, run_url=run_url)
        issue = write_checklist_issue(args.repo, token, body)
        out = f"Checklist: [#{issue['number']}]({issue['html_url']})\n\n{out}"
    if args.choices_output:
        with open(args.choices_output, "a", encoding="utf-8") as f:
            f.write(f"choices={json.dumps(offered(verdicts))}\n")
    print(out, end="")
    return 0


def delete(args: argparse.Namespace, token: str) -> int:
    try:
        choices = json.loads(args.choices)
    except json.JSONDecodeError:
        print("error: --choices is not the JSON `report --choices-output` wrote", file=sys.stderr)
        return 1
    if not args.run_id:
        print("error: --run-id or GITHUB_RUN_ID is required", file=sys.stderr)
        return 1
    if not run_approved(args.repo, token, args.run_id, args.environment):
        print(
            f"error: run {args.run_id} has no approval for environment `{args.environment}`; "
            "add required reviewers to it in the repository's environment settings",
            file=sys.stderr,
        )
        return 1
    issue = find_checklist_issue(args.repo, token)
    if issue is None:
        print("No open checklist issue; nothing to delete.")
        return 0
    _, _, verdicts = gather(args, token)
    plan = plan_deletions(ticked(issue.get("body") or ""), choices, verdicts)
    lines = []
    for name, skip in plan.items():
        if skip is None:
            skip = delete_branch(args.git_dir, args.remote, name, choices[name]["sha"])
        lines.append(f"- `{name}`: {'deleted' if skip is None else f'not deleted, {skip}'}")
    summary = "\n".join([f"Run {args.run_id}: {len(plan)} branches ticked.", "", *lines]) + "\n"
    # Printed first, so the job summary records the deletions even if the comment fails.
    print(summary, end="")
    if plan:
        gh_api(
            f"repos/{args.repo}/issues/{issue['number']}/comments", token, "POST", {"body": summary}
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    common.add_argument("--git-dir", default=".", help="a clone of the repository")
    common.add_argument("--remote", default="origin", help="the remote to fetch from")
    common.add_argument("--keep", action="append", default=[], metavar="PATTERN")
    common.add_argument("--grace-days", type=int, default=14)
    common.add_argument("--no-fetch", action="store_true")
    common.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", ""))
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    report_parser = commands.add_parser("report", parents=[common])
    report_parser.add_argument("--checklist", action="store_true")
    report_parser.add_argument("--choices-output", metavar="PATH")
    delete_parser = commands.add_parser("delete", parents=[common])
    delete_parser.add_argument("--environment", required=True)
    delete_parser.add_argument(
        "--choices", required=True, help="the JSON `report --choices-output` wrote"
    )
    args = parser.parse_args(argv)
    if not args.repo:
        parser.error("--repo or GITHUB_REPOSITORY is required")
    token = os.environ.get("GITHUB_TOKEN", "")
    return report(args, token) if args.command == "report" else delete(args, token)


if __name__ == "__main__":
    sys.exit(main())
