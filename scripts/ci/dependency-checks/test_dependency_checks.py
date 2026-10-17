#!/usr/bin/env python3
"""Unit tests for dependency_checks.py.

The report fixtures copy the shapes Gradle 9.5.1's `dependencies` task printed
for this repository's own graph: a BOM node listing its members as "(c)"
constraints with "requested -> selected" where they differ, versionless
BOM-managed declarations at the top level ("ui -> 1.12.1"), and "(*)" on every
occurrence after the first.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import itertools

import dependency_checks as dc
from workflow_files import REPO_ROOT, load_workflow

SHA_A = "a" * 40
SHA_B = "b" * 40


def report(configuration: str, tree: str) -> str:
    """Return a report as Gradle prints it, with *tree* as *configuration*'s section."""
    return (
        "\n------------------------------------------------------------\n"
        "Project ':app'\n"
        "------------------------------------------------------------\n\n"
        f"{configuration} - Runtime classpath of '/release'.\n"
        f"{tree.strip(chr(10))}\n\n"
        "(c) - A dependency constraint, not a dependency.\n"
    )


def parse(tree: str) -> list[dc.Node]:
    return dc.parse_report(report("releaseRuntimeClasspath", tree), "releaseRuntimeClasspath")


def graph(tree: str) -> dict[str, list[dc.Node]]:
    """Return every configuration in CONFIGURATIONS resolving to the same *tree*."""
    return {
        dc.configuration_id(project, configuration): parse(tree)
        for project, configuration in dc.CONFIGURATIONS
    }


BASE_TREE = r"""
+--- androidx.core:core-ktx:1.15.0 -> 1.16.0
|    +--- androidx.core:core:1.16.0
|    |    \--- androidx.lifecycle:lifecycle-runtime:2.6.1 -> 2.8.7
|    \--- org.jetbrains.kotlin:kotlin-stdlib:2.4.20
+--- androidx.lifecycle:lifecycle-runtime-ktx:2.8.7
|    \--- androidx.lifecycle:lifecycle-runtime:2.8.7 (*)
+--- androidx.compose:compose-bom:2024.12.01
|    +--- androidx.compose.ui:ui:1.7.6 (c)
|    \--- androidx.compose.runtime:runtime:1.7.6 (c)
+--- androidx.compose.ui:ui -> 1.7.6
|    +--- androidx.compose.runtime:runtime:1.7.6
|    \--- androidx.compose.ui:ui:1.7.6 (c)
\--- project :e2e-mock-camera
"""

HEAD_TREE = r"""
+--- androidx.core:core-ktx:1.19.1
|    +--- androidx.core:core:1.19.1
|    |    +--- androidx.lifecycle:lifecycle-runtime:2.10.0
|    |    \--- org.jetbrains.kotlinx:kotlinx-serialization-core:1.7.3
|    \--- org.jetbrains.kotlin:kotlin-stdlib:2.4.20
+--- androidx.lifecycle:lifecycle-runtime-ktx:2.8.7 -> 2.10.0
|    \--- androidx.lifecycle:lifecycle-runtime:2.10.0 (*)
+--- androidx.compose:compose-bom:2024.12.01
|    +--- androidx.compose.ui:ui:1.7.6 (c)
|    \--- androidx.compose.runtime:runtime:1.7.6 -> 1.9.0 (c)
+--- androidx.compose.ui:ui -> 1.7.6
|    +--- androidx.compose.runtime:runtime:1.7.6 -> 1.9.0
|    \--- androidx.compose.ui:ui:1.7.6 (c)
\--- project :e2e-mock-camera
"""

CORE_KTX_DIFF = """\
diff --git a/app/build.gradle.kts b/app/build.gradle.kts
--- a/app/build.gradle.kts
+++ b/app/build.gradle.kts
@@ -1 +1 @@
-    implementation("androidx.core:core-ktx:1.15.0")
+    implementation("androidx.core:core-ktx:1.19.1")
"""


