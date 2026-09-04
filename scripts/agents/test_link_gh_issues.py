#!/usr/bin/env python3
"""Unit tests for link_gh_issues.py.

No test here touches the network: `api` is replaced by a fake that records each
call and answers from an in-memory model of the two link families. Under test
are reference parsing, id resolution, the inversion of `--blocks` and
`--child-of` onto the other issue's endpoint, idempotence, the guards, and the
reporting.

GitHub serves no write for the `blocking` direction, so an inversion bug would
record a dependency backwards: right in the report, wrong in the sidebar.
"""

import ast
import contextlib
import io
import json
import os
import stat
import sys
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import link_gh_issues as lgi  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

OWNER = "aunger"
REPO = "gallery-button-for-pixel-camera"


def issue_payload(number, id_=None, title=None, state="open", pull_request=False):
    payload = {
        "number": number,
        "id": id_ if id_ is not None else 1000 + number,
        "title": title or f"Issue {number}",
        "state": state,
    }
    if pull_request:
        payload["pull_request"] = {"url": "https://example.invalid"}
    return payload


class FakeApi:
    """A stand-in for `link_gh_issues.api` backed by an in-memory link model.

    `links` is keyed by (family, owner, repo, number)--the holder of the
    link--and holds the set of linked issue ids, which is exactly the shape the
    real membership endpoints return.
    """

    def __init__(self, issues=None, links=None, errors=None, not_found=()):
        self.issues = issues or {}
        self.links = links or {}
        self.errors = errors or {}
        # (method, path) pairs the real API answers 404 for. `api` hands 404
        # back to its caller rather than raising, so every caller has to decide
        # what it means; these let the tests pin each of those decisions.
        self.not_found = set(not_found)
        self.calls = []

    def __call__(self, method, path, token, body=None):
        self.calls.append((method, path, body))

        for (err_method, err_path), error in self.errors.items():
            if err_method == method and err_path == path:
                raise error

        if (method, path.split("?")[0]) in self.not_found:
            return 404, {"message": "Not Found"}

        # GET /repos/{o}/{r}/issues/{n}
        parts = path.strip("/").split("/")
        if method == "GET" and len(parts) == 5 and parts[3] == "issues":
            key = (parts[1], parts[2], int(parts[4]))
            if key not in self.issues:
                return 404, {"message": "Not Found"}
            return 200, self.issues[key]

        # The two read-only views are derived from the same model, by looking
        # up the inverse of a stored link--which is exactly how GitHub
        # presents them, and keeps the `show` tests honest.
        inverse = self._inverse_view(method, path)
        if inverse is not None:
            return inverse

        family, holder, tail = self._route(method, path)
        if family is None:
            raise AssertionError(f"unexpected call: {method} {path}")

        members = self.links.setdefault((family,) + holder, set())
        if method == "GET":
            return 200, [self.issues[k] for k in self.issues if self.issues[k]["id"] in members]
        if method == "POST":
            if family == "dependency":
                members.add(body["issue_id"])
                return 201, {}
            return self._adopt(holder, members, body)
        if method == "DELETE":
            target = tail if tail is not None else body["sub_issue_id"]
            members.discard(target)
            return 204, None
        raise AssertionError(f"unexpected method {method}")

    def _adopt(self, holder, members, body):
        """POST .../sub_issues, enforcing GitHub's one-parent-per-issue rule.

        Without this the fake would accept a second parent that the real API
        refuses with 422, and the guard against silently moving a sub-issue
        would have nothing to fail against.
        """
        operand = body["sub_issue_id"]
        others = [
            key
            for key, ids in self.links.items()
            if key[0] == "sub-issue" and key[1:] != holder and operand in ids
        ]
        if others and not body.get("replace_parent"):
            raise lgi.ApiError(
                422,
                "HTTP 422: An error occurred while adding the sub-issue to the parent issue. "
                "Sub issue may only have one parent",
            )
        for key in others:
            self.links[key].discard(operand)
        members.add(operand)
        return 201, {}

    def _issue_by_id(self, id_):
        for payload in self.issues.values():
            if payload["id"] == id_:
                return payload
        return None

    def _holders_linking(self, family, target_id):
        """Every holder whose link set contains `target_id`, as issue payloads."""
        found = []
        for (link_family, owner, repo, number), members in self.links.items():
            if link_family == family and target_id in members:
                payload = self.issues.get((owner, repo, number))
                if payload is not None:
                    found.append(payload)
        return found

    def _inverse_view(self, method, path):
        """Answer the two endpoints that have no write side."""
        parts = path.split("?")[0].strip("/").split("/")
        if method != "GET" or len(parts) < 6 or parts[3] != "issues":
            return None
        subject = self.issues.get((parts[1], parts[2], int(parts[4])))
        rest = parts[5:]
        if rest[:2] == ["dependencies", "blocking"]:
            if subject is None:
                return 404, {"message": "Not Found"}
            return 200, self._holders_linking("dependency", subject["id"])
        if rest == ["parent"]:
            parents = self._holders_linking("sub-issue", subject["id"]) if subject else []
            if not parents:
                return 404, {"message": "No parent issue found"}
            return 200, parents[0]
        return None

    def _route(self, method, path):
        """Map a link endpoint onto (family, holder key, trailing id)."""
        parts = path.split("?")[0].strip("/").split("/")
        if len(parts) < 6 or parts[3] != "issues":
            return None, None, None
        holder = (parts[1], parts[2], int(parts[4]))
        rest = parts[5:]
        if rest[:2] == ["dependencies", "blocked_by"]:
            tail = int(rest[2]) if len(rest) > 2 else None
            return "dependency", holder, tail
        if rest[0] in ("sub_issues", "sub_issue"):
            return "sub-issue", holder, None
        return None, None, None


