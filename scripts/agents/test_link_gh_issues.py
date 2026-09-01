#!/usr/bin/env python3
"""Unit tests for link_gh_issues.py.

Every test here runs with no network: `api` is replaced by a fake that records
each call and answers from a small in-memory model of the two link families.
What is under test is therefore the reference parsing, the id resolution, the
inversion of `--blocks` and `--child-of` onto the other issue's endpoint, the
idempotence rules, the guards, and the reporting.

The inversion tests are the ones that matter most. GitHub serves no write for
the `blocking` direction, so a bug there would silently record a dependency
backwards -- a link that looks right in the report and is wrong in the sidebar.
"""

import io
import contextlib
import json
import os
import stat
import sys
import unittest
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

    `links` is keyed by (family, owner, repo, number) -- the holder of the link
    -- and holds the set of linked issue ids, which is exactly the shape the
    real membership endpoints return.
    """

    def __init__(self, issues=None, links=None, errors=None):
        self.issues = issues or {}
        self.links = links or {}
        self.errors = errors or {}
        self.calls = []

    def __call__(self, method, path, token, body=None):
        self.calls.append((method, path, body))

        for (err_method, err_path), error in self.errors.items():
            if err_method == method and err_path == path:
                raise error

        # GET /repos/{o}/{r}/issues/{n}
        parts = path.strip("/").split("/")
        if method == "GET" and len(parts) == 5 and parts[3] == "issues":
            key = (parts[1], parts[2], int(parts[4]))
            if key not in self.issues:
                return 404, {"message": "Not Found"}
            return 200, self.issues[key]

        # The two read-only views are derived from the same model, by looking
        # up the inverse of a stored link -- which is exactly how GitHub
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
            members.add(body["issue_id"] if family == "dependency" else body["sub_issue_id"])
            return 201, {}
        if method == "DELETE":
            target = tail if tail is not None else body["sub_issue_id"]
            members.discard(target)
            return 204, None
        raise AssertionError(f"unexpected method {method}")

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

    def test_a_dependency_on_a_pull_request_is_refused_with_the_fixes_hint(self):
        issues = two_issues()
        issues[(OWNER, REPO, 17)] = issue_payload(17, id_=1700, pull_request=True)
        fake = FakeApi(issues)
        code, _, err = run(["add", OWNER, REPO, "42", "--blocked-by", "17"], fake)
        self.assertEqual(code, 1)
        self.assertIn("Fixes #N", err)
        self.assertFalse([c for c in fake.calls if c[0] == "POST"])

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

    def test_a_forbidden_write_explains_the_token_permission(self):
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
        self.assertIn("issues: write", err)


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


class TestShow(unittest.TestCase):
    def _fake_with_parent(self):
        issues = two_issues()
        fake = FakeApi(issues, links={("dependency", OWNER, REPO, 42): {1700}})
        return fake

    def test_show_lists_each_relation(self):
        code, out, err = run(["show", OWNER, REPO, "42"], self._fake_with_parent())
        self.assertEqual(code, 0, err)
        self.assertIn("Blocked by (1):", out)
        self.assertIn(f"{OWNER}/{REPO}#42: Subject", out)
        self.assertIn("Blocking: none", out)
        self.assertIn("Sub-issues: none", out)

    def test_show_reports_a_parentless_issue_as_none(self):
        """GitHub answers `GET .../parent` with 404 when there is no parent."""
        code, out, err = run(["show", OWNER, REPO, "42"], self._fake_with_parent())
        self.assertEqual(code, 0, err)
        self.assertIn("Parent: none", out)

    def test_show_json_is_parseable_and_carries_every_relation(self):
        code, out, err = run(["show", OWNER, REPO, "42", "--json"], self._fake_with_parent())
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
        self.assertIn("Parent (1):", out)
        self.assertIn("Prerequisite", out)

    def test_show_lists_sub_issues_of_a_parent(self):
        fake = FakeApi(two_issues(), links={("sub-issue", OWNER, REPO, 42): {1700}})
        code, out, err = run(["show", OWNER, REPO, "42"], fake)
        self.assertEqual(code, 0, err)
        self.assertIn("Sub-issues (1):", out)

    def test_show_makes_no_write_call(self):
        fake = self._fake_with_parent()
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


class TestRelations(unittest.TestCase):
    def test_every_relation_maps_a_flag_to_an_argparse_destination(self):
        for relation in lgi.RELATIONS:
            with self.subTest(flag=relation.flag):
                self.assertTrue(relation.flag.startswith("--"))
                self.assertEqual(relation.dest, relation.flag[2:].replace("-", "_"))

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

    def test_docstring_records_why_the_fixes_link_is_absent(self):
        """The 'why not' is the part a future reader is most likely to undo."""
        self.assertIn("What this script deliberately does not do", lgi.__doc__)
        # The claim, and the remedy that replaces it.
        self.assertIn("no write API", lgi.__doc__)
        self.assertIn("Fixes #123", lgi.__doc__)

    def test_usage_examples_keep_their_line_continuations(self):
        """A non-raw docstring would eat the trailing backslashes, and the
        multi-line examples would then be uncopyable."""
        self.assertIn("<issue> \\\n", lgi.__doc__)


if __name__ == "__main__":
    unittest.main()
