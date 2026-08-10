#!/usr/bin/env python3
"""Unit tests for github_headers.py."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import github_headers as gh  # noqa: E402


class TestGithubHeaders(unittest.TestCase):
    def test_returns_expected_header_set(self):
        headers = gh.github_headers("my-token")
        self.assertEqual(
            headers,
            {
                "Authorization": "Bearer my-token",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def test_interpolates_the_given_token(self):
        headers = gh.github_headers("another-token")
        self.assertEqual(headers["Authorization"], "Bearer another-token")

    def test_empty_token_still_returns_full_header_set(self):
        headers = gh.github_headers("")
        self.assertEqual(headers["Authorization"], "Bearer ")
        self.assertEqual(headers["Accept"], "application/vnd.github+json")
        self.assertEqual(headers["X-GitHub-Api-Version"], "2022-11-28")


if __name__ == "__main__":
    unittest.main()
