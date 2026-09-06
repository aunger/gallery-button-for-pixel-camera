#!/usr/bin/env python3
"""Unit tests for dispatch_cost.py.

Nothing here reads a real transcript: each test writes the records it needs into
a temporary project directory, so the fixtures state the shape under test rather
than depending on whichever session happens to be on disk.

The traps are what these mostly cover, because each of them reports a plausible
wrong number rather than failing:

- counting records instead of `message.id` groups multiplies calls and every
  total with them;
- taking the first record's `output_tokens` reads the partial streaming count;
- folding cache reads into new tokens, or dropping them, is not a cost;
- pricing a 1-hour cache write at the 5-minute rate understates it by 1.6x;
- a full re-write is invisible in any total that does not break writes out per
  call.
"""

import ast
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import dispatch_cost as dc  # noqa: E402

SESSION = "11111111-2222-3333-4444-555555555555"


def usage(input_tokens=2, write_5m=0, write_1h=0, read=0, output=1, breakdown=True):
    block = {
        "input_tokens": input_tokens,
        "cache_creation_input_tokens": write_5m + write_1h,
        "cache_read_input_tokens": read,
        "output_tokens": output,
    }
    if breakdown:
        block["cache_creation"] = {
            "ephemeral_5m_input_tokens": write_5m,
            "ephemeral_1h_input_tokens": write_1h,
        }
    return block


def record(message_id, timestamp, stop_reason=None, **kwargs):
    return {
        "type": "assistant",
        "timestamp": timestamp,
        "message": {
            "id": message_id,
            "role": "assistant",
            "stop_reason": stop_reason,
            "usage": usage(**kwargs),
        },
    }


def non_call_record(timestamp):
    """A user turn: no `message.usage`, so not an API call."""
    return {"type": "user", "timestamp": timestamp, "message": {"role": "user", "content": "hi"}}


def write_transcript(path, records):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for entry in records:
            handle.write(json.dumps(entry) if isinstance(entry, dict) else entry)
            handle.write("\n")


