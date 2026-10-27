#!/usr/bin/env python3
"""Unit tests for branch_cleanup_report.py.

`classify()` and `merged_into_default()` are tested over fabricated branches, pull requests
and reachability.
The git helpers are tested against real repositories built under a temporary directory: an
origin with a merged branch, a squash-merged branch whose pull request head the origin
carries, and an unmerged branch, fetched into a shallow clone.
"""

import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import branch_cleanup_report as bcr

REPO = "owner/repo"
NOW = datetime.datetime(2026, 10, 26, tzinfo=datetime.UTC)
OLD = NOW - datetime.timedelta(days=30)
GRACE = datetime.timedelta(days=14)


def branch(name: str, sha: str, committed: datetime.datetime = OLD) -> bcr.Branch:
    return bcr.Branch(name, sha, committed)


def pull(
    number: int,
    head: str,
    *,
    state: str = "closed",
    merged: bool = False,
    base: str = "main",
    head_repo: str | None = REPO,
    merge_commit_sha: str | None = None,
) -> dict:
    """Build a pull request as the REST API's list endpoint returns one."""
    return {
        "number": number,
        "state": state,
        "merged_at": "2026-09-01T00:00:00Z" if merged else None,
        "merge_commit_sha": merge_commit_sha,
        "base": {"ref": base},
        "head": {"ref": head, "repo": {"full_name": head_repo} if head_repo else None},
    }


def classify(branches, *, reaches=None, pulls=(), merged=(), protected=(), keep=()):
    """Run `classify()` with every unreachable sha reaching nothing, and return reasons."""
    reaches = reaches or {}
    verdicts = bcr.classify(
        branches,
        default="main",
        protected=set(protected),
        pulls=list(pulls),
        repo=REPO,
        keep_patterns=list(keep),
        reach_of=lambda sha: reaches.get(sha, bcr.Reach(False, frozenset())),
        merged=set(merged),
        now=NOW,
        grace=GRACE,
    )
    return {v.branch.name: (v.section, v.reason) for v in verdicts}


IN_MAIN = bcr.Reach(True, frozenset())


def run(repo_dir: str, *args: str) -> str:
    return bcr.git(repo_dir, *args).stdout.strip()


def commit(repo_dir: str, message: str) -> str:
    """Commit nothing on *repo_dir*'s current branch with *message*, and return the new sha."""
    identity = ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
    run(repo_dir, *identity, "commit", "-q", "--allow-empty", "-m", message)
    return run(repo_dir, "rev-parse", "HEAD")


class TestClassifyDelete(unittest.TestCase):
    def test_tip_in_default_branch(self):
        got = classify([branch("old", "a")], reaches={"a": IN_MAIN})
        self.assertEqual(got["old"], (bcr.DELETE, "in default branch"))

    def test_tip_in_merged_pull_head(self):
        got = classify(
            [branch("squashed", "a")],
            reaches={"a": bcr.Reach(False, frozenset({7, 3}))},
            merged={3, 7},
        )
        self.assertEqual(got["squashed"], (bcr.DELETE, "merged in #3"))

    def test_tip_in_unmerged_pull_head_is_review(self):
        got = classify([branch("closed", "a")], reaches={"a": bcr.Reach(False, frozenset({3}))})
        self.assertEqual(got["closed"], (bcr.REVIEW, "unmerged commits"))

    def test_duplicate_of_a_kept_branch(self):
        got = classify([branch("copy", "a"), branch("test/x", "a")], keep=["test/*"])
        self.assertEqual(got["copy"], (bcr.DELETE, "same tip as `test/x`"))
        self.assertEqual(got["test/x"], (bcr.KEEP, "matches `test/*`"))

    def test_duplicate_review_branches_keep_the_first_by_name(self):
        got = classify([branch("b", "a"), branch("a", "a"), branch("c", "z")])
        self.assertEqual(got["a"], (bcr.REVIEW, "unmerged commits"))
        self.assertEqual(got["b"], (bcr.DELETE, "same tip as `a`"))
        self.assertEqual(got["c"], (bcr.REVIEW, "unmerged commits"))

    def test_recent_tip_goes_to_review(self):
        fresh = NOW - datetime.timedelta(days=1)
        got = classify(
            [branch("new", "a", fresh), branch("x", "b", fresh), branch("y", "b", fresh)],
            reaches={"a": IN_MAIN},
        )
        self.assertEqual(got["new"], (bcr.REVIEW, "recent; otherwise in default branch"))
        self.assertEqual(got["y"], (bcr.REVIEW, "recent; otherwise same tip as `x`"))

    def test_tip_exactly_at_grace_is_old_enough(self):
        got = classify([branch("edge", "a", NOW - GRACE)], reaches={"a": IN_MAIN})
        self.assertEqual(got["edge"][0], bcr.DELETE)


