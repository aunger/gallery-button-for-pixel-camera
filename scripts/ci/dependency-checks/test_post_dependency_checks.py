#!/usr/bin/env python3
"""Unit tests for post_dependency_checks.py, against a fake GitHub API."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dependency_checks as dc  # noqa: E402
import post_dependency_checks as post  # noqa: E402
from test_dependency_checks import findings_1145  # noqa: E402

REPO = "owner/repo"
HEAD = "c" * 40


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.github.com/x", code, "error", {}, None)


class FakeGitHub:
    """Records every call and answers from a list of comments and one pull request."""

    def __init__(self, labels=(), comments=(), fail=()):
        self.pr = {"number": 7, "head": {"sha": HEAD}, "labels": [{"name": n} for n in labels]}
        self.comments = [dict(c) for c in comments]
        self.fail = set(fail)  # (method, path prefix) pairs that raise
        self.calls: list[tuple[str, str, object]] = []

    def gh_api(self, path, token, method="GET", body=None, max_attempts=4):
        self.calls.append((method, path, body))
        for fail_method, prefix in self.fail:
            if method == fail_method and path.startswith(prefix):
                raise http_error(500)
        if method == "GET" and "/comments?" in path:
            return self.comments if path.endswith("page=1") else []
        if method == "DELETE":
            name = urllib.parse.unquote(path.rsplit("/", 1)[1])
            if name not in {label["name"] for label in self.pr["labels"]}:
                raise http_error(404)
        return None

    def fetch_open_pull_requests(self, repo, token):
        return [self.pr, {"number": 8, "head": {"sha": "d" * 40}, "labels": []}]

    def writes(self):
        return [(m, p, b) for m, p, b in self.calls if m != "GET"]


def bot_comment(comment_id, body):
    return {"id": comment_id, "user": {"login": "github-actions[bot]"}, "body": body}


class PostTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.output = os.path.join(self.dir, "output")

    def run_post(self, github, findings):
        path = os.path.join(self.dir, "findings.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(findings, f)
        env = {
            "GITHUB_TOKEN": "t",
            "GITHUB_REPOSITORY": REPO,
            "HEAD_SHA": HEAD,
            "FINDINGS": path,
            "GITHUB_OUTPUT": self.output,
        }
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(post.emxl, "gh_api", github.gh_api),
            mock.patch.object(
                post.emxl, "fetch_open_pull_requests", github.fetch_open_pull_requests
            ),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = post.main()
        return code, out.getvalue() + err.getvalue()

    def test_first_report_posts_four_comments_and_their_labels(self):
        github = FakeGitHub()
        code, _ = self.run_post(github, findings_1145())
        self.assertEqual(code, 0)
        posted = [b["body"] for m, p, b in github.writes() if p.endswith("/issues/7/comments")]
        self.assertEqual(
            [body.split("\n", 1)[0] for body in posted], [dc.comment_marker(c) for c in dc.CHECKS]
        )
        added = [b["labels"] for m, p, b in github.writes() if p.endswith("/issues/7/labels")]
        self.assertEqual(
            added,
            [
                ["dependencies", "dep: hidden change"],
                ["dep: pin drift"],
                ["dep: compose misaligned"],
            ],
        )
        with open(self.output, encoding="utf-8") as f:
            self.assertEqual(f.read(), "status=checked\n")

    def test_labels_already_present_are_not_added_again(self):
        github = FakeGitHub(labels=["dependencies", "dep: hidden change"])
        self.run_post(github, findings_1145())
        added = [b["labels"] for m, p, b in github.writes() if p.endswith("/labels")]
        self.assertNotIn(["dependencies", "dep: hidden change"], added)

    def test_cleared_finding_edits_the_comment_and_removes_its_recorded_label(self):
        previous = dc.render_comment("dep-pin-drift", findings_1145())
        github = FakeGitHub(labels=["dep: pin drift"], comments=[bot_comment(11, previous)])
        findings = findings_1145()
        findings["checks"]["dep-pin-drift"]["drifts"] = []
        code, _ = self.run_post(github, findings)
        self.assertEqual(code, 0)
        self.assertIn(
            ("PATCH", f"repos/{REPO}/issues/comments/11"), [(m, p) for m, p, _ in github.writes()]
        )
        self.assertIn(
            ("DELETE", f"repos/{REPO}/issues/7/labels/dep%3A%20pin%20drift"),
            [(m, p) for m, p, _ in github.writes()],
        )

    def test_a_label_this_check_never_recorded_is_never_removed(self):
        # Dependabot labels its own pull requests "dependencies"; a dep-moves
        # comment that never applied it must not take it away.
        findings = findings_1145()
        findings["checks"]["dep-moves"]["moves"] = []
        previous = dc.render_comment("dep-moves", findings)
        github = FakeGitHub(labels=["dependencies"], comments=[bot_comment(11, previous)])
        self.run_post(github, findings)
        self.assertFalse([p for m, p, _ in github.writes() if m == "DELETE"])

    def test_a_recorded_label_is_removed_even_if_dependabot_also_applied_it(self):
        previous = dc.render_comment("dep-moves", findings_1145())
        github = FakeGitHub(
            labels=["dependencies", "dep: hidden change"], comments=[bot_comment(11, previous)]
        )
        findings = findings_1145()
        findings["checks"]["dep-moves"]["moves"] = []
        self.run_post(github, findings)
        removed = sorted(p.rsplit("/", 1)[1] for m, p, _ in github.writes() if m == "DELETE")
        self.assertEqual(removed, ["dep%3A%20hidden%20change", "dependencies"])

    def test_only_its_own_labels_are_removed_whatever_the_record_says(self):
        forged = (
            dc.comment_marker("dep-pin-drift") + '\n<!-- applied-labels: ["orchestrating"] -->\n'
        )
        github = FakeGitHub(labels=["orchestrating"], comments=[bot_comment(11, forged)])
        findings = findings_1145()
        findings["checks"]["dep-pin-drift"]["drifts"] = []
        self.run_post(github, findings)
        self.assertFalse([p for m, p, _ in github.writes() if m == "DELETE"])

    def test_a_marker_comment_by_someone_else_is_not_ours(self):
        planted = {
            "id": 5,
            "user": {"login": "someone"},
            "body": dc.comment_marker("dep-pin-drift"),
        }
        github = FakeGitHub(comments=[planted])
        self.run_post(github, findings_1145())
        self.assertNotIn(
            ("PATCH", f"repos/{REPO}/issues/comments/5"), [(m, p) for m, p, _ in github.writes()]
        )

    def test_unchanged_comment_is_not_rewritten(self):
        findings = findings_1145()
        comments = [
            bot_comment(20 + i, dc.render_comment(c, findings)) for i, c in enumerate(dc.CHECKS)
        ]
        labels = ["dependencies", "dep: hidden change", "dep: pin drift", "dep: compose misaligned"]
        github = FakeGitHub(labels=labels, comments=comments)
        self.assertEqual(self.run_post(github, findings)[0], 0)
        self.assertEqual(github.writes(), [])

    def test_skipped_run_writes_nothing_on_a_pull_request_never_reported(self):
        github = FakeGitHub(labels=["dependencies"])
        self.assertEqual(self.run_post(github, dc.skipped_findings())[0], 0)
        self.assertEqual(github.writes(), [])
        with open(self.output, encoding="utf-8") as f:
            self.assertEqual(f.read(), "status=skipped\n")

    def test_skipped_run_clears_an_earlier_report(self):
        previous = dc.render_comment("dep-compose-alignment", findings_1145())
        github = FakeGitHub(
            labels=["dep: compose misaligned"], comments=[bot_comment(11, previous)]
        )
        self.run_post(github, dc.skipped_findings())
        writes = [(m, p) for m, p, _ in github.writes()]
        self.assertEqual(
            writes,
            [
                ("PATCH", f"repos/{REPO}/issues/comments/11"),
                ("DELETE", f"repos/{REPO}/issues/7/labels/dep%3A%20compose%20misaligned"),
            ],
        )

    def test_failed_comment_write_leaves_labels_alone_and_fails(self):
        github = FakeGitHub(fail={("POST", f"repos/{REPO}/issues/7/comments")})
        code, log = self.run_post(github, findings_1145())
        self.assertEqual(code, 1)
        self.assertFalse([p for m, p, _ in github.writes() if "/labels" in p])
        self.assertIn("Leaving its labels unchanged", log)

    def test_failed_label_write_fails_after_trying_every_check(self):
        github = FakeGitHub(fail={("POST", f"repos/{REPO}/issues/7/labels")})
        code, _ = self.run_post(github, findings_1145())
        self.assertEqual(code, 1)
        posted = [p for m, p, _ in github.writes() if m == "POST" and p.endswith("/comments")]
        self.assertEqual(len(posted), len(dc.CHECKS))

    def test_no_pull_request_with_that_head_writes_nothing(self):
        github = FakeGitHub()
        github.pr["head"]["sha"] = "e" * 40
        code, log = self.run_post(github, findings_1145())
        self.assertEqual(code, 0)
        self.assertEqual(github.writes(), [])
        self.assertIn("Nothing to write", log)

    def test_invalid_findings_fail_before_any_call(self):
        github = FakeGitHub()
        findings = findings_1145()
        findings["checks"]["dep-pin-drift"]["drifts"][0]["library"] = "<script>"
        code, log = self.run_post(github, findings)
        self.assertEqual(code, 1)
        self.assertEqual(github.calls, [])
        self.assertIn("not usable", log)

    def test_missing_environment(self):
        with mock.patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(post.main(), 1)


if __name__ == "__main__":
    unittest.main()
