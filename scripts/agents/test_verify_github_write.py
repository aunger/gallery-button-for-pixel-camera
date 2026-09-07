#!/usr/bin/env python3
"""Unit tests for verify_github_write.py.

Every test here runs with no network: the read-back is stubbed, so what is
under test is the filtering, locating, normalizing, diffing, escaping,
classifying, logging, and reporting, plus the wiring in .claude/settings.json.

The wiring test is the permanent guard against the failure this checker exists
to prevent: a hook that looks configured, fires for nothing, and turns absence
of warnings into false assurance.
"""

import ast
import contextlib
import io
import json
import os
import re
import stat
import sys
import urllib.error
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import verify_github_write as vgw  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MIDDLE_DOT = "·"


def payload(tool_name, tool_input, tool_response=None, session_id="test-session"):
    """Return a PostToolUse-shaped hook payload."""
    return {
        "session_id": session_id,
        "hook_event_name": "PostToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_response": tool_response,
    }


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


class TestFilter(unittest.TestCase):
    def test_github_write_with_body_is_a_text_write(self):
        self.assertTrue(vgw.is_text_write("mcp__github__add_issue_comment", {"body": "hi"}))

    def test_github_write_with_title_only_is_a_text_write(self):
        self.assertTrue(vgw.is_text_write("mcp__github__issue_write", {"title": "hi"}))

    def test_read_tool_carrying_no_text_is_ignored(self):
        self.assertFalse(
            vgw.is_text_write("mcp__github__issue_read", {"method": "get", "issue_number": 1})
        )

    def test_non_github_tool_is_ignored(self):
        self.assertFalse(vgw.is_text_write("Bash", {"body": "hi"}))

    def test_empty_text_is_ignored(self):
        self.assertFalse(vgw.is_text_write("mcp__github__add_issue_comment", {"body": ""}))

    def test_non_dict_input_is_ignored(self):
        self.assertFalse(vgw.is_text_write("mcp__github__add_issue_comment", None))

    def test_sent_texts_returns_only_populated_string_fields(self):
        texts = vgw.sent_texts({"title": "t", "body": "b", "labels": ["x"], "issue_number": 3})
        self.assertEqual(texts, {"title": "t", "body": "b"})


# ---------------------------------------------------------------------------
# Reading the object out of an MCP tool result
# ---------------------------------------------------------------------------


class TestResponseObject(unittest.TestCase):
    def test_plain_dict(self):
        self.assertEqual(vgw._response_object({"id": 7, "body": "x"})["id"], 7)

    def test_json_encoded_string(self):
        self.assertEqual(vgw._response_object('{"number": 12}')["number"], 12)

    def test_list_of_text_content_blocks(self):
        blocks = [{"type": "text", "text": json.dumps({"id": 99, "html_url": "u"})}]
        self.assertEqual(vgw._response_object(blocks)["id"], 99)

    def test_content_key_wrapping_text_blocks(self):
        wrapped = {"content": [{"type": "text", "text": json.dumps({"number": 5})}]}
        self.assertEqual(vgw._response_object(wrapped)["number"], 5)

    def test_unrecognizable_response_yields_empty_dict(self):
        self.assertEqual(vgw._response_object("not json at all"), {})

    def test_none_yields_empty_dict(self):
        self.assertEqual(vgw._response_object(None), {})


# ---------------------------------------------------------------------------
# Locating the stored object
# ---------------------------------------------------------------------------