class TestClassifyKeep(unittest.TestCase):
    """Each keep rule wins over evidence that would otherwise delete the branch."""

    def keep(self, name: str, **kwargs) -> tuple[str, str]:
        return classify([branch(name, "a")], reaches={"a": IN_MAIN}, **kwargs)[name]

    def test_default_branch(self):
        self.assertEqual(self.keep("main"), (bcr.KEEP, "default branch"))

    def test_protected(self):
        self.assertEqual(self.keep("release", protected={"release"}), (bcr.KEEP, "protected"))

    def test_head_of_open_pull(self):
        got = self.keep("work", pulls=[pull(5, "work", state="open")])
        self.assertEqual(got, (bcr.KEEP, "head of open #5"))

    def test_base_of_open_pull(self):
        got = self.keep("stack", pulls=[pull(5, "other", state="open", base="stack")])
        self.assertEqual(got, (bcr.KEEP, "base of open #5"))

    def test_open_pull_from_a_fork_with_the_same_ref_does_not_keep(self):
        got = self.keep("work", pulls=[pull(5, "work", state="open", head_repo="fork/repo")])
        self.assertEqual(got, (bcr.DELETE, "in default branch"))

    def test_closed_pull_does_not_keep(self):
        got = self.keep("work", pulls=[pull(5, "work")])
        self.assertEqual(got, (bcr.DELETE, "in default branch"))

    def test_pattern(self):
        self.assertEqual(self.keep("test/v", keep=["test/*"]), (bcr.KEEP, "matches `test/*`"))

    def test_pattern_is_case_sensitive(self):
        self.assertEqual(self.keep("Test/v", keep=["test/*"])[0], bcr.DELETE)


class TestMergedIntoDefault(unittest.TestCase):
    def merged(self, pulls, reachable=()):
        return bcr.merged_into_default(pulls, "main", lambda sha: sha in reachable)

    def test_merged_into_default(self):
        self.assertEqual({1}, self.merged([pull(1, "a", merged=True)]))

    def test_closed_unmerged_is_not(self):
        self.assertEqual(set(), self.merged([pull(1, "a", merge_commit_sha="m")], {"m"}))

    def test_stacked_pull_counts_when_its_merge_commit_reached_default(self):
        prs = [
            pull(1, "a", merged=True, base="stack", merge_commit_sha="m1"),
            pull(2, "b", merged=True, base="stack", merge_commit_sha="m2"),
            pull(3, "c", merged=True, base="stack"),
        ]
        self.assertEqual({1}, self.merged(prs, {"m1"}))