class CompareVersionsTest(unittest.TestCase):
    def test_orders(self):
        ascending = ["1.0.0-alpha01", "1.0.0-alpha02", "1.0.0-beta01", "1.0.0-rc01", "1.0.0"]
        ascending += ["1.0.0.1", "1.0.1", "1.2", "1.10", "2.0.0"]
        for lower, higher in itertools.pairwise(ascending):
            with self.subTest(lower=lower, higher=higher):
                self.assertEqual(dc.compare_versions(lower, higher), -1)
                self.assertEqual(dc.compare_versions(higher, lower), 1)

    def test_equal(self):
        self.assertEqual(dc.compare_versions("2.8.7", "2.8.7"), 0)


class ParseReportTest(unittest.TestCase):
    def test_tree_shape(self):
        roots = parse(BASE_TREE)
        self.assertEqual(
            [node.library for node in roots],
            [
                "androidx.core:core-ktx",
                "androidx.lifecycle:lifecycle-runtime-ktx",
                "androidx.compose:compose-bom",
                "androidx.compose.ui:ui",
                "project :e2e-mock-camera",
            ],
        )
        core_ktx = roots[0]
        self.assertEqual((core_ktx.requested, core_ktx.selected), ("1.15.0", "1.16.0"))
        self.assertEqual(
            core_ktx.children[0].children[0].library, "androidx.lifecycle:lifecycle-runtime"
        )
        ui = roots[3]
        self.assertEqual((ui.requested, ui.selected, ui.version), (None, "1.7.6", "1.7.6"))
        self.assertTrue(ui.children[1].constraint)

    def test_resolved_versions_skip_constraints_and_projects(self):
        versions = dc.resolved_versions(parse(HEAD_TREE))
        self.assertEqual(versions["androidx.lifecycle:lifecycle-runtime"], "2.10.0")
        self.assertEqual(versions["androidx.compose.runtime:runtime"], "1.9.0")
        self.assertEqual(versions["androidx.compose:compose-bom"], "2024.12.01")
        self.assertNotIn("project :e2e-mock-camera", versions)

    def test_constraint_members_reads_the_first_expanded_occurrence(self):
        self.assertEqual(
            dc.constraint_members(parse(HEAD_TREE), dc.COMPOSE_BOM),
            {"androidx.compose.ui:ui": "1.7.6", "androidx.compose.runtime:runtime": "1.7.6"},
        )
        self.assertEqual(dc.constraint_members(parse(HEAD_TREE), "absent:absent"), {})

    def test_not_resolved_nodes_are_dropped(self):
        roots = parse("+--- a:b:1.0 (n)\n\\--- c:d:2.0")
        self.assertEqual([node.library for node in roots], ["c:d"])

    def test_rich_version(self):
        (node,) = parse("\\--- a:b:{strictly 1.0} -> 1.0")
        self.assertEqual((node.requested, node.selected), ("{strictly 1.0}", "1.0"))

    def test_no_dependencies(self):
        text = report("releaseRuntimeClasspath", "No dependencies")
        self.assertEqual(dc.parse_report(text, "releaseRuntimeClasspath"), [])

    def test_failed_node_is_an_error(self):
        with self.assertRaisesRegex(dc.ReportError, "failed to resolve a:b:1.0"):
            parse("+--- c:d:2.0\n|    \\--- a:b:1.0 FAILED")

    def test_unrecognized_line_is_an_error(self):
        with self.assertRaises(dc.ReportError):
            parse("+--- c:d:2.0\nsomething else")
        with self.assertRaises(dc.ReportError):
            parse("+--- not a coordinate")

    def test_skipped_level_is_an_error(self):
        with self.assertRaises(dc.ReportError):
            parse("+--- c:d:2.0\n|    |    \\--- a:b:1.0")

    def test_missing_section_is_an_error(self):
        with self.assertRaisesRegex(dc.ReportError, "no section"):
            dc.parse_report(
                report("debugCompileClasspath", "+--- a:b:1.0"), "releaseRuntimeClasspath"
            )

    def test_section_ends_at_blank_line(self):
        text = report("releaseRuntimeClasspath", "\\--- a:b:1.0") + "\nother\n+--- c:d:1.0\n"
        self.assertEqual(len(dc.parse_report(text, "releaseRuntimeClasspath")), 1)