class TestLocate(unittest.TestCase):
    def test_issue_update_uses_the_number_from_the_input(self):
        target = vgw.locate(
            "mcp__github__issue_write",
            {"method": "update", "owner": "o", "repo": "r", "issue_number": 9, "body": "b"},
            None,
        )
        self.assertEqual(target.api_url, "https://api.github.com/repos/o/r/issues/9")

    def test_issue_create_uses_the_number_from_the_result(self):
        target = vgw.locate(
            "mcp__github__issue_write",
            {"method": "create", "owner": "o", "repo": "r", "title": "t", "body": "b"},
            {"number": 42},
        )
        self.assertEqual(target.api_url, "https://api.github.com/repos/o/r/issues/42")

    def test_comment_uses_the_id_from_the_result(self):
        target = vgw.locate(
            "mcp__github__add_issue_comment",
            {"owner": "o", "repo": "r", "issue_number": 9, "body": "b"},
            {"id": 555},
        )
        self.assertEqual(target.api_url, "https://api.github.com/repos/o/r/issues/comments/555")

    def test_created_object_with_no_identifier_is_unverifiable(self):
        with self.assertRaises(vgw.Unverifiable):
            vgw.locate(
                "mcp__github__add_issue_comment",
                {"owner": "o", "repo": "r", "issue_number": 9, "body": "b"},
                None,
            )

    def test_create_pull_request(self):
        target = vgw.locate(
            "mcp__github__create_pull_request",
            {"owner": "o", "repo": "r", "title": "t", "body": "b", "head": "h", "base": "main"},
            {"number": 8},
        )
        self.assertEqual(target.api_url, "https://api.github.com/repos/o/r/pulls/8")

    def test_create_pull_request_falls_back_to_the_number_in_the_result_url(self):
        # The shape the GitHub MCP server actually returned when this checker's
        # own pull request was opened: an id, a url, and no number.  The id is
        # the pull request's database id, not the number REST addresses it by.
        target = vgw.locate(
            "mcp__github__create_pull_request",
            {
                "owner": "aunger",
                "repo": "gb",
                "title": "t",
                "body": "b",
                "head": "h",
                "base": "main",
            },
            {"id": "4340393530", "url": "https://github.com/aunger/gb/pull/951"},
        )
        self.assertEqual(target.api_url, "https://api.github.com/repos/aunger/gb/pulls/951")

    def test_issue_create_falls_back_to_the_number_in_the_result_url(self):
        target = vgw.locate(
            "mcp__github__issue_write",
            {"method": "create", "owner": "o", "repo": "r", "title": "t", "body": "b"},
            {"id": "1", "url": "https://github.com/o/r/issues/77"},
        )
        self.assertEqual(target.api_url, "https://api.github.com/repos/o/r/issues/77")

    def test_a_result_url_without_a_number_is_still_unverifiable(self):
        with self.assertRaises(vgw.Unverifiable):
            vgw.locate(
                "mcp__github__create_pull_request",
                {"owner": "o", "repo": "r", "title": "t", "body": "b"},
                {"id": "1", "url": "https://github.com/o/r"},
            )

    def test_update_pull_request(self):
        target = vgw.locate(
            "mcp__github__update_pull_request",
            {"owner": "o", "repo": "r", "pullNumber": 8, "body": "b"},
            None,
        )
        self.assertEqual(target.api_url, "https://api.github.com/repos/o/r/pulls/8")

    def test_review_reply_uses_the_new_comment_id_not_the_one_replied_to(self):
        target = vgw.locate(
            "mcp__github__add_reply_to_pull_request_comment",
            {"owner": "o", "repo": "r", "pullNumber": 8, "commentId": 111, "body": "b"},
            {"id": 222},
        )
        self.assertEqual(target.api_url, "https://api.github.com/repos/o/r/pulls/comments/222")

    def test_a_submitted_review_is_located_through_the_reviews_listing(self):
        # The result the GitHub MCP server actually returned when the first
        # review of this checker's own pull request was submitted: a plain
        # string, with no id, url, or number anywhere in it.
        target = vgw.locate(
            "mcp__github__pull_request_review_write",
            {"method": "submit_pending", "owner": "o", "repo": "r", "pullNumber": 8, "body": "b"},
            "pull request review submitted successfully",
        )
        self.assertEqual(
            target.api_url, "https://api.github.com/repos/o/r/pulls/8/reviews?per_page=100"
        )

    def test_review_created_with_an_event_is_located_the_same_way(self):
        target = vgw.locate(
            "mcp__github__pull_request_review_write",
            {
                "method": "create",
                "owner": "o",
                "repo": "r",
                "pullNumber": 8,
                "body": "b",
                "event": "COMMENT",
            },
            "pull request review submitted successfully",
        )
        self.assertEqual(
            target.api_url, "https://api.github.com/repos/o/r/pulls/8/reviews?per_page=100"
        )

    def test_the_newest_submitted_review_is_the_one_compared(self):
        target = vgw.locate(
            "mcp__github__pull_request_review_write",
            {"method": "submit_pending", "owner": "o", "repo": "r", "pullNumber": 8, "body": "b"},
            "pull request review submitted successfully",
        )
        listing = [
            {"id": 1, "state": "COMMENTED", "submitted_at": "2026-08-01T00:00:00Z", "body": "old"},
            {"id": 3, "state": "COMMENTED", "submitted_at": "2026-08-03T00:00:00Z", "body": "new"},
            {"id": 2, "state": "COMMENTED", "submitted_at": "2026-08-02T00:00:00Z", "body": "mid"},
        ]
        self.assertEqual(target.stored_object(listing)["body"], "new")

    def test_a_pending_review_is_never_the_one_compared(self):
        target = vgw.locate(
            "mcp__github__pull_request_review_write",
            {"method": "submit_pending", "owner": "o", "repo": "r", "pullNumber": 8, "body": "b"},
            "pull request review submitted successfully",
        )
        listing = [
            {"id": 1, "state": "COMMENTED", "submitted_at": "2026-08-01T00:00:00Z", "body": "sent"},
            {"id": 9, "state": "PENDING", "body": "draft"},
        ]
        self.assertEqual(target.stored_object(listing)["body"], "sent")

    def test_a_reviews_listing_that_may_be_paginated_is_unverifiable(self):
        # The listing comes back oldest first, so a full page means the newest
        # review may be on a page this checker did not fetch.  Guessing would
        # diff the wrong review and report a difference that is not one.
        target = vgw.locate(
            "mcp__github__pull_request_review_write",
            {"method": "submit_pending", "owner": "o", "repo": "r", "pullNumber": 8, "body": "b"},
            "pull request review submitted successfully",
        )
        full_page = [
            {"id": index, "state": "COMMENTED", "submitted_at": "2026-08-01T00:00:00Z"}
            for index in range(vgw.REVIEWS_PAGE_SIZE)
        ]
        with self.assertRaises(vgw.Unverifiable):
            target.stored_object(full_page)

    def test_a_listing_with_no_submitted_review_is_unverifiable(self):
        target = vgw.locate(
            "mcp__github__pull_request_review_write",
            {"method": "submit_pending", "owner": "o", "repo": "r", "pullNumber": 8, "body": "b"},
            "pull request review submitted successfully",
        )
        with self.assertRaises(vgw.Unverifiable):
            target.stored_object([{"id": 9, "state": "PENDING", "body": "draft"}])

    def test_an_ordinary_target_treats_the_response_as_the_object(self):
        target = vgw.locate(
            "mcp__github__update_pull_request",
            {"owner": "o", "repo": "r", "pullNumber": 8, "body": "b"},
            None,
        )
        self.assertEqual(target.stored_object({"body": "x"}), {"body": "x"})
        with self.assertRaises(vgw.Unverifiable):
            target.stored_object(["not", "an", "object"])

    def test_pending_review_is_unverifiable(self):
        with self.assertRaises(vgw.Unverifiable):
            vgw.locate(
                "mcp__github__pull_request_review_write",
                {"method": "create", "owner": "o", "repo": "r", "pullNumber": 8, "body": "b"},
                {"id": 79},
            )

    def test_pending_review_comment_is_unverifiable(self):
        with self.assertRaises(vgw.Unverifiable):
            vgw.locate(
                "mcp__github__add_comment_to_pending_review",
                {"owner": "o", "repo": "r", "pullNumber": 8, "path": "f", "body": "b"},
                None,
            )

    def test_unknown_write_tool_is_unverifiable_rather_than_skipped(self):
        with self.assertRaises(vgw.Unverifiable) as caught:
            vgw.locate(
                "mcp__github__some_future_tool", {"owner": "o", "repo": "r", "body": "b"}, None
            )
        self.assertIn("not in this checker's table", str(caught.exception))


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