def run(argv, fake, token="t0ken"):
    """Run main() against the fake, returning (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with patch.object(lgi, "api", fake), patch.dict(os.environ, {"GITHUB_TOKEN": token}):
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = lgi.main(argv)
    return code, out.getvalue(), err.getvalue()


def two_issues():
    return {
        (OWNER, REPO, 42): issue_payload(42, id_=4200, title="Subject"),
        (OWNER, REPO, 17): issue_payload(17, id_=1700, title="Prerequisite"),
    }


def three_issues():
    """A subject and two candidate parents, for the one-parent-per-issue rule."""
    issues = two_issues()
    issues[(OWNER, REPO, 19)] = issue_payload(19, id_=1900, title="Other parent")
    return issues


class FakeResponse:
    """The context manager `_OPENER.open` returns, over a fixed body."""

    def __init__(self, status, body=""):
        self.status = status
        self._body = body.encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """A `_OPENER` stand-in that replays a script of answers and records the calls.

    Patching this rather than `urllib.request.urlopen` is the point: a change
    that went back to `urlopen` would follow the redirect these tests refuse.
    """

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def open(self, request, timeout=None):
        self.calls.append((request.get_method(), request.full_url))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def http_error(code, body="", headers=None):
    """The HTTPError urllib raises for a non-2xx, built for the tests."""
    return urllib.error.HTTPError(
        f"{lgi.API_ROOT}/x",
        code,
        "",
        headers if headers is not None else {},
        io.BytesIO(body.encode()),
    )


# ---------------------------------------------------------------------------
# Reference parsing
# ---------------------------------------------------------------------------


class TestParseRef(unittest.TestCase):
    def test_bare_number_inherits_the_subject_repository(self):
        self.assertEqual(lgi.parse_ref("123", OWNER, REPO), (OWNER, REPO, 123))

    def test_hash_number_inherits_the_subject_repository(self):
        self.assertEqual(lgi.parse_ref("#123", OWNER, REPO), (OWNER, REPO, 123))

    def test_qualified_reference_overrides_the_repository(self):
        self.assertEqual(lgi.parse_ref("octo/other#7", OWNER, REPO), ("octo", "other", 7))

    def test_issue_url_is_accepted(self):
        self.assertEqual(
            lgi.parse_ref("https://github.com/octo/other/issues/7", OWNER, REPO),
            ("octo", "other", 7),
        )

    def test_pull_request_url_is_accepted(self):
        self.assertEqual(
            lgi.parse_ref("https://github.com/octo/other/pull/9", OWNER, REPO),
            ("octo", "other", 9),
        )

    def test_surrounding_whitespace_is_ignored(self):
        self.assertEqual(lgi.parse_ref("  #8 ", OWNER, REPO), (OWNER, REPO, 8))

    def test_unparseable_reference_is_rejected(self):
        for ref in ("", "abc", "owner/repo", "#", "12a"):
            with self.subTest(ref=ref):
                with self.assertRaises(ValueError):
                    lgi.parse_ref(ref, OWNER, REPO)

    def test_a_repository_name_may_not_swallow_a_slash(self):
        """`a/b/c#1` is not owner `a` and repo `b/c`; it addresses nothing."""
        with self.assertRaises(ValueError):
            lgi.parse_ref("a/b/c#1", OWNER, REPO)

    def test_a_url_on_another_host_is_rejected(self):
        """API_ROOT is github.com, so another host's #7 is a different issue."""
        for ref in (
            "https://gitlab.example.com/octo/other/issues/7",
            "https://github.example.com/octo/other/issues/7",
        ):
            with self.subTest(ref=ref):
                with self.assertRaises(ValueError):
                    lgi.parse_ref(ref, OWNER, REPO)


# ---------------------------------------------------------------------------
# Adding links
# ---------------------------------------------------------------------------


