#!/usr/bin/env python3
"""Unit tests for watch_toolchain_bump.py.

Every HTTP call is mocked; nothing here reaches Maven Central, raw.githubusercontent.com, OSV, or
the GitHub API.
"""

import io
import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))
import watch_toolchain_bump as wtb  # noqa: E402
from file_test_failure_issues import IssueLookup  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# The Kotlin row exactly as github/codeql ships it at codeql-cli/v2.26.2, RST markup and all.
KOTLIN_ROW = r'   Kotlin,"Kotlin 1.8.0 to 2.4.1\ *x*","kotlinc",``.kt``'

SUPPORTED_VERSIONS_RST = "\n".join(
    [
        ".. csv-table::",
        "   :header-rows: 1",
        "",
        "   Language,Variants,Compilers,Extensions",
        r'   Java,"Java 7 to 26 [6]_","javac (OpenJDK and Oracle JDK)",``.java``',
        KOTLIN_ROW,
        r"   Python [9]_,\"2.7, 3.13\",Not applicable,``.py``",
    ]
)

# Abridged from the real document. <latest>/<release> deliberately name a Beta, which is what
# they really hold, so a test fails if the parser starts trusting them.
KGP_METADATA_XML = """<?xml version="1.0" encoding="UTF-8"?>
<metadata>
  <groupId>org.jetbrains.kotlin</groupId>
  <artifactId>kotlin-gradle-plugin</artifactId>
  <versioning>
    <latest>2.4.20-Beta2</latest>
    <release>2.4.20-Beta2</release>
    <versions>
      <version>0.0.1-test-deploy</version>
      <version>2.4.0-RC2</version>
      <version>2.4.0</version>
      <version>2.4.9</version>
      <version>2.4.10-RC</version>
      <version>2.4.10</version>
      <version>2.4.20-Beta1</version>
      <version>2.4.20-Beta2</version>
    </versions>
  </versioning>
</metadata>
"""

WRAPPER_PROPERTIES = textwrap.dedent(
    """\
    distributionBase=GRADLE_USER_HOME
    distributionSha256Sum=bafc141b619ad6350fd975fc903156dd5c151998cc8b058e8c1044ab5f7b031f
    distributionUrl=https\\://services.gradle.org/distributions/gradle-9.5.1-bin.zip
    """
)

BUILD_GRADLE_KTS = textwrap.dedent(
    """\
    buildscript {
        dependencies {
            classpath("org.jetbrains.kotlin:kotlin-gradle-plugin:2.4.0")
        }
    }

    plugins {
        id("com.android.application") version "9.1.0" apply false
        id("org.jetbrains.kotlin.plugin.compose") version "2.4.0" apply false
    }
    """
)

CODEQL_WORKFLOW = textwrap.dedent(
    """\
    jobs:
      analyze-kotlin:
        steps:
          - uses: github/codeql-action/init@v4
          - uses: github/codeql-action/analyze@v4
    """
)