class ProjectFixture(unittest.TestCase):
    """A temporary `{root}/{project}/` holding whatever a test needs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "projects")
        self.project = os.path.join(self.root, "-home-user-repo")
        os.makedirs(self.project)

    def orchestrator(self, records, session=SESSION):
        write_transcript(os.path.join(self.project, session + ".jsonl"), records)

    def dispatch(self, agent, records, session=SESSION):
        write_transcript(
            os.path.join(self.project, session, "subagents", "agent-%s.jsonl" % agent), records
        )

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dc.main(["--projects-root", self.root] + list(argv))
        return code, out.getvalue(), err.getvalue()

    def run_json(self, *argv):
        code, out, err = self.run_main("--json", *argv)
        self.assertEqual(code, 0, err)
        return json.loads(out)


class TestOneCallPerMessageId(ProjectFixture):
    def test_records_of_one_message_are_one_call(self):
        # One assistant message spanning three content blocks, each repeating
        # the same usage. Summing the records would report 3 calls and triple
        # every input-side total.
        self.orchestrator(
            [
                record("msg_a", "2026-09-06T09:00:00.000Z", write_5m=1000, read=5000, output=1),
                record("msg_a", "2026-09-06T09:00:01.000Z", write_5m=1000, read=5000, output=1),
                record(
                    "msg_a",
                    "2026-09-06T09:00:02.000Z",
                    stop_reason="tool_use",
                    write_5m=1000,
                    read=5000,
                    output=400,
                ),
            ]
        )
        payload = self.run_json()
        session = payload["sessions"][0]
        self.assertEqual(session["calls"], 1)
        self.assertEqual(session["tokens"]["cache_write_5m"], 1000)
        self.assertEqual(session["tokens"]["cache_read"], 5000)

    def test_output_is_the_maximum_over_the_group_not_the_first_record(self):
        self.orchestrator(
            [
                record("msg_a", "2026-09-06T09:00:00.000Z", output=1),
                record("msg_a", "2026-09-06T09:00:01.000Z", output=3),
                record("msg_a", "2026-09-06T09:00:02.000Z", stop_reason="end_turn", output=612),
            ]
        )
        payload = self.run_json()
        self.assertEqual(payload["sessions"][0]["tokens"]["output"], 612)

    def test_records_without_usage_are_not_calls(self):
        self.orchestrator(
            [
                non_call_record("2026-09-06T09:00:00.000Z"),
                record("msg_a", "2026-09-06T09:00:01.000Z"),
                non_call_record("2026-09-06T09:00:02.000Z"),
            ]
        )
        self.assertEqual(self.run_json()["sessions"][0]["calls"], 1)

    def test_calls_are_ordered_and_numbered_by_start_time(self):
        self.orchestrator(
            [
                record("msg_a", "2026-09-06T09:00:00.000Z"),
                record("msg_b", "2026-09-06T09:00:10.000Z"),
                record("msg_c", "2026-09-06T09:00:20.000Z"),
            ]
        )
        rows = self.run_json("--calls")["sessions"][0]["call_detail"]
        self.assertEqual([row["call"] for row in rows], [1, 2, 3])
        self.assertEqual([row["gap_before_s"] for row in rows], [None, 10.0, 10.0])


class TestOutputIsAFloor(ProjectFixture):
    def test_calls_with_a_final_count_are_counted_and_reported(self):
        self.orchestrator(
            [
                record("msg_a", "2026-09-06T09:00:00.000Z", stop_reason="tool_use", output=500),
                record("msg_b", "2026-09-06T09:00:10.000Z", output=7),
            ]
        )
        payload = self.run_json()
        self.assertEqual(payload["sessions"][0]["calls"], 2)
        self.assertEqual(payload["sessions"][0]["calls_with_final_output"], 1)

        code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertIn("Output is a floor: 1 of 2 calls recorded a final `output_tokens`", out)

    def test_a_call_is_final_if_any_of_its_records_completed(self):
        self.orchestrator(
            [
                record("msg_a", "2026-09-06T09:00:00.000Z", output=2),
                record("msg_a", "2026-09-06T09:00:01.000Z", stop_reason="end_turn", output=90),
                record("msg_a", "2026-09-06T09:00:02.000Z", output=90),
            ]
        )
        payload = self.run_json()
        self.assertEqual(payload["sessions"][0]["calls_with_final_output"], 1)

    def test_the_per_call_table_marks_a_partial_output(self):
        self.orchestrator(
            [
                record("msg_a", "2026-09-06T09:00:00.000Z", stop_reason="tool_use", output=500),
                record("msg_b", "2026-09-06T09:00:10.000Z", output=7),
            ]
        )
        code, out, err = self.run_main("--calls")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.count("output partial"), 1)


class TestUnits(ProjectFixture):
    def test_each_token_class_is_weighted_by_its_own_multiplier(self):
        self.orchestrator(
            [
                record(
                    "msg_a",
                    "2026-09-06T09:00:00.000Z",
                    stop_reason="end_turn",
                    input_tokens=10,
                    write_5m=1000,
                    write_1h=100,
                    read=20000,
                    output=200,
                )
            ]
        )
        units = self.run_json()["sessions"][0]["units"]
        self.assertEqual(units["input"], 10.0)
        self.assertEqual(units["cache_read"], 2000.0)
        self.assertEqual(units["cache_write"], 1000 * 1.25 + 100 * 2.0)
        self.assertEqual(units["output"], 1000.0)
        self.assertEqual(units["total"], 10.0 + 2000.0 + 1450.0 + 1000.0)

    def test_a_one_hour_write_costs_more_than_the_same_tokens_at_five_minutes(self):
        # 1.25x against 2.0x: pricing every write at the 5-minute rate would
        # understate a 1-hour write by 1.6x, so the TTL split has to survive.
        self.orchestrator([record("msg_a", "2026-09-06T09:00:00.000Z", write_5m=1000, output=0)])
        five_minute = self.run_json()["sessions"][0]["units"]["cache_write"]
        self.orchestrator([record("msg_a", "2026-09-06T09:00:00.000Z", write_1h=1000, output=0)])
        one_hour = self.run_json()["sessions"][0]["units"]["cache_write"]
        self.assertEqual(five_minute, 1250.0)
        self.assertEqual(one_hour, 2000.0)

    def test_cache_reads_are_reported_apart_from_new_tokens(self):
        self.orchestrator(
            [
                record(
                    "msg_a",
                    "2026-09-06T09:00:00.000Z",
                    input_tokens=0,
                    write_5m=1000,
                    read=100000,
                    output=0,
                )
            ]
        )
        session = self.run_json()["sessions"][0]
        self.assertEqual(session["tokens"]["cache_read"], 100000)
        self.assertEqual(session["tokens"]["cache_write_5m"], 1000)
        # Reads are 10,000 units against 1,250 for the writes: 89% of the call.
        self.assertAlmostEqual(session["shares"]["cache_read"], 10000 / 11250)
        self.assertAlmostEqual(session["shares"]["cache_write"], 1250 / 11250)

    def test_multipliers_are_overridable(self):
        self.orchestrator([record("msg_a", "2026-09-06T09:00:00.000Z", read=1000, output=100)])
        payload = self.run_json("--read-multiplier", "0.025", "--output-multiplier", "10")
        units = payload["sessions"][0]["units"]
        self.assertEqual(units["cache_read"], 25.0)
        self.assertEqual(units["output"], 1000.0)
        self.assertEqual(payload["multipliers"]["read"], 0.025)

    def test_context_is_the_whole_input_side_of_the_last_call(self):
        self.orchestrator(
            [
                record("msg_a", "2026-09-06T09:00:00.000Z", input_tokens=2, write_5m=100, read=0),
                record("msg_b", "2026-09-06T09:00:10.000Z", input_tokens=2, write_5m=50, read=102),
            ]
        )
        self.assertEqual(self.run_json()["sessions"][0]["context"], 154)


class TestRewrites(ProjectFixture):
    def _expiring_dispatch(self):
        # Two warm appends, then a 377-second gap whose next call re-writes
        # nearly all of its context and keeps only a small shared prefix.
        return [
            record("msg_1", "2026-09-06T09:00:00.000Z", write_5m=2991, read=111151),
            record("msg_2", "2026-09-06T09:00:05.000Z", write_5m=2000, read=114144),
            record("msg_3", "2026-09-06T09:06:22.000Z", write_5m=87200, read=28325),
            record("msg_4", "2026-09-06T09:06:27.000Z", write_5m=2217, read=115525),
        ]

    def test_a_full_rewrite_is_flagged_with_the_gap_before_it(self):
        self.orchestrator(self._expiring_dispatch())
        rewrites = self.run_json()["sessions"][0]["rewrites"]
        self.assertEqual([entry["call"] for entry in rewrites], [3])
        self.assertEqual(rewrites[0]["gap_before_s"], 377.0)
        self.assertEqual(rewrites[0]["cache_write"], 87200)
        self.assertEqual(rewrites[0]["cache_read"], 28325)

    def test_a_warm_append_is_not_a_rewrite(self):
        self.orchestrator(self._expiring_dispatch())
        rows = self.run_json("--calls")["sessions"][0]["call_detail"]
        self.assertEqual([row["rewrite"] for row in rows], [False, False, True, False])

    def test_the_rewrite_outweighs_every_warm_call_around_it_combined(self):
        # This is why a re-write has to be broken out: one expired call costs
        # more than the three warm ones beside it put together.
        self.orchestrator(self._expiring_dispatch())
        rows = self.run_json("--calls")["sessions"][0]["call_detail"]
        warm = rows[0]["units"] + rows[1]["units"] + rows[3]["units"]
        self.assertGreater(rows[2]["units"], warm)
        self.assertGreater(rows[2]["units"], 7 * rows[3]["units"])

    def test_the_first_call_of_a_dispatch_that_writes_its_own_prefix_is_flagged(self):
        # In a parallel fan-out the first dispatch to arrive writes the shared
        # prefix and the rest read it, so its call 1 is a full re-write with no
        # gap before it.
        self.orchestrator([record("msg_0", "2026-09-06T09:00:00.000Z")])
        self.dispatch("aaaa", [record("msg_1", "2026-09-06T09:00:01.000Z", write_5m=36079)])
        self.dispatch(
            "bbbb", [record("msg_2", "2026-09-06T09:00:03.000Z", write_5m=7755, read=28325)]
        )
        by_label = {s["label"]: s for s in self.run_json()["sessions"]}
        self.assertEqual([r["call"] for r in by_label["aaaa"]["rewrites"]], [1])
        self.assertIsNone(by_label["aaaa"]["rewrites"][0]["gap_before_s"])
        self.assertEqual(by_label["bbbb"]["rewrites"], [])

    def test_the_threshold_is_overridable(self):
        self.orchestrator(self._expiring_dispatch())
        payload = self.run_json("--rewrite-fraction", "0.01")
        self.assertEqual(len(payload["sessions"][0]["rewrites"]), 4)
        self.assertEqual(payload["rewrite_fraction"], 0.01)

    def test_no_rewrite_is_said_so_rather_than_left_blank(self):
        self.orchestrator([record("msg_a", "2026-09-06T09:00:00.000Z", write_5m=1, read=10000)])
        code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertIn("No call wrote at least 50% of its own context.", out)


class TestLiveSessions(ProjectFixture):
    def test_a_session_whose_last_record_is_recent_is_marked_live(self):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        recent = (now - timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
        stale = (now - timedelta(seconds=4000)).isoformat().replace("+00:00", "Z")
        self.orchestrator([record("msg_a", recent)])
        self.dispatch("aaaa", [record("msg_b", stale)])
        by_label = {s["label"]: s for s in self.run_json()["sessions"]}
        self.assertTrue(by_label[SESSION]["live"])
        self.assertFalse(by_label["aaaa"]["live"])

    def test_the_snapshot_time_and_the_live_sessions_are_printed(self):
        from datetime import datetime, timedelta, timezone

        recent = (
            (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
        )
        self.orchestrator([record("msg_a", recent)])
        code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertIn("Snapshot ", out)
        self.assertIn("still live", out)
        self.assertIn(SESSION + "*", out)

    def test_the_live_window_is_overridable(self):
        from datetime import datetime, timedelta, timezone

        ago = (
            (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat().replace("+00:00", "Z")
        )
        self.orchestrator([record("msg_a", ago)])
        self.assertFalse(self.run_json()["sessions"][0]["live"])
        self.assertTrue(self.run_json("--live-window", "900")["sessions"][0]["live"])


class TestDiscovery(ProjectFixture):
    def test_an_orchestrator_is_listed_before_its_dispatches(self):
        self.orchestrator([record("msg_a", "2026-09-06T09:00:00.000Z")])
        self.dispatch("bbbb", [record("msg_c", "2026-09-06T09:00:02.000Z")])
        self.dispatch("aaaa", [record("msg_b", "2026-09-06T09:00:01.000Z")])
        payload = self.run_json()
        self.assertEqual(
            [(s["role"], s["label"]) for s in payload["sessions"]],
            [("orchestrator", SESSION), ("dispatch", "aaaa"), ("dispatch", "bbbb")],
        )

    def test_dispatches_are_reported_when_the_orchestrator_transcript_is_absent(self):
        # A session directory can precede or outlive its `{session}.jsonl`. Its
        # dispatches are real spend, so they are reported rather than refused.
        self.dispatch("aaaa", [record("msg_b", "2026-09-06T09:00:01.000Z")])
        payload = self.run_json()
        self.assertEqual([s["label"] for s in payload["sessions"]], ["aaaa"])
        self.assertEqual(payload["missing_orchestrator_transcripts"], [SESSION])
        code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertIn("No orchestrator transcript on disk for %s" % SESSION, out)

    def test_a_session_directory_holding_no_dispatches_is_ignored(self):
        self.orchestrator([record("msg_a", "2026-09-06T09:00:00.000Z")])
        os.makedirs(os.path.join(self.project, "other-session", "tool-results"))
        with open(
            os.path.join(self.project, "other-session", "tool-results", "hook.txt"), "w"
        ) as handle:
            handle.write("scratch")
        payload = self.run_json()
        self.assertEqual([s["label"] for s in payload["sessions"]], [SESSION])
        self.assertEqual(payload["missing_orchestrator_transcripts"], [])

    def test_session_selects_one_session_and_its_dispatches(self):
        other = "99999999-8888-7777-6666-555555555555"
        self.orchestrator([record("msg_a", "2026-09-06T09:00:00.000Z")])
        self.dispatch("aaaa", [record("msg_b", "2026-09-06T09:00:01.000Z")])
        self.orchestrator([record("msg_c", "2026-09-06T09:00:02.000Z")], session=other)
        self.dispatch("bbbb", [record("msg_d", "2026-09-06T09:00:03.000Z")], session=other)
        labels = [s["label"] for s in self.run_json("--session", other)["sessions"]]
        self.assertEqual(labels, [other, "bbbb"])

    def test_the_only_project_is_chosen_without_naming_it(self):
        self.orchestrator([record("msg_a", "2026-09-06T09:00:00.000Z")])
        self.assertEqual(self.run_json()["project"], self.project)

    def test_several_projects_are_refused_until_one_is_named(self):
        self.orchestrator([record("msg_a", "2026-09-06T09:00:00.000Z")])
        second = os.path.join(self.root, "-home-user-other")
        write_transcript(
            os.path.join(second, "s.jsonl"), [record("msg_b", "2026-09-06T09:00:00.000Z")]
        )
        code, out, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("--project", err)
        self.assertIn("-home-user-other", err)

        # A project name starts with `-`, so only the `--project=NAME` form
        # reaches argparse as a value rather than as an unknown option.
        code, out, err = self.run_main("--project=-home-user-other")
        self.assertEqual(code, 0, err)
        self.assertIn("-home-user-other", out)


class TestFailsLoudly(ProjectFixture):
    """Every unrecognised shape must be named, never reported as a zero.

    These fixtures hold a single transcript, so setting it aside leaves nothing
    to report and the run exits 1. `TestDegradesOneTranscriptAtATime` covers
    what happens when a healthy transcript sits beside the broken one.
    """

    def assert_refused(self, *argv, expect):
        code, out, err = self.run_main(*argv)
        self.assertEqual(code, 1, out)
        self.assertIn(expect, err)
        self.assertEqual(out, "")
        return err

    def test_a_missing_projects_root(self):
        code, out, err = self.run_main("--projects-root", os.path.join(self.tmp.name, "gone"))
        self.assertEqual(code, 1, out)
        self.assertIn("no projects directory at", err)

    def test_a_root_holding_no_transcript(self):
        self.assert_refused(expect="holds a transcript")

    def test_a_named_project_that_does_not_exist(self):
        self.orchestrator([record("msg_a", "2026-09-06T09:00:00.000Z")])
        self.assert_refused("--project=nope", expect="no project directory at")

    def test_a_session_that_does_not_exist(self):
        self.orchestrator([record("msg_a", "2026-09-06T09:00:00.000Z")])
        self.assert_refused("--session", "nope", expect="no transcript found for session nope")

    def test_a_transcript_with_no_usage_at_all(self):
        self.orchestrator([non_call_record("2026-09-06T09:00:00.000Z")])
        self.assert_refused(expect="no record carries `message.usage`")

    def test_a_line_that_is_not_json(self):
        # Not the last line: a malformed line mid-file is real corruption, not
        # the incomplete append that a live writer leaves at the tail.
        self.orchestrator(
            [
                record("msg_a", "2026-09-06T09:00:00.000Z"),
                "{not json",
                record("msg_b", "2026-09-06T09:00:01.000Z"),
            ]
        )
        err = self.assert_refused(expect="line is not JSON")
        self.assertIn(".jsonl:2", err)

    def test_a_line_that_is_not_an_object(self):
        self.orchestrator(
            [
                record("msg_a", "2026-09-06T09:00:00.000Z"),
                "[1, 2]",
                record("msg_b", "2026-09-06T09:00:01.000Z"),
            ]
        )
        self.assert_refused(expect="line is not a JSON object")

    def test_a_missing_usage_field(self):
        entry = record("msg_a", "2026-09-06T09:00:00.000Z")
        del entry["message"]["usage"]["cache_read_input_tokens"]
        self.orchestrator([entry])
        self.assert_refused(expect="usage has no integer `cache_read_input_tokens`")

    def test_a_usage_field_that_is_not_an_integer(self):
        entry = record("msg_a", "2026-09-06T09:00:00.000Z")
        entry["message"]["usage"]["output_tokens"] = "many"
        self.orchestrator([entry])
        self.assert_refused(expect="usage has no integer `output_tokens`")

    def test_usage_that_is_not_an_object(self):
        entry = record("msg_a", "2026-09-06T09:00:00.000Z")
        entry["message"]["usage"] = 42
        self.orchestrator([entry])
        self.assert_refused(expect="`message.usage` is not an object")

    def test_a_record_with_usage_but_no_message_id(self):
        entry = record("msg_a", "2026-09-06T09:00:00.000Z")
        del entry["message"]["id"]
        self.orchestrator([entry])
        self.assert_refused(expect="no `message.id` to group it by")

    def test_a_record_with_no_timestamp(self):
        entry = record("msg_a", "2026-09-06T09:00:00.000Z")
        del entry["timestamp"]
        self.orchestrator([entry])
        self.assert_refused(expect="record has no `timestamp` string")

    def test_a_timestamp_that_is_not_an_instant(self):
        self.orchestrator([record("msg_a", "the other day")])
        self.assert_refused(expect="is not an ISO 8601 instant")

    def test_records_of_one_message_disagreeing_on_the_input_side(self):
        # If this ever happened, one of the two is the real prompt size and
        # nothing on disk says which, so guessing would silently misprice.
        self.orchestrator(
            [
                record("msg_a", "2026-09-06T09:00:00.000Z", read=5000),
                record("msg_a", "2026-09-06T09:00:01.000Z", read=9000),
            ]
        )
        err = self.assert_refused(expect="must agree on the input side")
        self.assertIn("msg_a", err)

    def test_a_cache_creation_breakdown_that_does_not_sum(self):
        entry = record("msg_a", "2026-09-06T09:00:00.000Z", write_5m=1000)
        entry["message"]["usage"]["cache_creation"]["ephemeral_1h_input_tokens"] = 7
        self.orchestrator([entry])
        self.assert_refused(expect="`cache_creation` sums to 1007")

    def test_a_cache_creation_that_is_not_an_object(self):
        # `--assume-5m-writes` covers an absent breakdown, so it must not be
        # offered as the remedy here: passing it would reproduce this error.
        entry = record("msg_a", "2026-09-06T09:00:00.000Z", write_5m=1000)
        entry["message"]["usage"]["cache_creation"] = [{"ephemeral_5m_input_tokens": 1000}]
        self.orchestrator([entry])
        err = self.assert_refused(expect="`cache_creation` is not an object")
        self.assertNotIn("--assume-5m-writes", err)

        code, out, err = self.run_main("--assume-5m-writes")
        self.assertEqual(code, 1, out)
        self.assertIn("`cache_creation` is not an object", err)

    def test_a_missing_cache_creation_breakdown(self):
        self.orchestrator(
            [record("msg_a", "2026-09-06T09:00:00.000Z", write_5m=1000, breakdown=False)]
        )
        self.assert_refused(expect="--assume-5m-writes")

    def test_assume_5m_writes_prices_the_whole_write_at_the_five_minute_rate(self):
        self.orchestrator(
            [record("msg_a", "2026-09-06T09:00:00.000Z", write_5m=1000, breakdown=False)]
        )
        payload = self.run_json("--assume-5m-writes")
        session = payload["sessions"][0]
        self.assertEqual(session["tokens"]["cache_write_5m"], 1000)
        self.assertEqual(session["tokens"]["cache_write_1h"], 0)
        self.assertEqual(session["units"]["cache_write"], 1250.0)

    def test_a_record_that_wrote_nothing_needs_no_breakdown(self):
        # Zero tokens cost zero at either TTL, so there is no assumption to
        # make. Refusing here would push the operator into --assume-5m-writes,
        # which would then silently reprice real writes elsewhere in the run.
        self.orchestrator(
            [record("msg_a", "2026-09-06T09:00:00.000Z", read=5000, output=7, breakdown=False)]
        )
        session = self.run_json()["sessions"][0]
        self.assertEqual(session["calls"], 1)
        self.assertEqual(session["tokens"]["cache_write_5m"], 0)
        self.assertEqual(session["units"]["cache_write"], 0.0)

    def test_a_real_write_still_needs_its_breakdown_in_the_same_transcript(self):
        self.orchestrator(
            [
                record("msg_a", "2026-09-06T09:00:00.000Z", read=5000, breakdown=False),
                record("msg_b", "2026-09-06T09:00:10.000Z", write_5m=1000, breakdown=False),
            ]
        )
        self.assert_refused(expect="breakdown by TTL for its 1000 written tokens")


class TestDegradesOneTranscriptAtATime(ProjectFixture):
    """One unreadable transcript must not take the whole measurement with it.

    The figures cannot be recomputed tomorrow, so a session that has not yet
    written its first assistant turn must not be able to veto the report for
    every other session on the machine.
    """

    OTHER = "99999999-8888-7777-6666-555555555555"

    def test_a_healthy_transcript_is_still_reported_beside_an_unreadable_one(self):
        self.orchestrator(
            [record("msg_a", "2026-09-06T09:00:00.000Z", read=5000, output=9)],
        )
        self.orchestrator([non_call_record("2026-09-06T09:00:00.000Z")], session=self.OTHER)
        code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertIn(SESSION, out)
        self.assertIn("5,000", out)

    def test_the_transcript_that_was_set_aside_is_named_in_the_report(self):
        self.orchestrator([record("msg_a", "2026-09-06T09:00:00.000Z")])
        self.orchestrator([non_call_record("2026-09-06T09:00:00.000Z")], session=self.OTHER)
        code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertIn("1 transcript could not be read", out)
        self.assertIn(self.OTHER, out)
        self.assertIn("no record carries `message.usage`", out)

    def test_the_skip_also_reaches_stderr(self):
        self.orchestrator([record("msg_a", "2026-09-06T09:00:00.000Z")])
        self.orchestrator([non_call_record("2026-09-06T09:00:00.000Z")], session=self.OTHER)
        code, out, err = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("skipped", err)
        self.assertIn(self.OTHER, err)

    def test_json_carries_the_skips_under_unreadable(self):
        self.orchestrator([record("msg_a", "2026-09-06T09:00:00.000Z")])
        self.orchestrator([non_call_record("2026-09-06T09:00:00.000Z")], session=self.OTHER)
        payload = self.run_json()
        self.assertEqual([s["label"] for s in payload["sessions"]], [SESSION])
        self.assertEqual(len(payload["unreadable"]), 1)
        self.assertEqual(payload["unreadable"][0]["label"], self.OTHER)
        self.assertIn("no record carries `message.usage`", payload["unreadable"][0]["reason"])

    def test_an_empty_transcript_does_not_stop_the_report(self):
        self.orchestrator([record("msg_a", "2026-09-06T09:00:00.000Z")])
        write_transcript(os.path.join(self.project, self.OTHER + ".jsonl"), [])
        code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertIn(SESSION, out)
        self.assertIn(self.OTHER, out)

    def test_a_corrupt_dispatch_leaves_its_orchestrator_reported(self):
        self.orchestrator([record("msg_a", "2026-09-06T09:00:00.000Z")])
        self.dispatch(
            "aaaa",
            [
                record("msg_b", "2026-09-06T09:00:01.000Z"),
                "{torn",
                record("msg_c", "2026-09-06T09:00:02.000Z"),
            ],
        )
        payload = self.run_json()
        self.assertEqual([s["label"] for s in payload["sessions"]], [SESSION])
        self.assertEqual(payload["unreadable"][0]["label"], "aaaa")

    def test_nothing_readable_is_still_an_error_with_no_report(self):
        self.orchestrator([non_call_record("2026-09-06T09:00:00.000Z")])
        self.orchestrator([non_call_record("2026-09-06T09:00:00.000Z")], session=self.OTHER)
        code, out, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("no transcript could be read", err)


class TestTornFinalLine(ProjectFixture):
    """A live writer can be read mid-append, so the tail is held back."""

    def test_an_unparsable_final_line_is_dropped_not_fatal(self):
        self.orchestrator(
            [
                record("msg_a", "2026-09-06T09:00:00.000Z", read=5000),
                record("msg_b", "2026-09-06T09:00:10.000Z", read=6000),
                '{"type": "assistant", "message": {"id": "msg_c", "usa',
            ]
        )
        payload = self.run_json()
        session = payload["sessions"][0]
        self.assertEqual(session["calls"], 2)
        self.assertEqual(session["tokens"]["cache_read"], 11000)
        self.assertEqual(payload["unreadable"], [])

    def test_the_dropped_line_is_noted_in_the_report(self):
        self.orchestrator([record("msg_a", "2026-09-06T09:00:00.000Z"), '{"type": "assist'])
        payload = self.run_json()
        self.assertEqual(
            payload["sessions"][0]["notes"], ["line 2 was still being written and was skipped"]
        )
        code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertIn("line 2 was still being written and was skipped", out)

    def test_a_complete_final_line_is_still_read(self):
        self.orchestrator(
            [
                record("msg_a", "2026-09-06T09:00:00.000Z", read=5000),
                record("msg_b", "2026-09-06T09:00:10.000Z", read=6000),
            ]
        )
        payload = self.run_json()
        self.assertEqual(payload["sessions"][0]["calls"], 2)
        self.assertEqual(payload["sessions"][0]["notes"], [])

    def test_a_torn_tail_after_a_trailing_blank_line_is_still_the_tail(self):
        path = os.path.join(self.project, SESSION + ".jsonl")
        write_transcript(path, [record("msg_a", "2026-09-06T09:00:00.000Z")])
        with open(path, "a", encoding="utf-8") as handle:
            handle.write('\n{"type": "assist\n')
        payload = self.run_json()
        self.assertEqual(payload["sessions"][0]["calls"], 1)
        self.assertEqual(len(payload["sessions"][0]["notes"]), 1)


class TestRendering(ProjectFixture):
    def test_the_table_carries_every_component_and_a_total(self):
        self.orchestrator(
            [
                record(
                    "msg_a",
                    "2026-09-06T09:00:00.000Z",
                    stop_reason="end_turn",
                    write_5m=1000,
                    read=20000,
                    output=200,
                )
            ]
        )
        self.dispatch("aaaa", [record("msg_b", "2026-09-06T09:00:01.000Z", read=500)])
        code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        header = next(line for line in out.splitlines() if line.startswith("session "))
        for column in ["calls", "ctx", "input", "reads", "writes", "output", "units"]:
            self.assertIn(column, header)
        for column in ["read%", "write%", "out%"]:
            self.assertIn(column, header)
        self.assertIn("orchestrator", out)
        self.assertIn("dispatch", out)
        self.assertIn("TOTAL", out)
        self.assertIn("2 sessions", out)

    def test_ctx_is_explained_as_a_last_call_figure_not_a_total(self):
        # It sits in a row of columns that are all totals, so the report has to
        # say that this one is not.
        self.orchestrator([record("msg_a", "2026-09-06T09:00:00.000Z")])
        code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertIn("`ctx` is the context the session's last call sat on, not a total.", out)

    def test_the_multipliers_in_force_are_printed(self):
        self.orchestrator([record("msg_a", "2026-09-06T09:00:00.000Z")])
        code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertIn(
            "input 1x, cache read 0.1x, 5-minute write 1.25x, 1-hour write 2x, output 5x", out
        )

    def test_overridden_multipliers_are_the_ones_printed(self):
        self.orchestrator([record("msg_a", "2026-09-06T09:00:00.000Z")])
        code, out, err = self.run_main("--read-multiplier", "0.025")
        self.assertEqual(code, 0, err)
        self.assertIn("cache read 0.025x", out)

    def test_json_carries_the_snapshot_and_the_settings_in_force(self):
        self.orchestrator([record("msg_a", "2026-09-06T09:00:00.000Z")])
        payload = self.run_json()
        self.assertRegex(payload["snapshot"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(payload["multipliers"]["write_1h"], 2.0)
        self.assertEqual(payload["live_window_seconds"], 300.0)

    def test_call_detail_is_absent_unless_asked_for(self):
        self.orchestrator([record("msg_a", "2026-09-06T09:00:00.000Z")])
        self.assertNotIn("call_detail", self.run_json()["sessions"][0])
        self.assertIn("call_detail", self.run_json("--calls")["sessions"][0])

    def test_thousands_separators_are_used(self):
        self.orchestrator([record("msg_a", "2026-09-06T09:00:00.000Z", read=1234567)])
        code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertIn("1,234,567", out)


class TestHouseRules(unittest.TestCase):
    MODULE = os.path.join(os.path.dirname(__file__), "dispatch_cost.py")

    def test_no_third_party_imports(self):
        with open(self.MODULE, encoding="utf-8") as handle:
            source = handle.read()
        names = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.add(node.module.split(".")[0])
        self.assertEqual({name for name in names if name not in sys.stdlib_module_names}, set())

    def test_the_script_is_executable(self):
        self.assertTrue(os.stat(self.MODULE).st_mode & stat.S_IXUSR)

    def test_the_multiplier_defaults_are_the_published_ratios(self):
        self.assertEqual(dc.READ_MULTIPLIER, 0.1)
        self.assertEqual(dc.WRITE_5M_MULTIPLIER, 1.25)
        self.assertEqual(dc.WRITE_1H_MULTIPLIER, 2.0)
        self.assertEqual(dc.OUTPUT_MULTIPLIER, 5.0)


if __name__ == "__main__":
    unittest.main()