class FakeResponse:
    """The context manager urlopen returns, over a fixed body."""

    def __init__(self, body):
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def http_error(code):
    return urllib.error.HTTPError("https://api.github.com/x", code, "boom", {}, None)


class FakeOpener:
    """A urlopen stand-in that replays a script of responses and records requests."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)


class TestFetchStored(unittest.TestCase):
    """The failure paths here are the ones the issue's acceptance criteria name."""

    def setUp(self):
        patcher = patch.object(vgw.time, "sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_successful_read_returns_the_decoded_object(self):
        opener = FakeOpener('{"body": "stored"}')
        self.assertEqual(vgw.fetch_stored("https://api.github.com/x", opener), {"body": "stored"})

    def test_a_404_is_retried_once_to_absorb_read_after_create_lag(self):
        opener = FakeOpener(http_error(404), '{"body": "stored"}')
        self.assertEqual(vgw.fetch_stored("https://api.github.com/x", opener), {"body": "stored"})
        self.assertEqual(len(opener.requests), 2)
        self.sleep.assert_called_once_with(vgw.RETRY_DELAY_SECONDS)

    def test_a_second_404_is_unverifiable_rather_than_clean(self):
        opener = FakeOpener(http_error(404), http_error(404))
        with self.assertRaises(vgw.Unverifiable) as caught:
            vgw.fetch_stored("https://api.github.com/x", opener)
        self.assertIn("HTTP 404", str(caught.exception))

    def test_a_non_404_http_error_is_not_retried(self):
        opener = FakeOpener(http_error(500))
        with self.assertRaises(vgw.Unverifiable):
            vgw.fetch_stored("https://api.github.com/x", opener)
        self.assertEqual(len(opener.requests), 1)
        self.sleep.assert_not_called()

    def test_a_rate_limit_says_the_read_was_unauthenticated_when_it_was(self):
        opener = FakeOpener(http_error(403))
        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}, clear=False):
            with self.assertRaises(vgw.Unverifiable) as caught:
                vgw.fetch_stored("https://api.github.com/x", opener)
        self.assertIn("HTTP 403", str(caught.exception))
        self.assertIn("no GITHUB_TOKEN", str(caught.exception))

    def test_an_authenticated_read_does_not_blame_a_missing_token(self):
        opener = FakeOpener(http_error(403))
        with patch.dict(os.environ, {"GITHUB_TOKEN": "t"}, clear=False):
            with self.assertRaises(vgw.Unverifiable) as caught:
                vgw.fetch_stored("https://api.github.com/x", opener)
        self.assertNotIn("no GITHUB_TOKEN", str(caught.exception))

    def test_a_network_failure_is_unverifiable_not_unaltered(self):
        opener = FakeOpener(urllib.error.URLError("no route to host"))
        with self.assertRaises(vgw.Unverifiable) as caught:
            vgw.fetch_stored("https://api.github.com/x", opener)
        self.assertIn("URLError", str(caught.exception))

    def test_unparseable_json_is_unverifiable(self):
        opener = FakeOpener("not json")
        with self.assertRaises(vgw.Unverifiable):
            vgw.fetch_stored("https://api.github.com/x", opener)

    def test_a_token_is_sent_as_a_bearer_credential(self):
        opener = FakeOpener("{}")
        with patch.dict(os.environ, {"GITHUB_TOKEN": "secret"}, clear=False):
            vgw.fetch_stored("https://api.github.com/x", opener)
        headers = opener.requests[0].headers
        self.assertEqual(headers["Authorization"], "Bearer secret")
        self.assertIn("User-agent", headers)

    def test_without_a_token_no_authorization_header_is_sent(self):
        opener = FakeOpener("{}")
        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}, clear=False):
            vgw.fetch_stored("https://api.github.com/x", opener)
        self.assertNotIn("Authorization", opener.requests[0].headers)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