class ChangedLinesTest(unittest.TestCase):
    def test_coordinates_and_platforms(self):
        diff = CORE_KTX_DIFF + (
            '+    implementation(platform("androidx.compose:compose-bom:2026.09.00"))\n'
            '-    androidTestImplementation(enforcedPlatform("g.h:bom:1"))\n'
            '+    implementation("androidx.compose.ui:ui")\n'
            ' implementation("unchanged:context:1.0")\n'
        )
        lines = dc.changed_lines(diff)
        self.assertEqual(
            dc.coordinates_on(lines),
            {
                "androidx.core:core-ktx",
                "androidx.compose:compose-bom",
                "g.h:bom",
                "androidx.compose.ui:ui",
            },
        )
        self.assertEqual(dc.platforms_on(lines), {"androidx.compose:compose-bom", "g.h:bom"})


class FindMovesTest(unittest.TestCase):
    def moves(self, base_tree, head_tree, diff):
        rows = dc.find_moves(graph(base_tree), graph(head_tree), diff)
        return {row["library"]: row for row in rows}

    def test_core_ktx_bump_hides_lifecycle_and_the_new_library(self):
        rows = self.moves(BASE_TREE, HEAD_TREE, CORE_KTX_DIFF)
        self.assertFalse(rows["androidx.core:core-ktx"]["hidden"])
        self.assertEqual(rows["androidx.core:core-ktx"]["base"], ["1.16.0"])
        self.assertEqual(rows["androidx.core:core-ktx"]["head"], ["1.19.1"])
        for library in (
            "androidx.core:core",
            "androidx.lifecycle:lifecycle-runtime",
            "androidx.lifecycle:lifecycle-runtime-ktx",
            "androidx.compose.runtime:runtime",
        ):
            self.assertTrue(rows[library]["hidden"], library)
        added = rows["org.jetbrains.kotlinx:kotlinx-serialization-core"]
        self.assertEqual((added["base"], added["head"], added["hidden"]), ([], ["1.7.3"], True))
        self.assertNotIn("org.jetbrains.kotlin:kotlin-stdlib", rows)
        self.assertEqual(
            rows["androidx.core:core"]["configurations"],
            [dc.configuration_id(p, c) for p, c in dc.CONFIGURATIONS],
        )

    def test_removed_library(self):
        rows = self.moves("+--- a:b:1.0\n\\--- c:d:1.0", "\\--- a:b:1.0", "")
        self.assertEqual((rows["c:d"]["base"], rows["c:d"]["head"]), (["1.0"], []))

    def test_bom_members_are_explained_when_the_bom_line_changes(self):
        base = "+--- g:bom:1\n|    \\--- g:member:1.0 (c)\n\\--- g:member -> 1.0"
        head = "+--- g:bom:2\n|    \\--- g:member:2.0 (c)\n\\--- g:member -> 2.0\n"
        diff = '-  implementation(platform("g:bom:1"))\n+  implementation(platform("g:bom:2"))\n'
        rows = self.moves(base, head, diff)
        self.assertFalse(rows["g:member"]["hidden"])
        self.assertFalse(rows["g:bom"]["hidden"])

    def test_constraints_of_a_non_platform_line_do_not_explain(self):
        base = "+--- g:lib:1\n|    \\--- g:sibling:1.0 (c)\n\\--- g:sibling:1.0"
        head = "+--- g:lib:2\n|    \\--- g:sibling:2.0 (c)\n\\--- g:sibling:2.0"
        diff = '-  implementation("g:lib:1")\n+  implementation("g:lib:2")\n'
        self.assertTrue(self.moves(base, head, diff)["g:sibling"]["hidden"])

    def test_one_configuration_only(self):
        base, head = graph("\\--- a:b:1.0"), graph("\\--- a:b:1.0")
        only = dc.configuration_id(":app", "debugAndroidTestRuntimeClasspath")
        head[only] = parse("\\--- a:b:2.0")
        (row,) = dc.find_moves(base, head, "")
        self.assertEqual(row["configurations"], [only])


