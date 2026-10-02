#!/usr/bin/env python3
"""Tests for gradle_coordinates, the Gradle reader behind scripts/test_dependabot_config.sh.

The live test at the foot of this file is what holds GOOGLE_MAVEN_COORDINATE_PREFIXES
to the repository it models (issue #914). The Dependabot config guard classes
each declared coordinate as Google- or Central-hosted by prefix and requires
cooldown to exempt the first kind and hold the second. A coordinate the prefixes
class wrongly turns that requirement around: a Google-hosted one classed
Central-hosted is demanded the cooldown that hides its bumps (issue #905). So
this asks https://maven.google.com about every coordinate the gradle entries in
.github/dependabot.yml declare, and fails on any the prefixes disagree with.

It needs the network. Outside GitHub Actions an unreachable repository skips it;
under GitHub Actions it fails, since the build there resolves from the same
repository and a skip would let the check lapse unnoticed.
"""

import os
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor

import yaml

from gradle_coordinates import (
    GOOGLE_MAVEN_COORDINATE_PREFIXES,
    covered_directories,
    entry_declarations,
    google_maven_artifacts,
    group_index_url,
    is_google_hosted,
    parse_group_index,
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CONFIG_PATH = os.path.join(_REPO_ROOT, ".github", "dependabot.yml")


def _gradle_entry_coordinates():
    """Every versioned coordinate the gradle entries' manifests declare.

    Read through covered_directories and entry_declarations, the calls
    scripts/test_dependabot_config.sh makes, so this checks the coordinates that
    script classifies. Reporting a malformed entry is that script's job.
    """
    with open(_CONFIG_PATH) as handle:
        doc = yaml.safe_load(handle)
    coordinates = set()
    for entry in doc.get("updates") or []:
        if entry.get("package-ecosystem") != "gradle":
            continue
        declared, _manifests = entry_declarations(_REPO_ROOT, covered_directories(entry) or [])
        coordinates.update(declared)
    return coordinates


class TestIsGoogleHosted(unittest.TestCase):
    def test_androidx_and_material_are_google_hosted(self):
        for coordinate in (
            "androidx.core:core-ktx",
            "androidx.test.espresso:espresso-core",
            "com.google.android.material:material",
        ):
            with self.subTest(coordinate=coordinate):
                self.assertTrue(is_google_hosted(coordinate))

    def test_an_androidx_artifact_name_does_not_make_a_coordinate_google_hosted(self):
        # The case a loose "*androidx*" pattern gets wrong: Maven Central serves it.
        self.assertFalse(
            is_google_hosted("com.davemorrissey.labs:subsampling-scale-image-view-androidx")
        )

    def test_central_coordinates_are_not_google_hosted(self):
        for coordinate in ("junit:junit", "org.json:json", "org.mockito.kotlin:mockito-kotlin"):
            with self.subTest(coordinate=coordinate):
                self.assertFalse(is_google_hosted(coordinate))


class TestGroupIndex(unittest.TestCase):
    def test_url_turns_the_group_into_a_path(self):
        self.assertEqual(
            group_index_url("com.google.android.material"),
            "https://maven.google.com/com/google/android/material/group-index.xml",
        )

    def test_parse_names_each_artifact_the_index_lists(self):
        document = (
            b"<?xml version='1.0' encoding='UTF-8'?>\n"
            b"<com.google.android.material>\n"
            b'  <compose-theme-adapter versions="1.0.0,1.0.1"/>\n'
            b'  <material versions="1.13.0,1.14.0"/>\n'
            b"</com.google.android.material>\n"
        )
        self.assertEqual(parse_group_index(document), {"compose-theme-adapter", "material"})


class TestDeclaredCoordinatesMatchGoogleMaven(unittest.TestCase):
    """Live: the prefixes class every declared coordinate as maven.google.com does."""

    @classmethod
    def setUpClass(cls):
        cls.coordinates = sorted(_gradle_entry_coordinates())
        groups = sorted({coordinate.split(":", 1)[0] for coordinate in cls.coordinates})
        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                cls.served = dict(zip(groups, pool.map(google_maven_artifacts, groups)))
        except urllib.error.URLError as err:
            if os.environ.get("GITHUB_ACTIONS") == "true":
                raise
            raise unittest.SkipTest("cannot reach https://maven.google.com (%s)" % err)

    def is_served(self, coordinate):
        group, artifact = coordinate.split(":", 1)
        return artifact in self.served[group]

    def test_both_hosts_are_represented(self):
        # Without coordinates of both kinds the test below could pass on an
        # empty walk or a dead lookup, checking nothing.
        served = [c for c in self.coordinates if self.is_served(c)]
        self.assertTrue(served, "no declared coordinate is served by https://maven.google.com")
        self.assertLess(
            len(served), len(self.coordinates), "every declared coordinate is served by Google"
        )

    def test_prefixes_class_each_coordinate_as_maven_google_com_does(self):
        for coordinate in self.coordinates:
            served = self.is_served(coordinate)
            with self.subTest(coordinate=coordinate):
                self.assertEqual(
                    is_google_hosted(coordinate),
                    served,
                    "%s is %s by https://maven.google.com, but GOOGLE_MAVEN_COORDINATE_PREFIXES %r in "
                    "scripts/ci/gradle_coordinates.py classes it %s-hosted, so "
                    "scripts/test_dependabot_config.sh would demand the cooldown setting that "
                    "is wrong for it. %s (issue #914)"
                    % (
                        coordinate,
                        "served" if served else "not served",
                        GOOGLE_MAVEN_COORDINATE_PREFIXES,
                        "Central" if served else "Google",
                        "Add an entry matching it there, and a matching pattern to the cooldown "
                        "exclude list in .github/dependabot.yml."
                        if served
                        else "Narrow the entry that matches it.",
                    ),
                )


if __name__ == "__main__":
    unittest.main()
