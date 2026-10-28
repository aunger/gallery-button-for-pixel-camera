#!/usr/bin/env python3
"""branch_cleanup_report.py: Sort the repository's branches into delete, keep and review.

`report` prints a Markdown report and deletes nothing.
Under "Delete" it also prints the branch names alone in a block, to copy into an approval
comment.
With `--choices-output` it appends the branches it offered (every branch not kept), with their
tips and sections, to a file of `key=value` lines such as `$GITHUB_OUTPUT`.
`delete` deletes the branches a person names in the comment they approved the run with, out of
the ones `report` offered; see "Deleting" below.
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
"Review" as `recent` instead, and one whose tip is the default branch's own tip goes under
"Review" as `at the default branch's tip`.
A branch created from the default branch for new work has its tip in the default branch until
its first push, and these two rules keep it from being listed for deletion in that window.

Limits:

- Git does not record when a branch was created, so the tip's committer date stands in for it.
  A branch created today from a commit older than the grace period, other than the default
  branch's tip, counts as old.
- `merged in #N` trusts the pull request as merged: a commit on the branch that the same pull
  request later reverted counts as merged.

Deleting:

`delete` refuses to delete anything unless the workflow run it is part of (`--run-id`) has an
approval for the environment named by `--environment`.
GitHub creates an environment named in a workflow, without protection rules, the first time a
job runs in it; until someone adds required reviewers, that job runs unapproved, and this
check is what stops it from deleting.
It also refuses to run unless `--run-attempt` is 1, refusing a re-run attempt: the run's recorded
approvals do not say which attempt they were given for, so a new run, with a new approval, is
the only way to try again.
The branches to delete are the names in the comments of the run's approvals for that
environment, one per line.
A line may carry a list bullet (`- ` or `* `) or be wrapped in backticks, and code-fence lines
are skipped, so the block `report` prints can be pasted as it is.
Any other line, such as prose (`except fix/x`) or two names on one line, makes the comment
ambiguous, and then nothing is deleted.
An approval's comment is written with the approval and cannot be changed after it, so what is
deleted is what the approver chose, not what anyone else could edit in between.
It then sorts every branch afresh, and deletes a named branch only when all of these hold:

- `report` offered it, as `--choices` says; `--choices` comes from the report job's output.
- No rule above keeps it now.
- It still sorts under "Delete", if `report` offered it there.
  A branch named because `report` found it safe to delete is spared once that evidence is gone,
  as when the branch it shared a tip with was since deleted or force-pushed.
- Its tip is still the commit `report` offered.
- Its tip is not about to be lost: a branch under Delete because another branch holds the same
  tip is spared when that other branch is being deleted in the same run.

Each deletion is a `git push --force-with-lease` against that commit, so a branch pushed to
after the check is left alone too.

The script fetches the remote's branches and every `refs/pull/*/head` into a namespace of its
own, `refs/branch-report/`, and unshallows a shallow clone first, so its reachability checks
see the whole history.
Pass `--no-fetch` to reuse what an earlier run fetched.

Usage:
    python3 scripts/ci/prs-and-issues/branch_cleanup_report.py report
        [--choices-output PATH] [--keep PATTERN]
    python3 scripts/ci/prs-and-issues/branch_cleanup_report.py delete --environment NAME
        --choices JSON [--keep PATTERN]

Environment variables:
    GITHUB_REPOSITORY   Owner/repo, unless --repo is given
    GITHUB_TOKEN        Optional for `report`, whose API calls can be unauthenticated;
                        `delete` needs actions: read, and a git remote it can push to
    GITHUB_RUN_ID       The run `delete` reads approvals from, unless --run-id is given
    GITHUB_RUN_ATTEMPT  The attempt of that run, unless --run-attempt is given
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
    # For a `same tip as X` verdict, X: the branch whose presence makes this one deletable.
    holder: str | None = None


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


def gh_api(path: str, token: str) -> object:
    """GET *path* from the GitHub REST API and return the parsed JSON."""
    headers = github_headers(token)
    if not token:
        del headers["Authorization"]
    with urlopen_https(f"https://api.github.com/{path}", headers=headers, timeout=30) as r:
        return json.loads(r.read())


def gh_api_pages(path: str, token: str) -> list[dict]:
    """GET every page of the list at *path* (which already carries a query string)."""
    items: list[dict] = []
    page = 1
    while batch := gh_api(f"{path}&per_page=100&page={page}", token):
        items.extend(batch)
        page += 1
    return items


def approval_comments(repo: str, token: str, run_id: str, environment: str) -> list[str] | None:
    """Return the comments approving run *run_id* for *environment*; None if it has none."""
    reviews = [
        review
        for review in gh_api(f"repos/{repo}/actions/runs/{run_id}/approvals", token)
        if review["state"] == "approved"
        and any(env["name"] == environment for env in review["environments"])
    ]
    if not reviews:
        return None
    return [review.get("comment") or "" for review in reviews]


def parse_names(comment: str) -> tuple[set[str], list[str]]:
    """Return the branch names on *comment*'s lines, and the lines that are not one name.

    A line may carry a `- ` or `* ` bullet or be wrapped in backticks; blank lines and
    code-fence lines are skipped.
    """
    names: set[str] = set()
    other: list[str] = []
    for line in comment.splitlines():
        text = line.strip()
        if not text or text.startswith("```"):
            continue
        if text[:2] in ("- ", "* "):
            text = text[2:].strip()
        if len(text) > 2 and text[0] == text[-1] == "`":
            text = text[1:-1]
        if text and not any(c.isspace() or c == "`" for c in text):
            names.add(text)
        else:
            other.append(line.strip())
    return names, other


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
        # GitHub treats repository names as case-insensitive, so compare them that way.
        if pr["head"]["ref"] == branch.name and (head_repo or "").casefold() == repo.casefold():
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
    default_sha = next((b.sha for b in branches if b.name == default), None)
    verdicts: dict[str, Verdict] = {}
    for branch in branches:
        reason = keep_reason(branch, default, protected, open_pulls, repo, keep_patterns)
        if reason:
            verdicts[branch.name] = Verdict(branch, KEEP, reason)
            continue
        where = reach_of(branch.sha)
        landed = sorted(where.pulls & merged)
        if branch.sha == default_sha:
            verdicts[branch.name] = Verdict(branch, REVIEW, "at the default branch's tip")
        elif where.in_default:
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
        if v.section != REVIEW or v.branch.sha == default_sha:
            continue
        other = holder.setdefault(v.branch.sha, v.branch.name)
        if other != v.branch.name:
            verdicts[v.branch.name] = Verdict(v.branch, DELETE, f"same tip as `{other}`", other)

    for name, v in verdicts.items():
        if v.section == DELETE and now - v.branch.committed < grace:
            verdicts[name] = Verdict(v.branch, REVIEW, f"recent; otherwise {v.reason}", v.holder)
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
        if ((pr["head"].get("repo") or {}).get("full_name") or "").casefold() == repo.casefold():
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
        if section == DELETE:
            lines += ["", "To delete them, approve the run with a comment naming them:", ""]
            lines += fenced([v.branch.name for v in rows])
    return "\n".join(lines) + "\n"


def fenced(names: list[str]) -> list[str]:
    """Return *names* one per line in a Markdown code block that none of them can close."""
    longest = max((len(run) for name in names for run in re.findall("`+", name)), default=0)
    fence = "`" * max(3, longest + 1)
    return [fence, *names, fence]


def offered(verdicts: list[Verdict]) -> dict[str, dict[str, str]]:
    """Return the branches the report offers for deletion, each with its tip and section."""
    return {
        v.branch.name: {"sha": v.branch.sha, "section": v.section}
        for v in verdicts
        if v.section != KEEP
    }


def plan_deletions(
    chosen: set[str], choices: dict[str, dict[str, str]], verdicts: list[Verdict]
) -> dict[str, str | None]:
    """Map each chosen branch to why it must not be deleted now, or to None if it may be.

    *choices* is what the report offered, by branch, with the tip and section it saw;
    *verdicts* is the branches as they stand now.
    """
    current = {v.branch.name: v for v in verdicts}
    plan: dict[str, str | None] = {}
    for name in sorted(chosen):
        v = current.get(name)
        if name not in choices:
            plan[name] = "not offered by the report"
        elif v is None:
            plan[name] = "no longer exists"
        elif v.section == KEEP:
            plan[name] = f"kept: {v.reason}"
        elif v.branch.sha != choices[name]["sha"]:
            plan[name] = "tip moved since the report"
        elif choices[name]["section"] == DELETE and v.section != DELETE:
            plan[name] = f"no longer under {DELETE}: {v.reason}"
        else:
            plan[name] = None
    # A branch deletable only because another holds its tip loses that tip if the other
    # branch goes in the same run. The holder is never itself such a branch, so one pass does.
    for name, skip in plan.items():
        holder = current[name].holder if skip is None else None
        if holder is not None and plan.get(holder, "") is None:
            plan[name] = f"its tip's other holder `{holder}` is being deleted too"
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
    print(render(verdicts, repo=args.repo, default=default, pulls=pulls), end="")
    if args.choices_output:
        with open(args.choices_output, "a", encoding="utf-8") as f:
            f.write(f"choices={json.dumps(offered(verdicts))}\n")
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
    if args.run_attempt != "1":
        print(
            f"error: --run-attempt is {args.run_attempt or 'not set'}, not 1; the approvals of "
            f"run {args.run_id} do not say which attempt they were for, so start a new run",
            file=sys.stderr,
        )
        return 1
    comments = approval_comments(args.repo, token, args.run_id, args.environment)
    if comments is None:
        print(
            f"error: run {args.run_id} has no approval for environment `{args.environment}`; "
            "add required reviewers to it in the repository's environment settings",
            file=sys.stderr,
        )
        return 1
    chosen: set[str] = set()
    other: list[str] = []
    for comment in comments:
        names, lines = parse_names(comment)
        chosen |= names
        other += lines
    if other:
        print("The approval has lines that are not one branch name each; nothing was deleted:")
        print("", *(f"- {line}" for line in other), sep="\n")
        return 1
    if not chosen:
        print("The approval names no branches; nothing to delete.")
        return 0
    _, _, verdicts = gather(args, token)
    plan = plan_deletions(chosen, choices, verdicts)
    lines = []
    failed = 0
    for name, skip in plan.items():
        if skip is None:
            error = delete_branch(args.git_dir, args.remote, name, choices[name]["sha"])
            failed += error is not None
            skip = error
        lines.append(f"- `{name}`: {'deleted' if skip is None else f'not deleted, {skip}'}")
        # The job log streams as it goes, so it keeps this even if the run is cancelled.
        print(lines[-1], file=sys.stderr, flush=True)
    print("\n".join([f"Run {args.run_id}: {len(plan)} branches named.", "", *lines]))
    # A failed push is an error, unlike a branch the checks spared, so it fails the job.
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    common.add_argument("--git-dir", default=".", help="a clone of the repository")
    common.add_argument("--remote", default="origin", help="the remote to fetch from")
    common.add_argument("--keep", action="append", default=[], metavar="PATTERN")
    common.add_argument("--grace-days", type=int, default=14)
    common.add_argument("--no-fetch", action="store_true")
    common.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", ""))
    common.add_argument("--run-attempt", default=os.environ.get("GITHUB_RUN_ATTEMPT", ""))
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    report_parser = commands.add_parser("report", parents=[common])
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