class OtherChecksTest(unittest.TestCase):
    def test_pin_drift(self):
        tree = r"""
+--- up:declared:1.0 -> 2.0
+--- down:declared:2.0 -> 1.0
+--- same:declared:1.0
+--- bom:managed -> 3.0
+--- rich:declared:{strictly 1.0} -> 1.0
+--- g:bom:1
|    \--- g:member:1.0 -> 2.0 (c)
\--- transitive:parent:1.0
     \--- moved:child:1.0 -> 2.0
"""
        rows = dc.find_pin_drift(graph(tree))
        self.assertEqual(
            [(r["library"], r["declared"], r["resolved"]) for r in rows],
            [("up:declared", "1.0", "2.0")],
        )
        self.assertEqual(len(rows[0]["configurations"]), len(dc.CONFIGURATIONS))

    def test_classpath_mismatch(self):
        head = graph("+--- a:b:1.0\n\\--- c:d:1.0")
        head[":app:releaseRuntimeClasspath"] = parse("+--- a:b:1.1\n+--- c:d:1.0\n\\--- e:f:1.0")
        self.assertEqual(
            dc.find_classpath_mismatches(head),
            [{"variant": ":app:release", "library": "a:b", "compile": "1.0", "runtime": "1.1"}],
        )

    def test_compose_alignment(self):
        rows = dc.find_compose_misalignment(graph(HEAD_TREE))
        self.assertEqual(
            [(r["library"], r["bom"], r["resolved"]) for r in rows],
            [("androidx.compose.runtime:runtime", "1.7.6", "1.9.0")],
        )
        self.assertEqual(dc.find_compose_misalignment(graph(BASE_TREE)), [])

    def test_classpath_variants_are_read(self):
        ids = {dc.configuration_id(p, c) for p, c in dc.CONFIGURATIONS}
        for project, variant in dc.CLASSPATH_VARIANTS:
            for kind in ("CompileClasspath", "RuntimeClasspath"):
                self.assertIn(dc.configuration_id(project, f"{variant}{kind}"), ids)


def findings_1145() -> dict:
    return dc.analyze(graph(BASE_TREE), graph(HEAD_TREE), CORE_KTX_DIFF, SHA_A, SHA_B)


class FindingsTest(unittest.TestCase):
    def test_analyze_output_validates(self):
        findings = findings_1145()
        self.assertEqual(dc.validate_findings(json.loads(json.dumps(findings))), findings)
        self.assertEqual(dc.validate_findings(dc.skipped_findings()), dc.skipped_findings())

    def test_rejects_values_outside_the_patterns(self):
        for path, value in (
            (("dep-change", "moves", 0, "library"), "a:b|<img src=x>"),
            (("dep-pin-drift", "drifts", 0, "declared"), "1.0`"),
            (("dep-change", "moves", 0, "hidden"), "yes"),
            (("dep-change", "moves", 0, "configurations"), [":app:x\n"]),
        ):
            findings = json.loads(json.dumps(findings_1145()))
            target = findings["checks"]
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(dc.FindingsError):
                dc.validate_findings(findings)

    def test_rejects_wrong_shapes(self):
        good = findings_1145()
        for bad in (
            None,
            {**good, "schema": 2},
            {**good, "status": "other"},
            {**good, "extra": 1},
            {**good, "head_sha": "HEAD"},
            {**good, "checks": {k: v for k, v in good["checks"].items() if k != "dep-change"}},
            {**good, "checks": {**good["checks"], "dep-change": {"moves": [{"library": "a:b"}]}}},
            {"schema": 1, "status": "skipped", "checks": {}},
        ):
            with self.subTest(bad=str(bad)[:60]), self.assertRaises(dc.FindingsError):
                dc.validate_findings(bad)