class TestAdd(unittest.TestCase):
    def test_blocked_by_writes_to_the_subject_with_the_other_id(self):
        fake = FakeApi(two_issues())
        code, out, err = run(["add", OWNER, REPO, "42", "--blocked-by", "17"], fake)
        self.assertEqual(code, 0, err)
        self.assertIn(
            (
                "POST",
                f"/repos/{OWNER}/{REPO}/issues/42/dependencies/blocked_by",
                {"issue_id": 1700},
            ),
            fake.calls,
        )
        self.assertIn("Linked:", out)
        self.assertIn("is blocked by", out)

    def test_blocks_is_inverted_onto_the_other_issue(self):
        """`A blocks B` has no endpoint of its own; it is `B blocked_by A`."""
        fake = FakeApi(two_issues())
        code, out, err = run(["add", OWNER, REPO, "42", "--blocks", "17"], fake)
        self.assertEqual(code, 0, err)
        self.assertIn(
            (
                "POST",
                f"/repos/{OWNER}/{REPO}/issues/17/dependencies/blocked_by",
                {"issue_id": 4200},
            ),
            fake.calls,
        )
        # Reported from the subject's side, the way the caller asked for it.
        self.assertIn(f"{OWNER}/{REPO}#42 blocks {OWNER}/{REPO}#17", out)

    def test_parent_of_writes_the_child_id_to_the_subject(self):
        fake = FakeApi(two_issues())
        code, _, err = run(["add", OWNER, REPO, "42", "--parent-of", "17"], fake)
        self.assertEqual(code, 0, err)
        self.assertIn(
            ("POST", f"/repos/{OWNER}/{REPO}/issues/42/sub_issues", {"sub_issue_id": 1700}),
            fake.calls,
        )

    def test_child_of_is_inverted_onto_the_parent(self):
        fake = FakeApi(two_issues())
        code, _, err = run(["add", OWNER, REPO, "42", "--child-of", "17"], fake)
        self.assertEqual(code, 0, err)
        self.assertIn(
            ("POST", f"/repos/{OWNER}/{REPO}/issues/17/sub_issues", {"sub_issue_id": 4200}),
            fake.calls,
        )

    def test_several_links_in_one_call_all_apply(self):
        issues = two_issues()
        issues[(OWNER, REPO, 19)] = issue_payload(19, id_=1900)
        fake = FakeApi(issues)
        code, out, err = run(
            ["add", OWNER, REPO, "42", "--blocked-by", "17", "--blocked-by", "19"], fake
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(out.count("Linked:"), 2)

    def test_cross_repository_reference_targets_the_other_repository(self):
        issues = two_issues()
        issues[("octo", "other", 7)] = issue_payload(7, id_=700)
        fake = FakeApi(issues)
        code, _, err = run(["add", OWNER, REPO, "42", "--blocked-by", "octo/other#7"], fake)
        self.assertEqual(code, 0, err)
        self.assertIn(
            ("POST", f"/repos/{OWNER}/{REPO}/issues/42/dependencies/blocked_by", {"issue_id": 700}),
            fake.calls,
        )

    def test_an_existing_link_is_reported_and_not_rewritten(self):
        fake = FakeApi(two_issues(), links={("dependency", OWNER, REPO, 42): {1700}})
        code, out, err = run(["add", OWNER, REPO, "42", "--blocked-by", "17"], fake)
        self.assertEqual(code, 0, err)
        self.assertIn("Already linked:", out)
        self.assertFalse([c for c in fake.calls if c[0] == "POST"])

    def test_a_reference_used_twice_is_resolved_once(self):
        fake = FakeApi(two_issues())
        run(["add", OWNER, REPO, "42", "--blocked-by", "17", "--blocks", "17"], fake)
        lookups = [c for c in fake.calls if c == ("GET", f"/repos/{OWNER}/{REPO}/issues/17", None)]
        self.assertEqual(len(lookups), 1)


# ---------------------------------------------------------------------------
# Removing links
# ---------------------------------------------------------------------------


class TestRemove(unittest.TestCase):
    def test_dependency_removal_puts_the_id_in_the_path(self):
        fake = FakeApi(two_issues(), links={("dependency", OWNER, REPO, 42): {1700}})
        code, out, err = run(["remove", OWNER, REPO, "42", "--blocked-by", "17"], fake)
        self.assertEqual(code, 0, err)
        self.assertIn(
            ("DELETE", f"/repos/{OWNER}/{REPO}/issues/42/dependencies/blocked_by/1700", None),
            fake.calls,
        )
        self.assertIn("Unlinked:", out)

    def test_sub_issue_removal_puts_the_id_in_the_body(self):
        """The two families disagree here: path parameter vs request body."""
        fake = FakeApi(two_issues(), links={("sub-issue", OWNER, REPO, 42): {1700}})
        code, _, err = run(["remove", OWNER, REPO, "42", "--parent-of", "17"], fake)
        self.assertEqual(code, 0, err)
        self.assertIn(
            ("DELETE", f"/repos/{OWNER}/{REPO}/issues/42/sub_issue", {"sub_issue_id": 1700}),
            fake.calls,
        )

    def test_removing_an_absent_link_is_success(self):
        fake = FakeApi(two_issues())
        code, out, err = run(["remove", OWNER, REPO, "42", "--blocked-by", "17"], fake)
        self.assertEqual(code, 0, err)
        self.assertIn("Already unlinked:", out)
        self.assertFalse([c for c in fake.calls if c[0] == "DELETE"])

    def test_remove_is_inverted_for_blocks_just_as_add_is(self):
        fake = FakeApi(two_issues(), links={("dependency", OWNER, REPO, 17): {4200}})
        code, _, err = run(["remove", OWNER, REPO, "42", "--blocks", "17"], fake)
        self.assertEqual(code, 0, err)
        self.assertIn(
            ("DELETE", f"/repos/{OWNER}/{REPO}/issues/17/dependencies/blocked_by/4200", None),
            fake.calls,
        )

    def test_add_then_remove_returns_to_the_starting_state(self):
        fake = FakeApi(two_issues())
        run(["add", OWNER, REPO, "42", "--blocked-by", "17"], fake)
        run(["remove", OWNER, REPO, "42", "--blocked-by", "17"], fake)
        self.assertEqual(fake.links[("dependency", OWNER, REPO, 42)], set())


# ---------------------------------------------------------------------------
# Guards and failures
# ---------------------------------------------------------------------------


class TestGuards(unittest.TestCase):
    def test_linking_an_issue_to_itself_is_refused(self):
        fake = FakeApi(two_issues())
        code, _, err = run(["add", OWNER, REPO, "42", "--blocked-by", "42"], fake)
        self.assertEqual(code, 1)
        self.assertIn("itself", err)
        self.assertFalse([c for c in fake.calls if c[0] == "POST"])

    def test_a_pull_request_is_refused_in_every_position_of_both_families(self):
        """GitHub takes issues only, on both sides of both link types.

        All four positions answer 422, so the guard cannot be per-family.
        """
        cases = [
            ("--blocked-by", "17"),
            ("--blocks", "17"),
            ("--parent-of", "17"),
            ("--child-of", "17"),
        ]
        for flag, ref in cases:
            with self.subTest(flag=flag):
                issues = two_issues()
                issues[(OWNER, REPO, 17)] = issue_payload(17, id_=1700, pull_request=True)
                fake = FakeApi(issues)
                code, _, err = run(["add", OWNER, REPO, "42", flag, ref], fake)
                self.assertEqual(code, 1)
                self.assertIn("pull request", err)
                self.assertFalse([c for c in fake.calls if c[0] == "POST"])

    def test_a_pull_request_as_the_subject_is_refused_too(self):
        """The subject side matters: a PR cannot be blocked by anything."""
        issues = two_issues()
        issues[(OWNER, REPO, 42)] = issue_payload(42, id_=4200, pull_request=True)
        fake = FakeApi(issues)
        code, _, err = run(["add", OWNER, REPO, "42", "--blocked-by", "17"], fake)
        self.assertEqual(code, 1)
        self.assertIn("pull request", err)
        self.assertFalse([c for c in fake.calls if c[0] == "POST"])

    def test_the_pull_request_refusal_names_the_documented_fallback(self):
        """verification_planning.md tells an agent to fall back to the issue the
        PR resolves, so the error points there rather than only at `Fixes #N`."""
        issues = two_issues()
        issues[(OWNER, REPO, 17)] = issue_payload(17, id_=1700, pull_request=True)
        code, _, err = run(["add", OWNER, REPO, "42", "--blocked-by", "17"], FakeApi(issues))
        self.assertEqual(code, 1)
        self.assertIn("Link the issue the pull request resolves", err)
        self.assertIn("Fixes #N", err)

    def test_no_relation_flag_is_an_error(self):
        fake = FakeApi(two_issues())
        code, _, err = run(["add", OWNER, REPO, "42"], fake)
        self.assertEqual(code, 1)
        self.assertIn("--blocked-by", err)

    def test_missing_token_is_reported_before_any_call(self):
        fake = FakeApi(two_issues())
        code, _, err = run(["add", OWNER, REPO, "42", "--blocked-by", "17"], fake, token="")
        self.assertEqual(code, 1)
        self.assertIn("GITHUB_TOKEN", err)
        self.assertEqual(fake.calls, [])

    def test_an_unknown_issue_is_reported_and_fails(self):
        fake = FakeApi(two_issues())
        code, _, err = run(["add", OWNER, REPO, "42", "--blocked-by", "999"], fake)
        self.assertEqual(code, 1)
        self.assertIn("not found", err)

    def test_one_bad_reference_does_not_stop_the_others(self):
        issues = two_issues()
        issues[(OWNER, REPO, 19)] = issue_payload(19, id_=1900)
        fake = FakeApi(issues)
        code, out, err = run(
            ["add", OWNER, REPO, "42", "--blocked-by", "nonsense", "--blocked-by", "19"], fake
        )
        self.assertEqual(code, 1)
        self.assertIn("cannot parse", err)
        self.assertIn("Linked:", out)

    def test_dry_run_writes_nothing_but_reports_the_change(self):
        fake = FakeApi(two_issues())
        code, out, err = run(["add", OWNER, REPO, "42", "--blocked-by", "17", "--dry-run"], fake)
        self.assertEqual(code, 0, err)
        self.assertIn("Would link:", out)
        self.assertFalse([c for c in fake.calls if c[0] in ("POST", "DELETE")])

    def test_a_403_leads_with_the_missing_link_not_the_token(self):
        """GitHub answers 403 for a link that is not there, which reads as a
        permission problem and is not one. Getting this backwards sends the
        reader off to mint a token they already have, so the order is tested."""
        fake = FakeApi(
            two_issues(),
            errors={
                ("POST", f"/repos/{OWNER}/{REPO}/issues/42/dependencies/blocked_by"): lgi.ApiError(
                    403,
                    lgi._describe_failure(
                        403, {"message": "Resource not accessible by integration"}, ""
                    ),
                )
            },
        )
        code, _, err = run(["add", OWNER, REPO, "42", "--blocked-by", "17"], fake)
        self.assertEqual(code, 1)
        self.assertIn("does not exist", err)
        self.assertIn("403 (not 404)", err)
        # The permission cause is still offered, but second.
        self.assertIn("issues: write", err)
        self.assertLess(err.index("does not exist"), err.index("issues: write"))


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


class TestShow(unittest.TestCase):
    def _fake_with_a_dependency(self):
        """#42 blocked by #17, and deliberately no parent and no sub-issues."""
        issues = two_issues()
        fake = FakeApi(issues, links={("dependency", OWNER, REPO, 42): {1700}})
        return fake

    def test_show_lists_each_relation(self):
        code, out, err = run(["show", OWNER, REPO, "42"], self._fake_with_a_dependency())
        self.assertEqual(code, 0, err)
        self.assertIn("Blocked by (1):", out)
        self.assertIn(f"{OWNER}/{REPO}#42: Subject", out)
        self.assertIn("Blocking: none", out)
        self.assertIn("Sub-issues: none", out)

    def test_show_reports_a_parentless_issue_as_none(self):
        """GitHub answers `GET .../parent` with 404 when there is no parent."""
        code, out, err = run(["show", OWNER, REPO, "42"], self._fake_with_a_dependency())
        self.assertEqual(code, 0, err)
        self.assertIn("Parent: none", out)

    def test_show_json_is_parseable_and_carries_every_relation(self):
        code, out, err = run(["show", OWNER, REPO, "42", "--json"], self._fake_with_a_dependency())
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["issue"], f"{OWNER}/{REPO}#42")
        self.assertEqual([i["number"] for i in payload["blocked_by"]], [17])
        self.assertIsNone(payload["parent"])
        self.assertEqual(payload["sub_issues"], [])

    def test_show_reports_the_blocking_direction_from_the_stored_inverse(self):
        """#42 blocking #17 is stored as #17 blocked_by #42; show reads it back."""
        fake = FakeApi(two_issues(), links={("dependency", OWNER, REPO, 17): {4200}})
        code, out, err = run(["show", OWNER, REPO, "42"], fake)
        self.assertEqual(code, 0, err)
        self.assertIn("Blocking (1):", out)
        self.assertIn("#17", out)
        self.assertIn("Blocked by: none", out)

    def test_show_names_the_parent_when_the_issue_is_a_sub_issue(self):
        fake = FakeApi(two_issues(), links={("sub-issue", OWNER, REPO, 17): {4200}})
        code, out, err = run(["show", OWNER, REPO, "42"], fake)
        self.assertEqual(code, 0, err)
        self.assertIn("Parent:", out)
        self.assertNotIn("Parent (1):", out)
        self.assertIn("Prerequisite", out)

    def test_show_lists_sub_issues_of_a_parent(self):
        fake = FakeApi(two_issues(), links={("sub-issue", OWNER, REPO, 42): {1700}})
        code, out, err = run(["show", OWNER, REPO, "42"], fake)
        self.assertEqual(code, 0, err)
        self.assertIn("Sub-issues (1):", out)

    def test_show_makes_no_write_call(self):
        fake = self._fake_with_a_dependency()
        run(["show", OWNER, REPO, "42"], fake)
        self.assertFalse([c for c in fake.calls if c[0] in ("POST", "DELETE", "PATCH")])

    def test_issue_line_names_the_repository_for_a_cross_repo_link(self):
        line = lgi.issue_line(
            {"number": 7, "title": "Other", "state": "open", "repository": {"full_name": "o/r"}}
        )
        self.assertIn("o/r#7", line)

    def test_issue_line_truncates_a_long_title(self):
        line = lgi.issue_line({"number": 7, "title": "x" * 200, "state": "open"})
        self.assertLess(len(line), 100)
        self.assertIn("...", line)