class TestNormalize(unittest.TestCase):
    def test_crlf_becomes_lf(self):
        self.assertEqual(vgw.normalize("a\r\nb"), "a\nb")

    def test_trailing_newline_is_dropped_on_both_sides(self):
        self.assertEqual(vgw.normalize("body"), vgw.normalize("body\n"))

    def test_interior_newlines_are_kept(self):
        self.assertEqual(vgw.normalize("a\n\nb\n"), "a\n\nb")

    def test_byline_is_subtracted_from_stored_text(self):
        stored = "Real text\n_Generated by [Claude Code](https://claude.ai/code/session_abc)_"
        self.assertEqual(vgw.normalize(stored), "Real text")

    def test_byline_is_subtracted_from_sent_text_too(self):
        # Otherwise an agent that mistakenly sent a byline would get a phantom
        # "text was removed" finding when the workflow strips it.
        sent = "Real text\nhttps://claude.ai/code/session_abc"
        self.assertEqual(vgw.normalize(sent), vgw.normalize("Real text"))


# ---------------------------------------------------------------------------
# Diffing
# ---------------------------------------------------------------------------


class TestDiff(unittest.TestCase):
    def test_identical_text_produces_no_regions(self):
        self.assertEqual(vgw.diff_regions("same", "same"), ([], 0))

    def test_injected_middle_dot_is_one_region(self):
        regions, omitted = vgw.diff_regions(
            "ping @dependabot rebase", f"ping @{MIDDLE_DOT}dependabot rebase"
        )
        self.assertEqual(omitted, 0)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].stored, MIDDLE_DOT)
        self.assertEqual(regions[0].classification, "mention dotting")

    def test_removed_text_is_classified_as_removal(self):
        regions, _ = vgw.diff_regions("prefix PLACEHOLDER suffix", "prefix  suffix")
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].classification, "removal")
        self.assertEqual(regions[0].sent, "PLACEHOLDER")

    def test_added_text_is_classified_as_addition(self):
        regions, _ = vgw.diff_regions("body", "body\nfooter")
        self.assertEqual(regions[0].classification, "addition")

    def test_backticks_inserted_around_a_link_are_classified_as_their_own_behavior(self):
        # PR #958 (issue #962): links whose label is shaped like an owner/repo
        # pair came back wrapped in inserted back-tick runs, while the links in
        # the same body whose labels held no slash stored intact.  The run
        # length was never characterized, so any run has to count.
        link = "[frameworks/base](https://android.googlesource.com/platform/frameworks/base)"
        for run in (vgw.BACK_TICK, vgw.BACK_TICK * 2):
            sent = f"read {link} at head"
            regions, _ = vgw.diff_regions(sent, f"read {run}{link}{run} at head")
            self.assertEqual(len(regions), 2)
            for region in regions:
                self.assertEqual((region.sent, region.stored), ("", run))
                self.assertEqual(region.classification, "back-tick insertion")

    def test_a_lone_inserted_backtick_is_classified_the_same_way(self):
        # The class is named for what one region is, because one region is all
        # a Region can see.  A single inserted back-tick wraps nothing, so a
        # class named for a pair would have promised more than it can tell.
        regions, _ = vgw.diff_regions("see foo() below", "see `foo() below")
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].classification, "back-tick insertion")

    def test_an_insertion_carrying_more_than_backticks_is_still_an_addition(self):
        # The narrow test keeps an appended footer that happens to contain a
        # back-tick out of the insertion class, whose advice would misdirect it.
        regions, _ = vgw.diff_regions("body", "body\n`generated` by a bot")
        self.assertEqual(regions[0].classification, "addition")

    def test_replacement_is_classified_as_other(self):
        regions, _ = vgw.diff_regions("alpha", "omega")
        self.assertEqual(regions[0].classification, "other")

    def test_region_position_is_reported_in_the_sent_text(self):
        regions, _ = vgw.diff_regions("0123456789X", "0123456789")
        self.assertEqual(regions[0].position, 10)

    def test_region_count_is_bounded_and_the_remainder_is_counted(self):
        sent = " ".join(f"@user{index}" for index in range(20))
        stored = sent.replace("@", "@" + MIDDLE_DOT)
        regions, omitted = vgw.diff_regions(sent, stored)
        self.assertEqual(len(regions), vgw.MAX_REGIONS_PER_FIELD)
        self.assertEqual(omitted, 20 - vgw.MAX_REGIONS_PER_FIELD)

    def test_a_wholesale_rewrite_is_reported_coarsely(self):
        sent = "a" * (vgw.MAX_FINE_DIFF_CHARS + 10)
        stored = "b" * (vgw.MAX_FINE_DIFF_CHARS + 10)
        regions, omitted = vgw.diff_regions(sent, stored)
        self.assertEqual((len(regions), omitted), (1, 0))

    def test_a_small_change_in_a_long_body_stays_fine_grained(self):
        filler = "x" * 50000
        regions, _ = vgw.diff_regions(
            filler + "@bot" + filler, filler + "@" + MIDDLE_DOT + "bot" + filler
        )
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].classification, "mention dotting")