class LabelsAndCommentsTest(unittest.TestCase):
    def test_labels(self):
        findings = findings_1145()
        self.assertEqual(
            dc.labels_for("dep-change", findings), ["dep: change", "dep: hidden change"]
        )
        self.assertEqual(dc.labels_for("dep-pin-drift", findings), ["dep: pin drift"])
        self.assertEqual(dc.labels_for("dep-classpath-mismatch", findings), [])
        self.assertEqual(
            dc.labels_for("dep-compose-alignment", findings), ["dep: compose misaligned"]
        )
        self.assertEqual(dc.labels_for("dep-change", dc.skipped_findings()), [])

    def test_explained_moves_label_dep_change_only(self):
        findings = dc.analyze(graph(BASE_TREE), graph(BASE_TREE), "", SHA_A, SHA_B)
        findings["checks"]["dep-change"]["moves"] = [
            {
                "library": "a:b",
                "base": ["1"],
                "head": ["2"],
                "configurations": [":app:x"],
                "hidden": False,
            }
        ]
        self.assertEqual(dc.labels_for("dep-change", findings), ["dep: change"])

    def test_all_labels(self):
        self.assertEqual(
            dc.ALL_LABELS,
            {
                "dep: change",
                "dep: hidden change",
                "dep: pin drift",
                "dep: classpath mismatch",
                "dep: compose misaligned",
            },
        )

    def test_comment_starts_with_marker_and_records_the_owned_labels(self):
        findings = findings_1145()
        for check in dc.CHECKS:
            body = dc.render_comment(check, findings, ["dep: pin drift"])
            self.assertEqual(body.split("\n", 1)[0], dc.comment_marker(check))
            self.assertEqual(dc.recorded_labels(body), ["dep: pin drift"])
        # The record is what the caller says the check owns, not what the
        # findings call for: a label someone else applied is never recorded.
        self.assertEqual(dc.recorded_labels(dc.render_comment("dep-change", findings, [])), [])
        moves = dc.render_comment("dep-change", findings)
        self.assertIn(
            "| `org.jetbrains.kotlinx:kotlinx-serialization-core` | absent | `1.7.3` | **yes** |",
            moves,
        )
        self.assertIn(
            "Labels these findings call for: `dep: change`, `dep: hidden change`.\n\n| Library",
            moves,
        )
        self.assertNotIn("labels-added-by-this-check", moves)
        self.assertIn(
            dc._NONE_FOUND["dep-classpath-mismatch"],
            dc.render_comment("dep-classpath-mismatch", findings),
        )

    def test_skipped_comment(self):
        body = dc.render_comment("dep-pin-drift", dc.skipped_findings(), [])
        self.assertIn("no longer changes a build file", body)
        self.assertEqual(dc.recorded_labels(body), [])

    def test_recorded_labels_tolerates_garbage(self):
        self.assertEqual(dc.recorded_labels("no record"), [])
        self.assertEqual(dc.recorded_labels("<!-- labels-added-by-this-check: [oops -->"), [])
        self.assertEqual(dc.recorded_labels('<!-- labels-added-by-this-check: ["a", 1] -->'), ["a"])

    def test_long_comment_is_cut_and_summary_is_not(self):
        findings = findings_1145()
        findings["checks"]["dep-pin-drift"]["drifts"] = [
            {
                "library": f"g:a{i}",
                "declared": "1.0",
                "resolved": "2.0",
                "configurations": [":app:" + "x" * 150],
            }
            for i in range(1000)
        ]
        comment = dc.render_comment("dep-pin-drift", findings)
        self.assertLessEqual(len(comment), dc.MAX_COMMENT_LENGTH)
        self.assertRegex(comment, r"\n\d+ more rows do not fit in a comment")
        summary = dc.render_comment("dep-pin-drift", findings, max_length=None)
        self.assertIn("`g:a999`", summary)
        self.assertNotIn("more rows", summary)


class CommandLineTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dc.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def write_reports(self, side, tree):
        os.makedirs(os.path.join(self.dir, side))
        for project, configuration in dc.CONFIGURATIONS:
            path = os.path.join(self.dir, side, dc.report_file_name(project, configuration))
            with open(path, "w", encoding="utf-8") as f:
                f.write(report(configuration, tree))

    def test_configurations(self):
        code, out, _ = self.run_main("configurations")
        self.assertEqual(code, 0)
        self.assertIn(
            ":app releaseRuntimeClasspath app-releaseRuntimeClasspath.txt", out.splitlines()
        )
        self.assertIn(
            ":e2e-mock-gallery debugUnitTestRuntimeClasspath e2e-mock-gallery-debugUnitTestRuntimeClasspath.txt",
            out.splitlines(),
        )

    def test_analyze_then_summaries(self):
        self.write_reports("base", BASE_TREE)
        self.write_reports("head", HEAD_TREE)
        diff = os.path.join(self.dir, "build-files.diff")
        with open(diff, "w", encoding="utf-8") as f:
            f.write(CORE_KTX_DIFF)
        out = os.path.join(self.dir, "findings.json")
        base, head = os.path.join(self.dir, "base"), os.path.join(self.dir, "head")
        self.assertEqual(self.run_main("analyze", base, head, diff, SHA_A, SHA_B, out)[0], 0)
        self.assertEqual(dc.load_findings(out), json.loads(json.dumps(findings_1145())))
        self.assertEqual(self.run_main("summaries", out, self.dir)[0], 0)
        for check in dc.CHECKS:
            with open(os.path.join(self.dir, f"{check}.md"), encoding="utf-8") as f:
                self.assertTrue(f.read().startswith(dc.comment_marker(check)))

    def test_analyze_fails_on_an_unresolved_report(self):
        self.write_reports("base", BASE_TREE)
        self.write_reports("head", "\\--- a:b:1.0 FAILED")
        out = os.path.join(self.dir, "findings.json")
        code, _, err = self.run_main(
            "analyze",
            os.path.join(self.dir, "base"),
            os.path.join(self.dir, "head"),
            os.devnull,
            SHA_A,
            SHA_B,
            out,
        )
        self.assertEqual(code, 1)
        self.assertIn("::error::Gradle failed to resolve a:b:1.0", err)
        self.assertFalse(os.path.exists(out))

    def test_analyze_fails_on_a_missing_report(self):
        self.write_reports("base", BASE_TREE)
        code, _, err = self.run_main(
            "analyze",
            os.path.join(self.dir, "base"),
            os.path.join(self.dir, "nope"),
            os.devnull,
            SHA_A,
            SHA_B,
            os.path.join(self.dir, "findings.json"),
        )
        self.assertEqual(code, 1)
        self.assertIn("cannot read the report", err)

    def test_skipped(self):
        out = os.path.join(self.dir, "findings.json")
        self.assertEqual(self.run_main("skipped", out)[0], 0)
        self.assertEqual(dc.load_findings(out), dc.skipped_findings())

    def test_summaries_rejects_bad_findings(self):
        path = os.path.join(self.dir, "findings.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{}")
        self.assertEqual(self.run_main("summaries", path, self.dir)[0], 1)

    def test_usage(self):
        self.assertEqual(self.run_main()[0], 2)
        self.assertEqual(self.run_main("analyze", "too", "few")[0], 2)


class WorkflowTest(unittest.TestCase):
    def test_each_check_is_a_job_that_prints_its_own_report(self):
        # A check's job id is its name in the ruleset, while its comment's
        # marker and heading and its report file come from CHECKS, so the two
        # have to name each check alike.
        workflow = load_workflow(
            os.path.join(REPO_ROOT, ".github", "workflows", "dependency-version-checks.yml")
        )
        jobs = workflow["jobs"]
        self.assertEqual([job for job in jobs if job != "resolve"], list(dc.CHECKS))
        for check in dc.CHECKS:
            reports = [step.get("env", {}).get("REPORT", "") for step in jobs[check]["steps"]]
            with self.subTest(check=check):
                self.assertTrue(
                    any(r.endswith(f"/dependency-version-checks/{check}.md") for r in reports),
                    reports,
                )


if __name__ == "__main__":
    unittest.main()