# ---------------------------------------------------------------------------
# The relation table, and the file itself
# ---------------------------------------------------------------------------


class TestWritesAreNotAssumed(unittest.TestCase):
    """`api` returns 404 rather than raising, so each caller has to read it.

    Getting this wrong prints `Linked:` and exits 0 for a write that never
    happened.
    """

    def test_a_post_that_404s_is_a_failure_not_a_link(self):
        fake = FakeApi(
            two_issues(),
            not_found=[("POST", f"/repos/{OWNER}/{REPO}/issues/42/dependencies/blocked_by")],
        )
        code, out, err = run(["add", OWNER, REPO, "42", "--blocked-by", "17"], fake)
        self.assertEqual(code, 1)
        self.assertNotIn("Linked:", out)
        self.assertIn("nothing was written", err)

    def test_a_delete_that_404s_still_reaches_the_goal_state(self):
        """The goal is the link being gone, and a 404 means it is."""
        fake = FakeApi(
            two_issues(),
            links={("dependency", OWNER, REPO, 42): {1700}},
            not_found=[("DELETE", f"/repos/{OWNER}/{REPO}/issues/42/dependencies/blocked_by/1700")],
        )
        code, out, err = run(["remove", OWNER, REPO, "42", "--blocked-by", "17"], fake)
        self.assertEqual(code, 0, err)
        self.assertIn("Unlinked:", out)

    def test_an_unreadable_membership_endpoint_is_not_read_as_no_links(self):
        """Otherwise `remove` reports `Already unlinked` having checked nothing."""
        fake = FakeApi(
            two_issues(),
            not_found=[("GET", f"/repos/{OWNER}/{REPO}/issues/42/dependencies/blocked_by")],
        )
        code, out, err = run(["remove", OWNER, REPO, "42", "--blocked-by", "17"], fake)
        self.assertEqual(code, 1)
        self.assertNotIn("Already unlinked", out)
        self.assertIn("links are unknown", err)

    def test_a_membership_read_that_is_not_a_list_is_not_read_as_no_links(self):
        """A 200 carrying an object reads as "no links" as blindly as a 404.

        `missing_ok` has to cover both, not only the status code.
        """

        def api(method, path, token, body=None):
            head = path.split("?")[0]
            for number, id_ in ((42, 4200), (17, 1700)):
                if head.endswith(f"/issues/{number}"):
                    return 200, issue_payload(number, id_=id_)
            return 200, {"message": "unexpected object where a list belongs"}

        code, out, err = run(["remove", OWNER, REPO, "42", "--blocked-by", "17"], api)
        self.assertEqual(code, 1)
        self.assertNotIn("Already unlinked", out)
        self.assertIn("links are unknown", err)

    def test_show_still_reads_a_404_membership_endpoint_as_none(self):
        """`show` keeps the lenient reading: there, none is a real answer."""
        fake = FakeApi(
            two_issues(),
            not_found=[("GET", f"/repos/{OWNER}/{REPO}/issues/42/sub_issues")],
        )
        code, out, err = run(["show", OWNER, REPO, "42"], fake)
        self.assertEqual(code, 0, err)
        self.assertIn("Sub-issues: none", out)

    def test_an_issue_payload_without_an_id_is_reported_not_raised(self):
        fake = FakeApi({(OWNER, REPO, 42): {"number": 42, "title": "No id"}})
        code, _, err = run(["show", OWNER, REPO, "42"], fake)
        self.assertEqual(code, 1)
        self.assertIn("no issue id", err)

    def test_a_post_is_never_retried(self):
        """A POST that reached GitHub and lost its response would come back a
        duplicate, turning a link that was created into a reported failure."""
        self.assertNotIn("POST", lgi.IDEMPOTENT_METHODS)
        self.assertEqual(sorted(lgi.IDEMPOTENT_METHODS), ["DELETE", "GET"])