# ---------------------------------------------------------------------------
# Escaping and rendering
# ---------------------------------------------------------------------------


class TestEscape(unittest.TestCase):
    def test_ascii_passes_through(self):
        self.assertEqual(vgw.escape("plain text 123"), "plain text 123")

    def test_middle_dot_is_made_visible(self):
        escaped = vgw.escape(MIDDLE_DOT)
        self.assertIn("\\u00b7", escaped)
        self.assertNotIn(MIDDLE_DOT, escaped)

    def test_invisible_character_is_made_visible(self):
        escaped = vgw.escape("a​b")
        self.assertIn("\\u200b", escaped)

    def test_newline_and_tab_are_escaped(self):
        self.assertEqual(vgw.escape("a\nb\tc"), "a\\nb\\tc")

    def test_backslash_is_escaped_so_the_delta_is_unambiguous(self):
        self.assertEqual(vgw.escape("\\u00b7"), "\\\\u00b7")


class TestRenderField(unittest.TestCase):
    def test_output_is_bounded_across_both_sides_together(self):
        for count in (1, vgw.MAX_REGIONS_PER_FIELD):
            regions = [vgw.Region(index, "a" * 5000, "b" * 5000) for index in range(count)]
            rendered = vgw.render_field("body", regions, 0)
            delta_characters = sum(len(part) for part in re.findall(r"(?<=: )[ab]+", rendered))
            self.assertLessEqual(delta_characters, vgw.MAX_CHARS_PER_FIELD)
            self.assertIn("more characters", rendered)

    def test_omitted_regions_are_counted_in_the_output(self):
        rendered = vgw.render_field("body", [vgw.Region(0, "x", "y")], 4)
        self.assertIn("4 further changed region(s)", rendered)
        self.assertIn("5 changed region(s)", rendered)


# ---------------------------------------------------------------------------
# The session log
# ---------------------------------------------------------------------------


class TestAdvice(unittest.TestCase):
    """The advice is the sentence an agent acts on, so it must not overclaim."""

    def test_the_mention_advice_does_not_claim_every_attempt_is_altered(self):
        # A probe on 2026-08-23 stored a mention intact, so an agent told that
        # every attempt is altered would decline to retry on false grounds.
        advice = vgw.ADVICE["mention dotting"].lower()
        self.assertNotIn("every attempt", advice)
        self.assertIn("not constant", advice)

    def test_the_backtick_advice_does_not_claim_a_retry_is_pointless(self):
        # Mention dotting is the one behavior in this table that was probed for
        # constancy, and it turned out not to be constant.  This one has not
        # been probed at all, so the determinism claim retracted above is not
        # available here either.
        advice = vgw.ADVICE["back-tick insertion"].lower()
        self.assertNotIn("same result", advice)
        self.assertIn("not known", advice)

    def test_the_backtick_advice_sends_the_reader_to_the_stored_object(self):
        # The predicate sees an insertion of back-ticks, not what they enclose,
        # and PR #958 never pinned down whether the run enclosed the whole link
        # or only its label.  Those render differently, so the advice must not
        # assert an outcome it cannot know.
        advice = vgw.ADVICE["back-tick insertion"].lower()
        self.assertIn("read it rather than assuming", advice)

    def test_every_classification_has_advice(self):
        # build_report indexes ADVICE by classification name, so a behavior
        # added without advice would raise while reporting a real finding,
        # which is the one moment this checker exists for.
        with open(vgw.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        names = [
            node.value.value
            for function in ast.walk(tree)
            if isinstance(function, ast.FunctionDef) and function.name == "classification"
            for node in ast.walk(function)
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant)
        ]
        self.assertEqual(sorted(names), sorted(vgw.ADVICE))

    def test_no_advice_carries_a_live_mention_token(self):
        # The advice strings are this checker's own words, so they can be kept
        # free of anything a write path would alter.  The delta cannot: it is
        # the agent's text, quoted back.
        for name, advice in vgw.ADVICE.items():
            self.assertNotRegex(advice, r"@\w", f"{name} advice carries a live mention")

    def test_escaping_does_not_neutralize_a_mention_in_the_delta(self):
        # Printable ASCII passes through by design, so an at-sign token the
        # agent sent survives into the report.  Asserted here so the escape()
        # docstring cannot drift back into promising that the delta is safe to
        # quote into a GitHub comment.
        self.assertEqual(vgw.escape("please @dependabot rebase"), "please @dependabot rebase")


class TestLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch("tempfile.gettempdir", return_value=self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_path_is_scoped_to_the_session_and_sanitized(self):
        path = vgw.log_path("../../etc/pa sswd")
        self.assertEqual(os.path.dirname(path), self.tmp.name)
        self.assertNotIn("/", os.path.basename(path)[len("gb4pc-github-writes-") :])

    def test_entries_round_trip(self):
        vgw.append_log("s1", {"tool": "t", "status": "clean", "key": "k"})
        vgw.append_log("s1", {"tool": "t", "status": "finding", "key": "k2"})
        entries = vgw.read_log("s1")
        self.assertEqual([entry["status"] for entry in entries], ["clean", "finding"])

    def test_sessions_do_not_share_a_log(self):
        vgw.append_log("s1", {"status": "clean", "key": "k"})
        self.assertEqual(vgw.read_log("s2"), [])

    def test_missing_log_reads_as_empty(self):
        self.assertEqual(vgw.read_log("never-written"), [])

    def test_a_long_value_is_shortened_and_the_entry_survives(self):
        # Truncating the JSON text instead would produce a line that cannot be
        # parsed, and an unparseable line is an entry that vanishes from the
        # one artifact that answers "did the check actually run".
        vgw.append_log("s1", {"status": "clean", "key": "k" * 10000})
        with open(vgw.log_path("s1"), encoding="utf-8") as handle:
            self.assertLessEqual(len(handle.readline()), vgw.MAX_LOG_LINE_CHARS + 1)
        entries = vgw.read_log("s1")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["status"], "clean")
        self.assertEqual(len(entries[0]["key"]), vgw.MAX_LOG_VALUE_CHARS)

    def test_a_full_log_says_so_once_instead_of_going_quiet(self):
        path = vgw.log_path("s1")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("x" * (vgw.MAX_LOG_BYTES + 1) + "\n")
        vgw.append_log("s1", {"status": "clean", "key": "a"})
        vgw.append_log("s1", {"status": "clean", "key": "b"})
        entries = vgw.read_log("s1")
        self.assertEqual([entry["status"] for entry in entries], [vgw.LOG_FULL_MARKER])

    def test_already_reported_matches_on_status_and_key(self):
        entries = [{"status": "finding", "key": "url|removal"}]
        self.assertTrue(vgw.already_reported(entries, "finding", "url|removal"))
        self.assertFalse(vgw.already_reported(entries, "finding", "url|addition"))
        self.assertFalse(vgw.already_reported(entries, "unverified", "url|removal"))


# ---------------------------------------------------------------------------
# End to end, with the read-back stubbed
# ---------------------------------------------------------------------------


class TestCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch("tempfile.gettempdir", return_value=self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def comment(body, session_id="s"):
        return payload(
            "mcp__github__add_issue_comment",
            {"owner": "o", "repo": "r", "issue_number": 1, "body": body},
            {"id": 500},
            session_id,
        )

    def test_a_call_that_writes_no_text_says_nothing(self):
        status, message = vgw.check(payload("mcp__github__issue_read", {"method": "get"}))
        self.assertEqual((status, message), (vgw.EXIT_CLEAN, ""))

    def test_matching_text_says_nothing_and_is_logged(self):
        with patch.object(vgw, "fetch_stored", return_value={"body": "hello\n"}):
            status, message = vgw.check(self.comment("hello"))
        self.assertEqual((status, message), (vgw.EXIT_CLEAN, ""))
        self.assertEqual([entry["status"] for entry in vgw.read_log("s")], ["clean"])

    def test_altered_text_is_reported_with_an_escaped_delta_and_advice(self):
        stored = {"body": f"see @{MIDDLE_DOT}dependabot rebase"}
        with patch.object(vgw, "fetch_stored", return_value=stored):
            status, message = vgw.check(self.comment("see @dependabot rebase"))
        self.assertEqual(status, vgw.EXIT_FINDING)
        self.assertIn("altered in storage", message)
        self.assertIn("issues/comments/500", message)
        self.assertIn("\\u00b7", message)
        self.assertNotIn(MIDDLE_DOT, message)
        self.assertIn("notifies nobody", message)

    def test_the_report_links_the_stored_object_when_the_api_gives_a_url(self):
        stored = {"body": "changed", "html_url": "https://github.com/o/r/issues/1#issuecomment-500"}
        with patch.object(vgw, "fetch_stored", return_value=stored):
            message = vgw.check(self.comment("original"))[1]
        self.assertIn("https://github.com/o/r/issues/1#issuecomment-500", message)

    def test_a_repeat_of_the_same_alteration_escalates(self):
        stored = {"body": f"see @{MIDDLE_DOT}bot"}
        with patch.object(vgw, "fetch_stored", return_value=stored):
            first = vgw.check(self.comment("see @bot"))[1]
            second = vgw.check(self.comment("see @bot"))[1]
        self.assertIn("Edit it", first)
        self.assertNotIn("change approach", first)
        self.assertIn("change approach", second)

    def test_an_unverifiable_write_is_reported_once_per_session(self):
        pending = payload(
            "mcp__github__add_comment_to_pending_review",
            {"owner": "o", "repo": "r", "pullNumber": 1, "path": "f", "body": "b"},
            None,
            "s",
        )
        first_status, first_message = vgw.check(pending)
        second_status, second_message = vgw.check(pending)
        self.assertEqual(first_status, vgw.EXIT_FINDING)
        self.assertIn("NOT verified", first_message)
        self.assertEqual((second_status, second_message), (vgw.EXIT_CLEAN, ""))

    def test_a_failed_read_back_is_reported_as_unverified_not_as_clean(self):
        with patch.object(vgw, "fetch_stored", side_effect=vgw.Unverifiable("GET failed: nope")):
            status, message = vgw.check(self.comment("hello"))
        self.assertEqual(status, vgw.EXIT_FINDING)
        self.assertIn("NOT verified", message)
        self.assertIn("GET failed: nope", message)

    @staticmethod
    def issue_update(session_id="s", **fields):
        return payload(
            "mcp__github__issue_write",
            dict({"method": "update", "owner": "o", "repo": "r", "issue_number": 1}, **fields),
            None,
            session_id,
        )

    def test_a_field_the_stored_object_does_not_carry_is_reported(self):
        with patch.object(vgw, "fetch_stored", return_value={"body": "unrelated"}):
            status, message = vgw.check(self.issue_update(title="t"))
        self.assertEqual(status, vgw.EXIT_FINDING)
        self.assertIn("carries no such field", message)

    def test_a_field_that_could_not_be_compared_is_not_reported_as_clean(self):
        # The stored object carries title but not body, and the title matches.
        # Reporting that clean would be the false-assurance shape, and it would
        # put body in the log as a field that was checked.
        with patch.object(vgw, "fetch_stored", return_value={"title": "T"}):
            status, message = vgw.check(self.issue_update(title="T", body="B"))
        self.assertEqual(status, vgw.EXIT_FINDING)
        self.assertIn("NOT verified", message)
        self.assertIn("body", message)

    def test_the_log_records_only_the_fields_actually_compared(self):
        with patch.object(vgw, "fetch_stored", return_value={"title": "T"}):
            vgw.check(self.issue_update(title="T", body="B"))
        entry = vgw.read_log("s")[-1]
        self.assertEqual(entry["compared"], ["title"])
        self.assertEqual(entry["uncompared"], ["body"])
        self.assertNotEqual(entry["status"], "clean")

    def test_an_uncomparable_field_is_reported_once_per_session(self):
        with patch.object(vgw, "fetch_stored", return_value={"title": "T"}):
            first = vgw.check(self.issue_update(title="T", body="B"))
            second = vgw.check(self.issue_update(title="T", body="B"))
        self.assertEqual(first[0], vgw.EXIT_FINDING)
        self.assertEqual(second, (vgw.EXIT_CLEAN, ""))

    def test_a_finding_also_names_the_field_it_could_not_compare(self):
        with patch.object(vgw, "fetch_stored", return_value={"title": "changed"}):
            status, message = vgw.check(self.issue_update(title="T", body="B"))
        self.assertEqual(status, vgw.EXIT_FINDING)
        self.assertIn("altered in storage", message)
        self.assertIn("NOT compared", message)

    def test_both_fields_are_compared(self):
        write = payload(
            "mcp__github__issue_write",
            {
                "method": "update",
                "owner": "o",
                "repo": "r",
                "issue_number": 1,
                "title": "Title",
                "body": "Body",
            },
            None,
            "s",
        )
        with patch.object(vgw, "fetch_stored", return_value={"title": "Title", "body": "Bodie"}):
            status, message = vgw.check(write)
        self.assertEqual(status, vgw.EXIT_FINDING)
        self.assertIn('field "body"', message)
        self.assertNotIn('field "title"', message)


class TestMain(unittest.TestCase):
    @staticmethod
    def run_main(stdin_text):
        """Run main() on *stdin_text*, returning (exit code, stderr)."""
        captured = io.StringIO()
        with patch("sys.stdin", new=io.StringIO(stdin_text)):
            with contextlib.redirect_stderr(captured):
                status = vgw.main()
        return status, captured.getvalue()

    def test_unreadable_payload_is_a_checker_fault_not_a_finding(self):
        status, stderr = self.run_main("{not json")
        self.assertEqual(status, vgw.EXIT_INTERNAL_ERROR)
        self.assertIn("unreadable hook payload", stderr)

    def test_a_payload_that_is_not_an_object_is_a_checker_fault(self):
        self.assertEqual(self.run_main("[1, 2]")[0], vgw.EXIT_INTERNAL_ERROR)

    def test_an_unexpected_crash_exits_as_a_checker_fault(self):
        with patch.object(vgw, "check", side_effect=RuntimeError("boom")):
            status, stderr = self.run_main("{}")
        self.assertEqual(status, vgw.EXIT_INTERNAL_ERROR)
        self.assertIn("boom", stderr)

    def test_empty_stdin_is_clean_and_silent(self):
        self.assertEqual(self.run_main(""), (vgw.EXIT_CLEAN, ""))


# ---------------------------------------------------------------------------
# The checker must stay importable from a hook
# ---------------------------------------------------------------------------


class TestStandardLibraryOnly(unittest.TestCase):
    """A hook that fails on a missing dependency is a hook that stops checking."""

    ALLOWED_NON_STDLIB = {"github_headers", "session_bylines"}

    def test_no_third_party_imports(self):
        module_path = os.path.join(os.path.dirname(__file__), "verify_github_write.py")
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


# ---------------------------------------------------------------------------
# The wiring in .claude/settings.json
# ---------------------------------------------------------------------------

# A matcher made only of these characters is compared as an exact string rather
# than as a regular expression.  So "mcp__github" looks server-wide, matches no
# tool at all, and reports nothing forever, which is precisely the failure this
# checker exists to catch.
_EXACT_MATCH_ONLY = re.compile(r"^[A-Za-z0-9_\-, |]+$")

HOOK_SCRIPT = ".claude/hooks/post-tool-use-github-readback.sh"


def matcher_matches(matcher: str, tool_name: str) -> bool:
    """Evaluate a Claude Code hook matcher against a tool name."""
    if _EXACT_MATCH_ONLY.fullmatch(matcher):
        return matcher == tool_name
    return re.search(matcher, tool_name) is not None


class TestWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO_ROOT, ".claude", "settings.json"), encoding="utf-8") as handle:
            settings = json.load(handle)
        entries = [
            entry
            for entry in settings.get("hooks", {}).get("PostToolUse", [])
            if any(HOOK_SCRIPT in hook.get("command", "") for hook in entry.get("hooks", []))
        ]
        cls.entries = entries

    def test_exactly_one_post_tool_use_entry_runs_this_hook(self):
        self.assertEqual(len(self.entries), 1)

    def test_the_command_exists_and_is_executable(self):
        command = self.entries[0]["hooks"][0]["command"]
        path = command.replace("$CLAUDE_PROJECT_DIR", REPO_ROOT)
        self.assertTrue(os.path.isfile(path), f"{path} does not exist")
        self.assertTrue(os.stat(path).st_mode & stat.S_IXUSR, f"{path} is not executable")

    def test_the_matcher_fires_for_every_covered_write_tool(self):
        matcher = self.entries[0]["matcher"]
        for short_name in vgw.LOCATORS:
            tool_name = vgw.TOOL_PREFIX + short_name
            self.assertTrue(matcher_matches(matcher, tool_name), f"{matcher} misses {tool_name}")

    def test_the_matcher_fires_for_a_github_tool_added_later(self):
        # Narrowing the matcher to a list of today's write tools would let a
        # renamed or new tool escape it silently, and a silent gap is the
        # expensive failure here.
        self.assertTrue(
            matcher_matches(self.entries[0]["matcher"], "mcp__github__some_future_tool")
        )

    def test_the_matcher_does_not_fire_for_unrelated_tools(self):
        matcher = self.entries[0]["matcher"]
        self.assertFalse(matcher_matches(matcher, "Bash"))
        self.assertFalse(matcher_matches(matcher, "Agent"))

    def test_a_matcher_missing_the_regex_suffix_would_fail_this_suite(self):
        # The guard is only as good as the rule it applies, so assert the rule.
        self.assertFalse(matcher_matches("mcp__github", "mcp__github__add_issue_comment"))
        self.assertTrue(matcher_matches("mcp__github__.*", "mcp__github__add_issue_comment"))


if __name__ == "__main__":
    unittest.main()