class TestRender(unittest.TestCase):
    def test_sections_rows_and_links(self):
        verdicts = [
            bcr.Verdict(branch("fix/a|b", "a"), bcr.DELETE, "merged in #3"),
            bcr.Verdict(branch("main", "m"), bcr.KEEP, "default branch"),
        ]
        out = bcr.render(
            verdicts,
            repo=REPO,
            default="main",
            pulls=[
                pull(4, "fix/a|b"),
                pull(3, "fix/a|b", merged=True),
                pull(9, "fix/a|b", head_repo="fork/repo"),
            ],
        )
        self.assertIn("## Delete (1)", out)
        self.assertIn("## Review (0)", out)
        self.assertIn("## Keep (1)", out)
        self.assertIn(
            "| [fix/a\\|b](https://github.com/owner/repo/tree/fix/a%7Cb) | merged in #3 "
            "| 2026-09-26 | #3 merged, #4 closed |",
            out,
        )


class TestGhApiPages(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_reads_pages_until_empty_without_a_token(self, mock_urlopen):
        bodies = iter([[{"n": 1}], [{"n": 2}], []])

        def respond(req, data, timeout):
            cm = MagicMock()
            cm.__enter__.return_value.read.return_value = json.dumps(next(bodies)).encode()
            return cm

        mock_urlopen.side_effect = respond
        self.assertEqual([{"n": 1}, {"n": 2}], bcr.gh_api_pages("repos/o/r/pulls?state=all", ""))
        urls = [c.args[0].full_url for c in mock_urlopen.call_args_list]
        self.assertEqual(
            "https://api.github.com/repos/o/r/pulls?state=all&per_page=100&page=3", urls[-1]
        )
        self.assertIsNone(mock_urlopen.call_args.args[0].get_header("Authorization"))


class TestGit(unittest.TestCase):
    """The git helpers over an origin and a shallow clone of it."""

    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.origin = os.path.join(tmp.name, "origin")
        cls.clone = os.path.join(tmp.name, "clone")
        os.makedirs(cls.origin)
        run(cls.origin, "init", "-q", "-b", "main")
        cls.base = commit(cls.origin, "base")
        run(cls.origin, "branch", "merged")
        run(cls.origin, "checkout", "-q", "-b", "squashed")
        cls.squashed = commit(cls.origin, "squashed work")
        run(cls.origin, "update-ref", "refs/pull/1/head", cls.squashed)
        run(cls.origin, "checkout", "-q", "-b", "unmerged", "main")
        cls.unmerged = commit(cls.origin, "unmerged work")
        run(cls.origin, "checkout", "-q", "main")
        cls.squash = commit(cls.origin, "squash of #1")
        for i in range(3):
            commit(cls.origin, f"later {i}")
        bcr.git(tmp.name, "clone", "-q", "--depth", "1", f"file://{cls.origin}", cls.clone)
        bcr.fetch_refs(cls.clone, "origin")

    def test_fetch_unshallows_and_fetches_every_ref(self):
        shallow = bcr.git(self.clone, "rev-parse", "--is-shallow-repository").stdout.strip()
        self.assertEqual("false", shallow)
        names = [b.name for b in bcr.read_branches(self.clone)]
        self.assertEqual(["main", "merged", "squashed", "unmerged"], names)
        pulled = bcr.git(self.clone, "rev-parse", bcr.PULLS + "1").stdout.strip()
        self.assertEqual(self.squashed, pulled)

    def test_reach(self):
        self.assertEqual(bcr.Reach(True, frozenset({1})), bcr.reach(self.clone, "main", self.base))
        self.assertEqual(
            bcr.Reach(False, frozenset({1})), bcr.reach(self.clone, "main", self.squashed)
        )
        self.assertEqual(
            bcr.Reach(False, frozenset()), bcr.reach(self.clone, "main", self.unmerged)
        )

    def test_is_ancestor(self):
        main = bcr.HEADS + "main"
        self.assertTrue(bcr.is_ancestor(self.clone, self.squash, main))
        self.assertFalse(bcr.is_ancestor(self.clone, self.unmerged, main))
        self.assertFalse(bcr.is_ancestor(self.clone, "0" * 40, main))


SHA_A = "a" * 40
SHA_B = "b" * 40


class TestChecklist(unittest.TestCase):
    def verdicts(self) -> list[bcr.Verdict]:
        return [
            bcr.Verdict(branch("done", SHA_A), bcr.DELETE, "in default branch"),
            bcr.Verdict(branch("odd-->name", SHA_B), bcr.REVIEW, "unmerged commits"),
            bcr.Verdict(branch("main", SHA_B), bcr.KEEP, "default branch"),
        ]

    def test_ticks_delete_leaves_review_unticked_and_omits_keep(self):
        body = bcr.render_checklist(self.verdicts(), repo=REPO, run_url="https://x/runs/1")
        self.assertTrue(body.startswith(bcr.MARKER))
        self.assertIn("then approve [the run awaiting approval](https://x/runs/1).", body)
        self.assertIn("1 branches kept by rule are not listed", body)
        self.assertNotIn("tree/main", body)
        self.assertEqual({"done": SHA_A}, bcr.ticked(body))

    def test_ticking_by_hand_as_github_stores_it(self):
        body = bcr.render_checklist(self.verdicts(), repo=REPO, run_url="")
        edited = body.replace("- [x]", "- [ ]").replace("- [ ]", "- [X]", 2).replace("\n", "\r\n")
        self.assertEqual({"done": SHA_A, "odd-->name": SHA_B}, bcr.ticked(edited))

    def test_a_ticked_line_without_the_recorded_tip_is_ignored(self):
        self.assertEqual({}, bcr.ticked("- [x] [extra](https://example.com)\n- [x] extra"))


class TestPlanDeletions(unittest.TestCase):
    def test_each_reason_to_spare_a_ticked_branch(self):
        verdicts = [
            bcr.Verdict(branch("ok", SHA_A), bcr.REVIEW, "unmerged commits"),
            bcr.Verdict(branch("moved", SHA_B), bcr.DELETE, "in default branch"),
            bcr.Verdict(branch("now-open", SHA_A), bcr.KEEP, "head of open #9"),
        ]
        chosen = {"ok": SHA_A, "moved": SHA_A, "now-open": SHA_A, "gone": SHA_A}
        self.assertEqual(
            {
                "gone": "no longer exists",
                "moved": "tip moved since the checklist was written",
                "now-open": "kept: head of open #9",
                "ok": None,
            },
            bcr.plan_deletions(chosen, verdicts),
        )


class TestIssueApi(unittest.TestCase):
    def issue(self, number: int, body: str, **extra) -> dict:
        return {"number": number, "body": body, "html_url": f"https://i/{number}", **extra}

    @patch.object(bcr, "gh_api_pages")
    def test_finds_the_oldest_open_issue_carrying_the_marker(self, pages):
        pages.return_value = [
            self.issue(9, bcr.MARKER),
            self.issue(3, bcr.MARKER, pull_request={}),
            self.issue(5, "no marker"),
            self.issue(7, bcr.MARKER + "\nlist"),
            self.issue(8, None),
        ]
        self.assertEqual(7, bcr.find_checklist_issue(REPO, "t")["number"])
        self.assertIn("creator=github-actions%5Bbot%5D", pages.call_args.args[0])

    @patch.object(bcr, "gh_api")
    @patch.object(bcr, "find_checklist_issue")
    def test_writes_an_existing_issue_or_opens_one(self, find, api):
        find.return_value = self.issue(7, bcr.MARKER)
        bcr.write_checklist_issue(REPO, "t", "body")
        api.assert_called_with(f"repos/{REPO}/issues/7", "t", "PATCH", {"body": "body"})
        find.return_value = None
        bcr.write_checklist_issue(REPO, "t", "body")
        api.assert_called_with(
            f"repos/{REPO}/issues", "t", "POST", {"title": bcr.TITLE, "body": "body"}
        )

    @patch.object(bcr, "find_checklist_issue")
    def test_refuses_a_body_over_the_limit(self, find):
        with self.assertRaises(RuntimeError):
            bcr.write_checklist_issue(REPO, "t", "x" * (bcr.ISSUE_BODY_LIMIT + 1))
        find.assert_not_called()

    @patch.object(bcr, "gh_api")
    def test_run_approved_only_for_an_approval_of_the_environment(self, api):
        def review(state, env):
            return {"state": state, "environments": [{"name": env}]}

        for reviews, expected in [
            ([review("approved", "branch-deletion")], True),
            ([review("rejected", "branch-deletion")], False),
            ([review("approved", "other")], False),
            ([], False),
        ]:
            api.return_value = reviews
            self.assertIs(expected, bcr.run_approved(REPO, "t", "42", "branch-deletion"))
        api.assert_called_with(f"repos/{REPO}/actions/runs/42/approvals", "t")


class TestDeleteCommand(unittest.TestCase):
    ARGV = ("delete", "--repo", REPO, "--environment", "branch-deletion", "--run-id", "42")

    def setUp(self):
        for name in ("run_approved", "find_checklist_issue", "gather", "delete_branch", "gh_api"):
            patcher = patch.object(bcr, name)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        verdicts = [
            bcr.Verdict(branch("ok", SHA_A), bcr.DELETE, "in default branch"),
            bcr.Verdict(branch("main", SHA_B), bcr.KEEP, "default branch"),
        ]
        self.gather.return_value = ("main", [], verdicts)
        body = bcr.render_checklist(verdicts, repo=REPO, run_url="")
        self.find_checklist_issue.return_value = {"number": 7, "body": body + "- [x] main"}
        self.delete_branch.return_value = None

    def test_unapproved_run_deletes_nothing(self):
        self.run_approved.return_value = False
        with patch("sys.stderr"):
            self.assertEqual(1, bcr.main(list(self.ARGV)))
        self.delete_branch.assert_not_called()
        self.gh_api.assert_not_called()

    def test_approved_run_deletes_ticked_branches_and_reports_on_the_issue(self):
        self.run_approved.return_value = True
        with patch("sys.stdout"):
            self.assertEqual(0, bcr.main(list(self.ARGV)))
        self.delete_branch.assert_called_once_with(".", "origin", "ok", SHA_A)
        path, _, method, body = self.gh_api.call_args.args
        self.assertEqual((f"repos/{REPO}/issues/7/comments", "POST"), (path, method))
        self.assertIn("- `ok`: deleted", body["body"])

    def test_a_failed_push_is_reported(self):
        self.run_approved.return_value = True
        self.delete_branch.return_value = "stale info"
        with patch("sys.stdout"):
            bcr.main(list(self.ARGV))
        self.assertIn("- `ok`: not deleted, stale info", self.gh_api.call_args.args[3]["body"])


class TestDeleteBranch(unittest.TestCase):
    """`delete_branch()` against a bare origin, with the lease held and broken."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        origin = os.path.join(tmp.name, "origin.git")
        self.work = os.path.join(tmp.name, "work")
        bcr.git(tmp.name, "init", "-q", "--bare", origin)
        bcr.git(tmp.name, "clone", "-q", origin, self.work)
        self.sha = commit(self.work, "work")
        bcr.git(self.work, "push", "-q", "origin", "HEAD:refs/heads/a", "HEAD:refs/heads/b")

    def remote_has(self, name: str) -> bool:
        out = bcr.git(self.work, "ls-remote", "origin", f"refs/heads/{name}").stdout
        return bool(out.strip())

    def test_deletes_when_the_tip_is_the_recorded_commit(self):
        self.assertIsNone(bcr.delete_branch(self.work, "origin", "a", self.sha))
        self.assertFalse(self.remote_has("a"))

    def test_leaves_a_branch_whose_tip_moved(self):
        self.assertIsNotNone(bcr.delete_branch(self.work, "origin", "b", "1" * 40))
        self.assertTrue(self.remote_has("b"))


if __name__ == "__main__":
    unittest.main()