def write_repo(root: Path, *, codeql_workflow: str = CODEQL_WORKFLOW) -> Path:
    """Materialize the three files read_pinned_versions/read_codeql_action_ref read."""
    (root / "gradle" / "wrapper").mkdir(parents=True, exist_ok=True)
    (root / "gradle" / "wrapper" / "gradle-wrapper.properties").write_text(WRAPPER_PROPERTIES)
    (root / "build.gradle.kts").write_text(BUILD_GRADLE_KTS)
    (root / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    (root / ".github" / "workflows" / "codeql.yml").write_text(codeql_workflow)
    return root


def make_state(**overrides) -> dict:
    """A complete, plausible observation, overridable field by field."""
    state = {
        "pinned": {"gradle": "9.5.1", "agp": "9.1.0", "kgp": "2.4.0"},
        "latest_stable_kgp": "2.4.10",
        "codeql_action_ref": "v4",
        "codeql_cli_version": "2.26.2",
        "codeql_kotlin_ceiling": "2.4.1x",
        "toolchain_advisories": {
            "org.gradle:gradle-core@9.5.1": [],
            "com.android.tools.build:gradle@9.1.0": [],
            "org.jetbrains.kotlin:kotlin-gradle-plugin@2.4.0": [],
        },
        "errors": [],
    }
    state.update(overrides)
    return state


def _response(status: int = 200, text: str = "", payload: object = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.json.return_value = payload
    if status >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status}")
    return resp


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------


class TestVersionHelpers(unittest.TestCase):
    def test_stable_versions_recognized(self):
        for version in ("2.4.0", "2.4.10", "9.5.1", "1.8"):
            self.assertTrue(wtb.is_stable(version), version)

    def test_prereleases_rejected(self):
        for version in ("2.4.20-Beta2", "2.4.10-RC", "0.0.1-test-deploy", "2.4.1x"):
            self.assertFalse(wtb.is_stable(version), version)

    def test_version_key_orders_numerically_not_lexically(self):
        self.assertGreater(wtb.version_key("2.4.10"), wtb.version_key("2.4.9"))

    def test_ceiling_wildcard_expands_to_highest_admitted_version(self):
        self.assertEqual(wtb.ceiling_inclusive_max("2.4.1x"), "2.4.19")
        self.assertEqual(wtb.ceiling_inclusive_max("2.4.0x"), "2.4.09")

    def test_ceiling_without_wildcard_is_its_own_maximum(self):
        self.assertEqual(wtb.ceiling_inclusive_max("2.5.0"), "2.5.0")

    def test_ceiling_covers_version_inside_the_wildcard(self):
        self.assertTrue(wtb.ceiling_covers("2.4.1x", "2.4.10"))
        self.assertTrue(wtb.ceiling_covers("2.4.1x", "2.4.19"))

    def test_ceiling_does_not_cover_next_minor(self):
        self.assertFalse(wtb.ceiling_covers("2.4.1x", "2.4.20"))
        self.assertFalse(wtb.ceiling_covers("2.4.0x", "2.4.10"))

    def test_ceiling_coverage_unknown_when_an_input_is_missing(self):
        self.assertIsNone(wtb.ceiling_covers(None, "2.4.10"))
        self.assertIsNone(wtb.ceiling_covers("2.4.1x", None))


# ---------------------------------------------------------------------------
# Parsing the CodeQL Kotlin ceiling
# ---------------------------------------------------------------------------


class TestParseKotlinCeiling(unittest.TestCase):
    def test_reads_the_real_row_shape(self):
        self.assertEqual(wtb.parse_kotlin_ceiling(SUPPORTED_VERSIONS_RST), "2.4.1x")

    def test_reads_a_bound_without_a_wildcard(self):
        rst = '   Kotlin,"Kotlin 1.8.0 to 2.5.0","kotlinc",``.kt``'
        self.assertEqual(wtb.parse_kotlin_ceiling(rst), "2.5.0")

    def test_tolerates_a_footnote_marker_on_the_label(self):
        """The real codeql-cli/v2.18.0 row, from when Kotlin support carried a caveat."""
        rst = r'   Kotlin [7]_,"Kotlin 1.5.0 to 2.0.0\ *x*","kotlinc",``.kt``'
        self.assertEqual(wtb.parse_kotlin_ceiling(rst), "2.0.0x")

    def test_tolerates_a_variants_cell_that_omits_the_language_name(self):
        """Most rows of this table omit it already (TypeScript "2.6-5.9", Ruby "up to 3.3")."""
        rst = r'   Kotlin,"1.8.0 to 2.4.1\ *x*","kotlinc",``.kt``'
        self.assertEqual(wtb.parse_kotlin_ceiling(rst), "2.4.1x")

    def test_tolerates_an_unquoted_variants_cell(self):
        rst = "   Kotlin,Kotlin 1.8.0 to 2.4.1x,kotlinc,``.kt``"
        self.assertEqual(wtb.parse_kotlin_ceiling(rst), "2.4.1x")

    def test_the_bound_is_read_from_the_variants_cell_only(self):
        """A "to <version>" elsewhere on the row must not be mistaken for the ceiling."""
        rst = '   Kotlin,"Kotlin 1.8.0 to 2.4.1x","kotlinc up to 9.9.9",``.kt``'
        self.assertEqual(wtb.parse_kotlin_ceiling(rst), "2.4.1x")

    def test_missing_kotlin_row_fails_loudly(self):
        rst = "\n".join(
            line for line in SUPPORTED_VERSIONS_RST.splitlines() if "Kotlin," not in line
        )
        with self.assertRaises(wtb.FormatError):
            wtb.parse_kotlin_ceiling(rst)

    def test_reworded_kotlin_row_fails_loudly(self):
        rst = '   Kotlin,"Kotlin 1.8.0 through 2.4.1x","kotlinc",``.kt``'
        with self.assertRaises(wtb.FormatError):
            wtb.parse_kotlin_ceiling(rst)

    def test_a_kotlin_prefixed_language_is_not_mistaken_for_the_kotlin_row(self):
        rst = '   Kotlin/Native,"Kotlin 1.8.0 to 9.9.9","kotlinc",``.kt``'
        with self.assertRaises(wtb.FormatError):
            wtb.parse_kotlin_ceiling(rst)


# ---------------------------------------------------------------------------
# Parsing the latest stable KGP
# ---------------------------------------------------------------------------


class TestParseLatestStableKgp(unittest.TestCase):
    def test_picks_the_highest_stable_version(self):
        self.assertEqual(wtb.parse_latest_stable_kgp(KGP_METADATA_XML), "2.4.10")

    def test_ignores_latest_and_release_elements(self):
        """<latest>/<release> hold a Beta here; trusting them would return 2.4.20-Beta2."""
        self.assertNotIn("Beta", wtb.parse_latest_stable_kgp(KGP_METADATA_XML))

    def test_malformed_xml_fails_loudly(self):
        with self.assertRaises(wtb.FormatError):
            wtb.parse_latest_stable_kgp("<metadata><versioning>")

    def test_empty_version_list_fails_loudly(self):
        with self.assertRaises(wtb.FormatError):
            wtb.parse_latest_stable_kgp("<metadata><versioning><versions/></versioning></metadata>")

    def test_all_prerelease_list_fails_loudly(self):
        xml = (
            "<metadata><versioning><versions>"
            "<version>2.4.20-Beta1</version>"
            "</versions></versioning></metadata>"
        )
        with self.assertRaises(wtb.FormatError):
            wtb.parse_latest_stable_kgp(xml)


# ---------------------------------------------------------------------------
# Parsing the CodeQL CLI version
# ---------------------------------------------------------------------------


class TestParseCodeqlCliVersion(unittest.TestCase):
    def test_reads_cli_version(self):
        payload = json.dumps({"bundleVersion": "codeql-bundle-v2.26.2", "cliVersion": "2.26.2"})
        self.assertEqual(wtb.parse_codeql_cli_version(payload), "2.26.2")

    def test_missing_key_fails_loudly(self):
        with self.assertRaises(wtb.FormatError):
            wtb.parse_codeql_cli_version(json.dumps({"bundleVersion": "x"}))

    def test_invalid_json_fails_loudly(self):
        with self.assertRaises(wtb.FormatError):
            wtb.parse_codeql_cli_version("not json")


# ---------------------------------------------------------------------------
# Reading this repository's pins
# ---------------------------------------------------------------------------


class TestReadRepositoryPins(unittest.TestCase):
    def test_reads_gradle_agp_and_kgp(self):
        with tempfile.TemporaryDirectory() as tmp:
            pinned = wtb.read_pinned_versions(write_repo(Path(tmp)))
        self.assertEqual(pinned, {"gradle": "9.5.1", "agp": "9.1.0", "kgp": "2.4.0"})

    def test_missing_file_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(wtb.FormatError):
                wtb.read_pinned_versions(Path(tmp))

    def test_restructured_build_file_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = write_repo(Path(tmp))
            (root / "build.gradle.kts").write_text("plugins { }\n")
            with self.assertRaises(wtb.FormatError):
                wtb.read_pinned_versions(root)

    def test_reads_the_codeql_action_ref_from_the_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(wtb.read_codeql_action_ref(write_repo(Path(tmp))), "v4")

    def test_mixed_codeql_action_refs_use_the_most_common(self):
        workflow = CODEQL_WORKFLOW + "          - uses: github/codeql-action/init@v3\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = write_repo(Path(tmp), codeql_workflow=workflow)
            self.assertEqual(wtb.read_codeql_action_ref(root), "v4")

    def test_workflow_without_codeql_action_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = write_repo(Path(tmp), codeql_workflow="jobs: {}\n")
            with self.assertRaises(wtb.FormatError):
                wtb.read_codeql_action_ref(root)

    def test_default_repo_root_points_at_this_checkout(self):
        self.assertTrue((wtb._default_repo_root() / "build.gradle.kts").is_file())


# ---------------------------------------------------------------------------
# HTTP behaviour
# ---------------------------------------------------------------------------


class TestFetchText(unittest.TestCase):
    @patch("watch_toolchain_bump.requests")
    def test_returns_body(self, mock_requests):
        mock_requests.get.return_value = _response(text="hello")
        self.assertEqual(wtb.fetch_text("https://example.test/x"), "hello")

    @patch("watch_toolchain_bump.requests")
    def test_404_is_a_format_error(self, mock_requests):
        """A moved or renamed upstream document must be reported, not treated as a blip."""
        mock_requests.get.return_value = _response(status=404)
        with self.assertRaises(wtb.FormatError):
            wtb.fetch_text("https://example.test/x")

    @patch("watch_toolchain_bump.requests")
    def test_server_error_is_transient(self, mock_requests):
        mock_requests.get.return_value = _response(status=503)
        with self.assertRaises(wtb.TransientFetchError):
            wtb.fetch_text("https://example.test/x")

    @patch("watch_toolchain_bump.requests")
    def test_network_failure_is_transient(self, mock_requests):
        mock_requests.get.side_effect = Exception("connection reset")
        with self.assertRaises(wtb.TransientFetchError):
            wtb.fetch_text("https://example.test/x")


class TestQueryOsv(unittest.TestCase):
    @patch("watch_toolchain_bump.requests")
    def test_no_advisories_returns_empty_list(self, mock_requests):
        mock_requests.post.return_value = _response(payload={})
        self.assertEqual(wtb.query_osv("org.gradle:gradle-core", "9.5.1"), [])

    @patch("watch_toolchain_bump.requests")
    def test_advisory_ids_returned_sorted(self, mock_requests):
        mock_requests.post.return_value = _response(
            payload={"vulns": [{"id": "GHSA-zzz"}, {"id": "GHSA-aaa"}]}
        )
        self.assertEqual(wtb.query_osv("org.gradle:gradle-core", "9.5.1"), ["GHSA-aaa", "GHSA-zzz"])

    @patch("watch_toolchain_bump.requests")
    def test_queries_the_maven_ecosystem_at_the_pinned_version(self, mock_requests):
        mock_requests.post.return_value = _response(payload={})
        wtb.query_osv("com.android.tools.build:gradle", "9.1.0")
        payload = mock_requests.post.call_args[1]["json"]
        self.assertEqual(payload["package"]["ecosystem"], "Maven")
        self.assertEqual(payload["package"]["name"], "com.android.tools.build:gradle")
        self.assertEqual(payload["version"], "9.1.0")

    @patch("watch_toolchain_bump.requests")
    def test_network_failure_is_transient(self, mock_requests):
        mock_requests.post.side_effect = Exception("timeout")
        with self.assertRaises(wtb.TransientFetchError):
            wtb.query_osv("org.gradle:gradle-core", "9.5.1")


# ---------------------------------------------------------------------------
# collect_state
# ---------------------------------------------------------------------------


class TestCollectState(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = write_repo(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _fake_fetch(self, *, rst: str = SUPPORTED_VERSIONS_RST):
        def fetch(url: str) -> str:
            if "maven-metadata.xml" in url:
                return KGP_METADATA_XML
            if "defaults.json" in url:
                return json.dumps({"cliVersion": "2.26.2"})
            if "supported-versions-compilers.rst" in url:
                return rst
            raise AssertionError(f"unexpected URL {url}")

        return fetch

    def test_observes_every_fact(self):
        with patch.object(wtb, "fetch_text", side_effect=self._fake_fetch()):
            with patch.object(wtb, "query_osv", return_value=[]):
                state = wtb.collect_state(self.root)
        self.assertEqual(state, make_state())

    def test_codeql_tag_is_built_from_the_observed_cli_version(self):
        seen = []

        def fetch(url):
            seen.append(url)
            return self._fake_fetch()(url)

        with patch.object(wtb, "fetch_text", side_effect=fetch):
            with patch.object(wtb, "query_osv", return_value=[]):
                wtb.collect_state(self.root)
        self.assertTrue(any("/codeql-cli/v2.26.2/" in url for url in seen), seen)
        self.assertTrue(any("codeql-action/v4/src/defaults.json" in url for url in seen), seen)

    def test_unparseable_kotlin_row_is_recorded_as_an_error_not_as_no_movement(self):
        rst = '   Kotlin,"Kotlin 1.8.0 through 2.4.1x","kotlinc",``.kt``'
        with patch.object(wtb, "fetch_text", side_effect=self._fake_fetch(rst=rst)):
            with patch.object(wtb, "query_osv", return_value=[]):
                state = wtb.collect_state(self.root)
        self.assertIsNone(state["codeql_kotlin_ceiling"])
        self.assertEqual(len(state["errors"]), 1)
        self.assertIn("Kotlin row", state["errors"][0])

    def test_transient_fetch_failure_propagates(self):
        with patch.object(wtb, "fetch_text", side_effect=wtb.TransientFetchError("down")):
            with patch.object(wtb, "query_osv", return_value=[]):
                with self.assertRaises(wtb.TransientFetchError):
                    wtb.collect_state(self.root)

    def test_unreadable_pins_do_not_stop_the_upstream_observation(self):
        (self.root / "build.gradle.kts").unlink()
        with patch.object(wtb, "fetch_text", side_effect=self._fake_fetch()):
            with patch.object(wtb, "query_osv", return_value=[]) as mock_osv:
                state = wtb.collect_state(self.root)
        self.assertEqual(state["latest_stable_kgp"], "2.4.10")
        self.assertEqual(state["codeql_kotlin_ceiling"], "2.4.1x")
        self.assertTrue(state["errors"])
        # With no pins there is nothing to ask OSV about, and the empty advisory map is a change
        # from the previous observation, so this run still reports.
        self.assertEqual(mock_osv.call_count, 0)
        self.assertEqual(state["toolchain_advisories"], {})


# ---------------------------------------------------------------------------
# State round-trip through comments
# ---------------------------------------------------------------------------


class TestStateRoundTrip(unittest.TestCase):
    def test_state_survives_render_and_parse(self):
        state = make_state()
        self.assertEqual(wtb.parse_state_block(wtb.render_state_block(state)), state)

    def test_state_survives_a_full_comment(self):
        state = make_state()
        self.assertEqual(wtb.parse_state_block(wtb.render_comment(state, None)), state)

    def test_comment_without_marker_carries_no_state(self):
        self.assertIsNone(wtb.parse_state_block("Looks fine to me."))

    def test_corrupt_state_block_is_ignored(self):
        self.assertIsNone(wtb.parse_state_block("<!-- toolchain-bump-watch-state {oops -->"))

    def test_latest_recorded_state_takes_the_most_recent_marked_comment(self):
        older = make_state(latest_stable_kgp="2.4.0")
        newer = make_state(latest_stable_kgp="2.4.10")
        comments = [
            {"body": wtb.render_state_block(older)},
            {"body": wtb.render_state_block(newer)},
        ]
        self.assertEqual(wtb.latest_recorded_state(comments), newer)

    def test_a_human_reply_does_not_lose_the_watchers_place(self):
        state = make_state()
        comments = [
            {"body": wtb.render_state_block(state)},
            {"body": "Checked the table; still the same row."},
        ]
        self.assertEqual(wtb.latest_recorded_state(comments), state)

    def test_no_marked_comment_means_no_prior_state(self):
        self.assertIsNone(wtb.latest_recorded_state([{"body": "hello"}]))
        self.assertIsNone(wtb.latest_recorded_state([]))


class TestFetchIssueComments(unittest.TestCase):
    @patch("watch_toolchain_bump.requests")
    def test_paginates_until_a_short_page(self, mock_requests):
        first = _response(payload=[{"body": f"c{i}"} for i in range(100)])
        second = _response(payload=[{"body": "last"}])
        mock_requests.get.side_effect = [first, second]
        comments = wtb.fetch_issue_comments("tok", "owner/repo", 7)
        self.assertEqual(len(comments), 101)
        self.assertEqual(mock_requests.get.call_count, 2)

    @patch("watch_toolchain_bump.requests")
    def test_stops_on_an_empty_page(self, mock_requests):
        mock_requests.get.return_value = _response(payload=[])
        self.assertEqual(wtb.fetch_issue_comments("tok", "owner/repo", 7), [])
        self.assertEqual(mock_requests.get.call_count, 1)


# ---------------------------------------------------------------------------
# Change detection and rendering
# ---------------------------------------------------------------------------


class TestChangedFacts(unittest.TestCase):
    def test_identical_states_have_no_changed_facts(self):
        self.assertEqual(wtb.changed_facts(make_state(), make_state()), [])

    def test_a_new_kgp_release_is_named(self):
        changed = wtb.changed_facts(make_state(latest_stable_kgp="2.4.20"), make_state())
        self.assertEqual(changed, ["highest stable KGP published"])

    def test_a_moved_ceiling_is_named(self):
        changed = wtb.changed_facts(make_state(codeql_kotlin_ceiling="2.4.2x"), make_state())
        self.assertEqual(changed, ["CodeQL Kotlin ceiling"])

    def test_a_local_bump_is_named(self):
        bumped = make_state(pinned={"gradle": "9.7.0", "agp": "9.1.0", "kgp": "2.4.10"})
        self.assertEqual(
            wtb.changed_facts(bumped, make_state()), ["versions pinned in this repository"]
        )


class TestRenderComment(unittest.TestCase):
    def test_first_observation_is_labelled_as_such(self):
        body = wtb.render_comment(make_state(), None)
        self.assertIn("First observation recorded", body)

    def test_changed_facts_are_named_in_the_heading(self):
        body = wtb.render_comment(make_state(latest_stable_kgp="2.4.20"), make_state())
        self.assertIn("Changed: highest stable KGP published", body)

    def test_reports_a_kgp_above_the_pin(self):
        body = wtb.render_comment(make_state(), None)
        self.assertIn("A stable KGP above the pinned 2.4.0 is published: **2.4.10**", body)

    def test_reports_when_nothing_is_above_the_pin(self):
        state = make_state(latest_stable_kgp="2.4.0")
        self.assertIn("No stable KGP above the pinned 2.4.0", wtb.render_comment(state, None))

    def test_reports_a_ceiling_that_does_not_cover_the_new_kgp(self):
        state = make_state(latest_stable_kgp="2.4.20")
        body = wtb.render_comment(state, None)
        self.assertIn("does **not** cover KGP 2.4.20", body)

    def test_an_unreadable_input_is_flagged_as_not_a_no_movement_report(self):
        state = make_state(codeql_kotlin_ceiling=None, errors=["the Kotlin row moved"])
        body = wtb.render_comment(state, None)
        self.assertIn("[!WARNING]", body)
        self.assertIn("not** a report of no movement", body)
        self.assertIn("the Kotlin row moved", body)

    def test_a_clean_run_carries_no_warning(self):
        self.assertNotIn("[!WARNING]", wtb.render_comment(make_state(), None))

    def test_a_toolchain_advisory_is_called_out_as_actionable(self):
        state = make_state(
            toolchain_advisories={"org.gradle:gradle-core@9.5.1": ["GHSA-test-1234"]}
        )
        body = wtb.render_comment(state, None)
        self.assertIn("GHSA-test-1234", body)
        self.assertIn("directly actionable", body)

    def test_an_unqueryable_coordinate_does_not_render_as_no_advisories(self):
        state = make_state(
            toolchain_advisories={
                "org.gradle:gradle-core@9.5.1": None,
                "com.android.tools.build:gradle@9.1.0": [],
            },
            errors=["OSV returned a list, not an object"],
        )
        body = wtb.render_comment(state, None)
        self.assertIn(f"org.gradle:gradle-core@9.5.1: {wtb._UNKNOWN}", body)

    def test_all_coordinates_clean_renders_as_none(self):
        self.assertIn("| none |", wtb.render_comment(make_state(), None))

    def test_a_differing_state_with_no_tracked_change_does_not_say_changed_nothing(self):
        """Reachable when a recorded state carries a key this version no longer tracks."""
        prior = make_state()
        prior["retired_fact"] = "gone"
        body = wtb.render_comment(make_state(), prior)
        self.assertNotIn("Changed: \n", body)
        self.assertIn("no tracked fact moved", body)

    def test_says_it_does_not_bump_anything(self):
        self.assertIn("does not bump anything", wtb.render_comment(make_state(), None))


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


class TestMain(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = write_repo(Path(self._tmp.name))
        self._env = patch.dict(
            os.environ,
            {
                "GITHUB_TOKEN": "tok",
                "GITHUB_REPOSITORY": "owner/repo",
                "REPO_ROOT": str(self.root),
            },
        )
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_exit_0_when_token_missing(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}):
            with patch.object(wtb, "collect_state") as mock_collect:
                self.assertEqual(wtb.main([]), 0)
        mock_collect.assert_not_called()

    def test_exit_0_when_repository_missing(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": ""}):
            with patch.object(wtb, "collect_state") as mock_collect:
                self.assertEqual(wtb.main([]), 0)
        mock_collect.assert_not_called()

    def test_transient_failure_reports_nothing(self):
        with patch.object(wtb, "collect_state", side_effect=wtb.TransientFetchError("down")):
            with patch.object(wtb, "find_tracking_issue") as mock_find:
                self.assertEqual(wtb.main([]), 0)
        mock_find.assert_not_called()

    def test_creates_the_tracking_issue_and_posts_the_first_observation(self):
        with patch.object(wtb, "collect_state", return_value=make_state()):
            with patch.object(wtb, "find_tracking_issue", return_value=IssueLookup(True)):
                with patch.object(wtb, "create_issue", return_value=321) as mock_create:
                    with patch.object(wtb, "add_issue_comment") as mock_comment:
                        self.assertEqual(wtb.main([]), 0)
        self.assertEqual(mock_create.call_args[0][2], wtb.TRACKING_ISSUE_TITLE)
        self.assertEqual(mock_create.call_args[1]["labels"], [wtb.TRACKING_ISSUE_LABEL])
        self.assertEqual(mock_comment.call_args[0][2], 321)
        self.assertIn("First observation recorded", mock_comment.call_args[0][3])

    def test_gives_up_quietly_when_the_issue_cannot_be_created(self):
        with patch.object(wtb, "collect_state", return_value=make_state()):
            with patch.object(wtb, "find_tracking_issue", return_value=IssueLookup(True)):
                with patch.object(wtb, "create_issue", return_value=None):
                    with patch.object(wtb, "add_issue_comment") as mock_comment:
                        self.assertEqual(wtb.main([]), 0)
        mock_comment.assert_not_called()

    def test_unchanged_state_posts_nothing(self):
        state = make_state()
        comments = [{"body": wtb.render_state_block(state)}]
        with patch.object(wtb, "collect_state", return_value=state):
            with patch.object(
                wtb, "find_tracking_issue", return_value=IssueLookup(True, 9, "open")
            ):
                with patch.object(wtb, "fetch_issue_comments", return_value=comments):
                    with patch.object(wtb, "add_issue_comment") as mock_comment:
                        self.assertEqual(wtb.main([]), 0)
        mock_comment.assert_not_called()

    def test_changed_state_posts_a_comment(self):
        prior = make_state(latest_stable_kgp="2.4.0")
        comments = [{"body": wtb.render_state_block(prior)}]
        with patch.object(wtb, "collect_state", return_value=make_state()):
            with patch.object(
                wtb, "find_tracking_issue", return_value=IssueLookup(True, 9, "open")
            ):
                with patch.object(wtb, "fetch_issue_comments", return_value=comments):
                    with patch.object(wtb, "add_issue_comment") as mock_comment:
                        self.assertEqual(wtb.main([]), 0)
        self.assertEqual(mock_comment.call_args[0][2], 9)
        self.assertIn("Changed: highest stable KGP published", mock_comment.call_args[0][3])

    def test_a_closed_tracking_issue_is_reopened_when_there_is_something_to_say(self):
        prior = make_state(latest_stable_kgp="2.4.0")
        comments = [{"body": wtb.render_state_block(prior)}]
        with patch.object(wtb, "collect_state", return_value=make_state()):
            with patch.object(
                wtb, "find_tracking_issue", return_value=IssueLookup(True, 9, "closed")
            ):
                with patch.object(wtb, "fetch_issue_comments", return_value=comments):
                    with patch.object(wtb, "add_issue_comment"):
                        with patch.object(wtb, "reopen_issue") as mock_reopen:
                            self.assertEqual(wtb.main([]), 0)
        mock_reopen.assert_called_once_with("tok", "owner/repo", 9)

    def test_a_closed_tracking_issue_stays_closed_when_nothing_moved(self):
        state = make_state()
        comments = [{"body": wtb.render_state_block(state)}]
        with patch.object(wtb, "collect_state", return_value=state):
            with patch.object(
                wtb, "find_tracking_issue", return_value=IssueLookup(True, 9, "closed")
            ):
                with patch.object(wtb, "fetch_issue_comments", return_value=comments):
                    with patch.object(wtb, "reopen_issue") as mock_reopen:
                        self.assertEqual(wtb.main([]), 0)
        mock_reopen.assert_not_called()

    def test_unreadable_prior_state_posts_nothing_rather_than_duplicating(self):
        with patch.object(wtb, "collect_state", return_value=make_state()):
            with patch.object(
                wtb, "find_tracking_issue", return_value=IssueLookup(True, 9, "open")
            ):
                with patch.object(
                    wtb, "fetch_issue_comments", side_effect=Exception("rate limited")
                ):
                    with patch.object(wtb, "add_issue_comment") as mock_comment:
                        self.assertEqual(wtb.main([]), 0)
        mock_comment.assert_not_called()

    def test_a_failed_lookup_never_creates_a_second_tracking_issue(self):
        """The singleton the whole design rests on must not be duplicated by an API blip."""
        with patch.object(wtb, "collect_state", return_value=make_state()):
            with patch.object(wtb, "find_tracking_issue", return_value=IssueLookup(False)):
                with patch.object(wtb, "create_issue") as mock_create:
                    with patch.object(wtb, "add_issue_comment") as mock_comment:
                        self.assertEqual(wtb.main([]), 0)
        mock_create.assert_not_called()
        mock_comment.assert_not_called()

    def test_a_failed_comment_post_is_logged_as_a_failure(self):
        with patch.object(wtb, "collect_state", return_value=make_state()):
            with patch.object(
                wtb, "find_tracking_issue", return_value=IssueLookup(True, 9, "open")
            ):
                with patch.object(wtb, "fetch_issue_comments", return_value=[]):
                    with patch.object(wtb, "add_issue_comment", return_value=False):
                        with patch("sys.stderr", new_callable=io.StringIO) as err:
                            self.assertEqual(wtb.main([]), 0)
        self.assertIn("could not post the observation", err.getvalue())

    def test_dry_run_touches_no_github_state_and_needs_no_token(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "", "GITHUB_REPOSITORY": ""}):
            with patch.object(wtb, "collect_state", return_value=make_state()):
                with patch.object(wtb, "find_tracking_issue") as mock_find:
                    with patch.object(wtb, "add_issue_comment") as mock_comment:
                        with patch("sys.stdout", new_callable=io.StringIO) as out:
                            self.assertEqual(wtb.main(["--dry-run"]), 0)
        mock_find.assert_not_called()
        mock_comment.assert_not_called()
        self.assertIn("First observation recorded", out.getvalue())


# ---------------------------------------------------------------------------
# Locating the tracking issue without ever duplicating it
# ---------------------------------------------------------------------------


class TestFindTrackingIssue(unittest.TestCase):
    def test_a_search_hit_is_used_directly(self):
        hit = IssueLookup(True, 42, "open")
        with patch.object(wtb, "lookup_issue_by_title", return_value=hit):
            with patch.object(wtb, "confirm_tracking_issue_absent") as mock_confirm:
                self.assertEqual(wtb.find_tracking_issue("tok", "owner/repo"), hit)
        mock_confirm.assert_not_called()

    def test_a_failed_search_is_not_confirmed_away(self):
        with patch.object(wtb, "lookup_issue_by_title", return_value=IssueLookup(False)):
            with patch.object(wtb, "confirm_tracking_issue_absent") as mock_confirm:
                lookup = wtb.find_tracking_issue("tok", "owner/repo")
        self.assertFalse(lookup.fetch_ok)
        mock_confirm.assert_not_called()

    def test_no_search_hit_is_confirmed_against_the_issue_list(self):
        """The search index lags, so "nothing matched" alone must not authorize a create."""
        with patch.object(wtb, "lookup_issue_by_title", return_value=IssueLookup(True)):
            with patch.object(
                wtb, "confirm_tracking_issue_absent", return_value=IssueLookup(True, 7, "open")
            ) as mock_confirm:
                lookup = wtb.find_tracking_issue("tok", "owner/repo")
        mock_confirm.assert_called_once_with("tok", "owner/repo")
        self.assertEqual(lookup.number, 7)


class TestConfirmTrackingIssueAbsent(unittest.TestCase):
    def _issue(self, number: int, title: str, state: str = "open") -> dict:
        return {"number": number, "title": title, "state": state}

    @patch("watch_toolchain_bump.requests")
    def test_finds_the_issue_the_search_index_missed(self, mock_requests):
        mock_requests.get.return_value = _response(
            payload=[self._issue(7, wtb.TRACKING_ISSUE_TITLE, "closed")]
        )
        self.assertEqual(
            wtb.confirm_tracking_issue_absent("tok", "owner/repo"),
            IssueLookup(True, 7, "closed"),
        )

    @patch("watch_toolchain_bump.requests")
    def test_confirms_absence_when_no_title_matches(self, mock_requests):
        mock_requests.get.return_value = _response(payload=[self._issue(1, "Something else")])
        lookup = wtb.confirm_tracking_issue_absent("tok", "owner/repo")
        self.assertTrue(lookup.fetch_ok)
        self.assertIsNone(lookup.number)

    @patch("watch_toolchain_bump.requests")
    def test_queries_all_states_with_the_tracking_label(self, mock_requests):
        mock_requests.get.return_value = _response(payload=[])
        wtb.confirm_tracking_issue_absent("tok", "owner/repo")
        params = mock_requests.get.call_args[1]["params"]
        self.assertEqual(params["labels"], wtb.TRACKING_ISSUE_LABEL)
        self.assertEqual(params["state"], "all")

    @patch("watch_toolchain_bump.requests")
    def test_a_pull_request_with_the_same_title_is_skipped(self, mock_requests):
        pr = self._issue(3, wtb.TRACKING_ISSUE_TITLE)
        pr["pull_request"] = {"url": "https://example.test/pr/3"}
        mock_requests.get.return_value = _response(payload=[pr])
        self.assertIsNone(wtb.confirm_tracking_issue_absent("tok", "owner/repo").number)

    @patch("watch_toolchain_bump.requests")
    def test_paginates_past_a_full_page(self, mock_requests):
        first = _response(payload=[self._issue(i, f"Issue {i}") for i in range(100)])
        second = _response(payload=[self._issue(999, wtb.TRACKING_ISSUE_TITLE)])
        mock_requests.get.side_effect = [first, second]
        self.assertEqual(wtb.confirm_tracking_issue_absent("tok", "owner/repo").number, 999)

    @patch("watch_toolchain_bump.requests")
    def test_an_api_error_is_not_reported_as_absence(self, mock_requests):
        mock_requests.get.side_effect = Exception("rate limited")
        self.assertEqual(wtb.confirm_tracking_issue_absent("tok", "owner/repo"), IssueLookup(False))

    @patch("watch_toolchain_bump.requests")
    def test_exhausting_the_page_cap_is_not_reported_as_absence(self, mock_requests):
        mock_requests.get.return_value = _response(
            payload=[self._issue(i, f"Issue {i}") for i in range(100)]
        )
        lookup = wtb.confirm_tracking_issue_absent("tok", "owner/repo")
        self.assertFalse(lookup.fetch_ok)
        self.assertEqual(mock_requests.get.call_count, wtb._MAX_ISSUE_LIST_PAGES)


if __name__ == "__main__":
    unittest.main()