class TestRepeatedReference(unittest.TestCase):
    """The membership pre-read is cached, so it has to follow the writes.

    A stale cache re-sends the write for a reference named twice in one call,
    reporting the refusal as a failure after the goal state was reached.
    """

    def test_the_same_reference_added_twice_is_written_once(self):
        fake = FakeApi(two_issues())
        code, out, err = run(
            ["add", OWNER, REPO, "42", "--blocked-by", "17", "--blocked-by", "#17"], fake
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(len([c for c in fake.calls if c[0] == "POST"]), 1)
        self.assertIn("Already linked:", out)

    def test_the_same_reference_removed_twice_is_deleted_once(self):
        fake = FakeApi(two_issues(), links={("dependency", OWNER, REPO, 42): {1700}})
        code, out, err = run(
            ["remove", OWNER, REPO, "42", "--blocked-by", "17", "--blocked-by", "#17"], fake
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(len([c for c in fake.calls if c[0] == "DELETE"]), 1)
        self.assertIn("Already unlinked:", out)

    def test_a_reference_to_the_subject_does_not_refetch_it(self):
        fake = FakeApi(two_issues())
        code, _, _ = run(["add", OWNER, REPO, "42", "--blocked-by", "42"], fake)
        self.assertEqual(code, 1)
        lookups = [c for c in fake.calls if c == ("GET", f"/repos/{OWNER}/{REPO}/issues/42", None)]
        self.assertEqual(len(lookups), 1)


class TestOneParent(unittest.TestCase):
    """An issue gets one parent, so a sub-issue add can remove one.

    GitHub refuses the second parent with 422 unless the body carries
    `replace_parent`, so the move is the caller's to ask for.
    """

    def child_of_17(self):
        """#42 is already a sub-issue of #17."""
        return FakeApi(three_issues(), links={("sub-issue", OWNER, REPO, 17): {4200}})

    def posts(self, fake):
        return [c for c in fake.calls if c[0] == "POST"]

    def test_moving_a_sub_issue_is_refused_without_the_flag(self):
        fake = self.child_of_17()
        code, out, err = run(["add", OWNER, REPO, "42", "--child-of", "19"], fake)
        self.assertEqual(code, 1)
        self.assertNotIn("Linked:", out)
        self.assertIn("--replace-parent", err)
        self.assertIn("one parent", err)

    def test_the_refusal_names_the_issue_about_to_lose_the_child(self):
        """GitHub's own 422 does not say which parent, which is the whole point
        of reading it first."""
        fake = self.child_of_17()
        code, _, err = run(["add", OWNER, REPO, "42", "--child-of", "19"], fake)
        self.assertEqual(code, 1)
        self.assertIn(f"{OWNER}/{REPO}#17", err)

    def test_the_parent_slug_uses_the_repository_the_payload_names(self):
        """A parent in another repository must not be reported as a local one."""
        fake = self.child_of_17()
        fake.issues[(OWNER, REPO, 17)] = dict(
            fake.issues[(OWNER, REPO, 17)], repository={"full_name": "octo/other"}
        )
        code, _, err = run(["add", OWNER, REPO, "42", "--child-of", "19"], fake)
        self.assertEqual(code, 1)
        self.assertIn("octo/other#17", err)

    def test_a_refused_move_writes_nothing(self):
        """The point of the pre-read is that the 422 never has to happen."""
        fake = self.child_of_17()
        run(["add", OWNER, REPO, "42", "--child-of", "19"], fake)
        self.assertEqual(self.posts(fake), [])
        self.assertEqual(fake.links[("sub-issue", OWNER, REPO, 17)], {4200})

    def test_parent_of_is_guarded_from_the_uninverted_side_too(self):
        """`--parent-of` puts the operand in the other position; same rule."""
        fake = self.child_of_17()
        code, _, err = run(["add", OWNER, REPO, "19", "--parent-of", "42"], fake)
        self.assertEqual(code, 1)
        self.assertIn("--replace-parent", err)
        self.assertEqual(self.posts(fake), [])

    def test_replace_parent_moves_it_and_says_so(self):
        fake = self.child_of_17()
        code, out, err = run(
            ["add", OWNER, REPO, "42", "--child-of", "19", "--replace-parent"], fake
        )
        self.assertEqual(code, 0, err)
        self.assertIn("Linked:", out)
        self.assertIn(f"replacing {OWNER}/{REPO}#17 as its parent", out)
        self.assertEqual(self.posts(fake)[0][2], {"sub_issue_id": 4200, "replace_parent": True})
        self.assertEqual(fake.links[("sub-issue", OWNER, REPO, 19)], {4200})
        self.assertEqual(fake.links[("sub-issue", OWNER, REPO, 17)], set())

    def test_a_parentless_operand_is_linked_without_asking_to_replace(self):
        """`replace_parent` is for the move; a plain adoption must not send it."""
        fake = FakeApi(three_issues())
        code, out, err = run(["add", OWNER, REPO, "42", "--child-of", "19"], fake)
        self.assertEqual(code, 0, err)
        self.assertIn("Linked:", out)
        self.assertNotIn("replacing", out)
        self.assertEqual(self.posts(fake)[0][2], {"sub_issue_id": 4200})

    def test_re_adding_the_parent_it_already_has_is_not_a_move(self):
        fake = self.child_of_17()
        code, out, err = run(["add", OWNER, REPO, "42", "--child-of", "17"], fake)
        self.assertEqual(code, 0, err)
        self.assertIn("Already linked:", out)
        self.assertNotIn("replacing", out)
        self.assertEqual(self.posts(fake), [])

    def test_a_dependency_add_never_reads_the_parent(self):
        """Only the sub-issue family has the rule, so only it pays for the read."""
        fake = FakeApi(two_issues())
        code, _, err = run(["add", OWNER, REPO, "42", "--blocked-by", "17"], fake)
        self.assertEqual(code, 0, err)
        self.assertEqual([c for c in fake.calls if c[1].endswith("/parent")], [])

    def unreadable_parent(self, payload):
        """A fake whose `/parent` answers 200 with something that is not an issue."""

        class Fake(FakeApi):
            def __call__(self, method, path, token, body=None):
                if method == "GET" and path.split("?")[0].endswith("/issues/42/parent"):
                    self.calls.append((method, path, body))
                    return 200, payload
                return super().__call__(method, path, token, body)

        return Fake(three_issues(), links={("sub-issue", OWNER, REPO, 17): {4200}})

    def test_an_unreadable_parent_is_not_read_as_no_parent(self):
        """The pre-read gates a write, so it must not invent an answer.

        Reporting "no parent" here writes without `replace_parent`, discarding
        the caller's `--replace-parent`.
        """
        for payload in (["not", "a", "dict"], {"number": 17}, {"id": 1700}, None):
            with self.subTest(payload=payload):
                fake = self.unreadable_parent(payload)
                code, out, err = run(
                    ["add", OWNER, REPO, "42", "--child-of", "19", "--replace-parent"], fake
                )
                self.assertEqual(code, 1)
                self.assertIn("already has a parent is unknown", err)
                self.assertNotIn("Linked:", out)
                self.assertEqual(self.posts(fake), [])

    def test_a_404_parent_is_still_read_as_no_parent(self):
        """`get_issue` has already proved the operand exists and is visible, so
        the only cause left for a 404 is GitHub's own "No parent issue found",
        which is what the fake answers for #42, a fixture issue with no parent."""
        fake = FakeApi(three_issues())
        code, out, err = run(
            ["add", OWNER, REPO, "42", "--child-of", "19", "--replace-parent"], fake
        )
        self.assertEqual(code, 0, err)
        self.assertIn("Linked:", out)
        self.assertNotIn("replacing", out)

    def test_the_flag_is_sent_exactly_when_a_parent_was_displaced(self):
        """Sending it whenever the flag is present would turn a pre-read that
        missed a parent into a silent move; the 422 is the safer failure."""
        moved = self.child_of_17()
        code, _, err = run(
            ["add", OWNER, REPO, "42", "--child-of", "19", "--replace-parent"], moved
        )
        self.assertEqual(code, 0, err)
        self.assertTrue(self.posts(moved)[0][2].get("replace_parent"))

        fresh = FakeApi(three_issues())
        code, _, err = run(
            ["add", OWNER, REPO, "42", "--child-of", "19", "--replace-parent"], fresh
        )
        self.assertEqual(code, 0, err)
        self.assertNotIn("replace_parent", self.posts(fresh)[0][2])

    def test_the_parent_is_read_once_and_then_followed(self):
        """Two moves of one issue in a call: the second must see the first."""
        fake = self.child_of_17()
        code, out, err = run(
            ["add", OWNER, REPO, "42", "--child-of", "19", "--child-of", "17", "--replace-parent"],
            fake,
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(len([c for c in fake.calls if c[1].endswith("/parent")]), 1)
        self.assertEqual(len(self.posts(fake)), 2)
        # The second write moves it back, so it must ask to replace as well.
        self.assertTrue(all(c[2].get("replace_parent") for c in self.posts(fake)))
        self.assertEqual(fake.links[("sub-issue", OWNER, REPO, 17)], {4200})
        self.assertEqual(fake.links[("sub-issue", OWNER, REPO, 19)], set())

    def test_removing_a_parent_lets_the_next_add_proceed_without_the_flag(self):
        fake = self.child_of_17()
        code, out, err = run(["remove", OWNER, REPO, "42", "--child-of", "17"], fake)
        self.assertEqual(code, 0, err)
        code, out, err = run(["add", OWNER, REPO, "42", "--child-of", "19"], fake)
        self.assertEqual(code, 0, err)
        self.assertIn("Linked:", out)

    def test_dry_run_previews_the_move_without_writing(self):
        fake = self.child_of_17()
        code, out, err = run(
            ["add", OWNER, REPO, "42", "--child-of", "19", "--replace-parent", "--dry-run"], fake
        )
        self.assertEqual(code, 0, err)
        self.assertIn("Would link:", out)
        self.assertIn(f"replacing {OWNER}/{REPO}#17 as its parent", out)
        self.assertEqual(self.posts(fake), [])

    def test_dry_run_refuses_the_move_it_would_refuse_for_real(self):
        fake = self.child_of_17()
        code, _, err = run(["add", OWNER, REPO, "42", "--child-of", "19", "--dry-run"], fake)
        self.assertEqual(code, 1)
        self.assertIn("--replace-parent", err)


class TestDryRunPreviewsTheRealRun(unittest.TestCase):
    """A preview that does not match the run it previews is worse than none."""

    def test_a_reference_named_twice_previews_as_it_would_run(self):
        fake = FakeApi(two_issues())
        code, out, err = run(
            ["add", OWNER, REPO, "42", "--blocked-by", "17", "--blocked-by", "#17", "--dry-run"],
            fake,
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(out.count("Would link:"), 1)
        self.assertIn("Already linked:", out)
        self.assertFalse([c for c in fake.calls if c[0] in ("POST", "DELETE")])

    def test_a_reference_removed_twice_previews_as_it_would_run(self):
        fake = FakeApi(two_issues(), links={("dependency", OWNER, REPO, 42): {1700}})
        code, out, err = run(
            ["remove", OWNER, REPO, "42", "--blocked-by", "17", "--blocked-by", "17", "--dry-run"],
            fake,
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(out.count("Would unlink:"), 1)
        self.assertIn("Already unlinked:", out)


class TestReportOrder(unittest.TestCase):
    def test_the_report_follows_the_order_the_flags_were_typed(self):
        """One list per flag would order the report by RELATIONS instead."""
        fake = FakeApi(three_issues())
        code, out, err = run(
            ["add", OWNER, REPO, "42", "--parent-of", "19", "--blocked-by", "17"], fake
        )
        self.assertEqual(code, 0, err)
        self.assertLess(out.index("#19"), out.index("#17"))

    def test_the_reverse_order_reports_in_reverse(self):
        fake = FakeApi(three_issues())
        code, out, err = run(
            ["add", OWNER, REPO, "42", "--blocked-by", "17", "--parent-of", "19"], fake
        )
        self.assertEqual(code, 0, err)
        self.assertLess(out.index("#17"), out.index("#19"))


class TestRetryDelay(unittest.TestCase):
    """Retrying on a flat delay while GitHub asks for longer earns the next 429."""

    def test_retry_after_is_honored(self):
        self.assertEqual(lgi._retry_delay({"Retry-After": "30"}), 30.0)

    def test_a_missing_or_unparseable_header_falls_back_to_the_default(self):
        for headers in ({}, None, {"Retry-After": "in a bit"}):
            with self.subTest(headers=headers):
                self.assertEqual(lgi._retry_delay(headers), lgi.RETRY_DELAY_SECONDS)

    def test_a_shorter_retry_after_does_not_shorten_the_default(self):
        self.assertEqual(lgi._retry_delay({"Retry-After": "0"}), lgi.RETRY_DELAY_SECONDS)

    def test_a_wait_past_the_cap_declines_the_retry(self):
        """Sleeping the cap and retrying anyway just earns the same refusal."""
        self.assertIsNone(lgi._retry_delay({"Retry-After": "3600"}))

    def test_a_primary_limit_reset_is_read_as_a_timestamp(self):
        """A reset is absolute, so the wait is its distance from now."""
        soon = str(int(time.time()) + 10)
        delay = lgi._retry_delay({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": soon})
        self.assertIsNotNone(delay)
        self.assertGreater(delay, lgi.RETRY_DELAY_SECONDS)
        self.assertLessEqual(delay, 10.0)

    def test_a_distant_reset_declines_the_retry(self):
        far = str(int(time.time()) + 3600)
        self.assertIsNone(
            lgi._retry_delay({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": far})
        )

    def test_a_reset_with_quota_left_is_not_a_wait(self):
        """The reset rides on every response; only a spent quota makes it one."""
        headers = {
            "X-RateLimit-Remaining": "4999",
            "X-RateLimit-Reset": str(int(time.time()) + 3600),
        }
        self.assertEqual(lgi._retry_delay(headers), lgi.RETRY_DELAY_SECONDS)


class TestRedirect(unittest.TestCase):
    """urllib turns a redirected POST into a GET, so a write must refuse one."""

    def test_a_redirected_write_is_a_failure_not_a_link(self):
        """The refusal lands on the POST, which is the call a rename rewrites."""

        def api(method, path, token, body=None):
            if method == "POST":
                raise lgi.ApiError(301, lgi._describe_failure(301, {"message": "Moved"}, ""))
            if path.endswith("/issues/42"):
                return 200, issue_payload(42, id_=4200)
            if path.endswith("/issues/17"):
                return 200, issue_payload(17, id_=1700)
            return 200, []

        out, err = io.StringIO(), io.StringIO()
        with patch.object(lgi, "api", api), patch.dict(os.environ, {"GITHUB_TOKEN": "t"}):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = lgi.main(["add", OWNER, REPO, "42", "--blocked-by", "17"])
        self.assertEqual(code, 1)
        self.assertNotIn("Linked:", out.getvalue())
        self.assertIn("renamed", err.getvalue())

    def test_the_opener_refuses_to_follow(self):
        handler = lgi._NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 301, "", {}, "http://x/"))

    def test_the_installed_handler_raises_rather_than_returning_the_redirect(self):
        """Returning None from `redirect_request` has to end as an error.

        The refusal is only worth anything if urllib turns it into an
        HTTPError; were the 301 handed back instead, `api` would read it as a
        response and a write would report success having written nothing.
        """
        self.assertTrue(any(isinstance(h, lgi._NoRedirect) for h in lgi._OPENER.handlers))
        request = urllib.request.Request(f"{lgi.API_ROOT}/repos/o/r/issues/1")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            lgi._OPENER.error(
                "http",
                request,
                io.BytesIO(b""),
                301,
                "Moved Permanently",
                {"location": f"{lgi.API_ROOT}/repos/new/name/issues/1"},
            )
        self.assertEqual(caught.exception.code, 301)

    def test_api_calls_through_the_opener_and_reports_the_rename(self):
        """`urlopen` would follow the redirect; only `_OPENER` refuses it."""
        opener = FakeOpener(http_error(301, '{"message": "Moved Permanently"}'))
        with patch.object(lgi, "_OPENER", opener):
            with self.assertRaises(lgi.ApiError) as caught:
                lgi.api("POST", "/repos/o/r/issues/1/dependencies/blocked_by", "t", {"issue_id": 2})
        self.assertEqual([method for method, _ in opener.calls], ["POST"])
        self.assertEqual(caught.exception.status, 301)
        self.assertIn("renamed", caught.exception.message)


class TestRetryLoop(unittest.TestCase):
    """What `api` does with the one retry a 5xx and a rate limit are worth."""

    def test_a_500_carrying_a_distant_reset_is_still_retried(self):
        """`X-RateLimit-Reset` is on every response, so it must not cancel this."""
        headers = {
            "X-RateLimit-Remaining": "4999",
            "X-RateLimit-Reset": str(int(time.time()) + 3600),
        }
        opener = FakeOpener(http_error(500, "", headers), FakeResponse(200, '{"id": 1}'))
        with patch.object(lgi, "_OPENER", opener), patch("time.sleep") as slept:
            self.assertEqual(lgi.api("GET", "/repos/o/r/issues/1", "t"), (200, {"id": 1}))
        self.assertEqual(len(opener.calls), 2)
        self.assertEqual(slept.call_args[0][0], lgi.RETRY_DELAY_SECONDS)

    def test_a_spent_quota_resetting_in_an_hour_is_not_slept_through(self):
        headers = {
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(int(time.time()) + 3600),
        }
        opener = FakeOpener(http_error(403, '{"message": "rate limit exceeded"}', headers))
        with patch.object(lgi, "_OPENER", opener), patch("time.sleep") as slept:
            with self.assertRaises(lgi.ApiError) as caught:
                lgi.api("GET", "/repos/o/r/issues/1", "t")
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(len(opener.calls), 1)
        slept.assert_not_called()


class TestPagination(unittest.TestCase):
    """`paged` walks pages, and a later page it cannot read is truncation."""

    def _api(self, pages):
        seen = []

        def api(method, path, token, body=None):
            page = int(path.split("&page=")[1])
            seen.append(page)
            return pages[page - 1] if page <= len(pages) else (200, [])

        return api, seen

    def test_every_page_is_walked_until_a_short_one(self):
        full = [{"id": n} for n in range(100)]
        api, seen = self._api([(200, full), (200, [{"id": 999}])])
        with patch.object(lgi, "api", api):
            items = lgi.paged("/p", "t")
        self.assertEqual(len(items), 101)
        self.assertEqual(seen, [1, 2])

    def test_an_unreadable_later_page_raises_rather_than_truncating(self):
        full = [{"id": n} for n in range(100)]
        api, _ = self._api([(200, full), (404, None)])
        with patch.object(lgi, "api", api):
            with self.assertRaises(lgi.ApiError) as caught:
                lgi.paged("/p", "t")
        self.assertIn("page 2", caught.exception.message)


class TestShowCounts(unittest.TestCase):
    """A count above the rows beneath it names links `show` never printed."""

    def test_an_entry_that_is_not_an_issue_is_not_counted(self):
        blocked_by = f"/repos/{OWNER}/{REPO}/issues/42/dependencies/blocked_by"

        def api(method, path, token, body=None):
            if path.endswith("/issues/42"):
                return 200, issue_payload(42, id_=4200)
            if path.startswith(blocked_by):
                return 200, [issue_payload(17, id_=1700), "not an issue"]
            if path.endswith("/parent"):
                return 404, {"message": "No parent issue found"}
            return 200, []

        out, err = io.StringIO(), io.StringIO()
        with patch.object(lgi, "api", api), patch.dict(os.environ, {"GITHUB_TOKEN": "t"}):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = lgi.main(["show", OWNER, REPO, "42"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("Blocked by (1):", out.getvalue())

    def test_a_list_of_only_unreadable_entries_is_not_reported_as_none(self):
        """`none` denies the link; entries that carried no issue do not."""
        blocked_by = f"/repos/{OWNER}/{REPO}/issues/42/dependencies/blocked_by"

        def api(method, path, token, body=None):
            if path.endswith("/issues/42"):
                return 200, issue_payload(42, id_=4200)
            if path.startswith(blocked_by):
                return 200, ["not an issue"]
            if path.endswith("/parent"):
                return 404, {"message": "No parent issue found"}
            return 200, []

        out, err = io.StringIO(), io.StringIO()
        with patch.object(lgi, "api", api), patch.dict(os.environ, {"GITHUB_TOKEN": "t"}):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = lgi.main(["show", OWNER, REPO, "42"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("Blocked by: 1 unreadable", out.getvalue())
        self.assertNotIn("Blocked by: none", out.getvalue())
        # The endpoints that really answered with nothing still say so.
        self.assertIn("Blocking: none", out.getvalue())


class TestFailureLines(unittest.TestCase):
    def test_a_response_with_no_body_does_not_render_the_status_twice(self):
        line = lgi._describe_failure(404, None, "", fallback="the endpoint answered 404")
        self.assertEqual(line, "HTTP 404: the endpoint answered 404")

    def test_the_status_is_still_the_last_resort_when_no_fallback_is_given(self):
        self.assertEqual(lgi._describe_failure(500, None, ""), "HTTP 500: HTTP 500")

    def test_a_body_still_wins_over_the_fallback(self):
        line = lgi._describe_failure(404, {"message": "Not Found"}, "", fallback="unused")
        self.assertEqual(line, "HTTP 404: Not Found")


class TestRelations(unittest.TestCase):
    def test_every_relation_is_reachable_by_its_own_flag(self):
        self.assertEqual(len(lgi.RELATION_BY_FLAG), len(lgi.RELATIONS))
        for relation in lgi.RELATIONS:
            with self.subTest(flag=relation.flag):
                self.assertTrue(relation.flag.startswith("--"))
                self.assertIs(lgi.RELATION_BY_FLAG[relation.flag], relation)

    def test_each_family_has_one_plain_and_one_inverted_direction(self):
        for family in ("dependency", "sub-issue"):
            directions = sorted(r.inverted for r in lgi.RELATIONS if r.family == family)
            self.assertEqual(directions, [False, True], family)

    def test_members_path_differs_by_family(self):
        holder = lgi.Issue(OWNER, REPO, 42, 4200, "t", False)
        self.assertTrue(lgi.members_path("dependency", holder).endswith("/dependencies/blocked_by"))
        self.assertTrue(lgi.members_path("sub-issue", holder).endswith("/sub_issues"))

    def test_script_is_executable(self):
        path = os.path.join(REPO_ROOT, "scripts", "agents", "link_gh_issues.py")
        self.assertTrue(os.stat(path).st_mode & stat.S_IXUSR)

    def test_docstring_records_which_relations_are_not_handled(self):
        """The 'why not' is the part a future reader is most likely to undo."""
        self.assertIn("Relations this script does not handle", lgi.__doc__)
        self.assertIn("duplicate of", lgi.__doc__)

    def test_the_help_epilog_agrees_with_the_docstring(self):
        """`--help` is prose too, and it drifted from the docstring once."""
        epilog = lgi.build_parser().epilog
        self.assertIn("not handled here", epilog)
        self.assertNotIn("no API", epilog)

    def test_docstring_records_the_one_parent_rule(self):
        """Refusing the move is a deliberate difference from sub_issue_write,
        so the reason has to be where a reader will look for it."""
        self.assertIn("An issue gets exactly one parent", lgi.__doc__)
        self.assertIn("--replace-parent", lgi.__doc__)

    def test_usage_examples_keep_their_line_continuations(self):
        """A non-raw docstring would eat the trailing backslashes, and the
        multi-line examples would then be uncopyable."""
        self.assertIn("<issue> \\\n", lgi.__doc__)


class TestStandardLibraryOnly(unittest.TestCase):
    """The docstring promises standard library only; nothing enforced it."""

    ALLOWED_NON_STDLIB = {"github_headers"}

    def test_no_third_party_imports(self):
        module_path = os.path.join(os.path.dirname(__file__), "link_gh_issues.py")
        with open(module_path, encoding="utf-8") as handle:
            source = handle.read()
        names = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.add(node.module.split(".")[0])
        third_party = {
            name for name in names - self.ALLOWED_NON_STDLIB if name not in sys.stdlib_module_names
        }
        self.assertEqual(third_party, set())


if __name__ == "__main__":
    unittest.main()
