#!/usr/bin/env python3
"""Unit tests for https_only.py."""

import os
import socket
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import https_only

# Every URL here must be refused before anything is opened.
REFUSED_URLS = [
    "file:///etc/passwd",
    "FILE:///etc/passwd",
    "http://api.github.com/repos/o/r",
    "ftp://example.com/x",
    "data:text/plain,hello",
    "custom://api.github.com/x",
    "api.github.com/repos/o/r",
    "https:api.github.com/repos/o/r",
    "https://",
    " https://api.github.com/repos/o/r",
    "",
]


class TestHttpsRequest(unittest.TestCase):
    def test_keeps_an_https_url_unchanged(self):
        url = "https://api.github.com/repos/o/r/issues?per_page=100&page=2"
        self.assertEqual(https_only.https_request(url).full_url, url)

    def test_lowercases_an_upper_case_https_scheme(self):
        request = https_only.https_request("HTTPS://api.github.com/x")
        self.assertEqual(request.full_url, "https://api.github.com/x")

    def test_passes_data_headers_and_method_to_the_request(self):
        request = https_only.https_request(
            "https://api.github.com/x",
            data=b"{}",
            headers={"Authorization": "Bearer t"},
            method="PATCH",
        )
        self.assertEqual(request.data, b"{}")
        self.assertEqual(request.get_header("Authorization"), "Bearer t")
        self.assertEqual(request.get_method(), "PATCH")

    def test_refuses_every_scheme_but_https(self):
        for url in REFUSED_URLS:
            with self.subTest(url=url), self.assertRaises(ValueError):
                https_only.https_request(url)


class TestUrlopenHttps(unittest.TestCase):
    def test_opens_the_url_with_its_data_and_options(self):
        with patch("urllib.request.urlopen") as urlopen:
            result = https_only.urlopen_https(
                "https://api.github.com/x",
                b"{}",
                headers={"Accept": "application/vnd.github+json"},
                method="POST",
                timeout=7,
            )
        self.assertIs(result, urlopen.return_value)
        (request, data), kwargs = urlopen.call_args
        self.assertEqual(request.full_url, "https://api.github.com/x")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Accept"), "application/vnd.github+json")
        self.assertEqual(data, b"{}")
        self.assertEqual(kwargs, {"timeout": 7})

    def test_applies_the_socket_default_timeout_unless_given_one(self):
        previous = socket.getdefaulttimeout()
        socket.setdefaulttimeout(42)
        try:
            with patch("urllib.request.urlopen") as urlopen:
                https_only.urlopen_https("https://api.github.com/x")
        finally:
            socket.setdefaulttimeout(previous)
        self.assertEqual(urlopen.call_args.kwargs, {"timeout": 42})

    def test_refuses_every_scheme_but_https_without_opening_anything(self):
        for url in REFUSED_URLS:
            with self.subTest(url=url), patch("urllib.request.urlopen") as urlopen:
                with self.assertRaises(ValueError):
                    https_only.urlopen_https(url)
                urlopen.assert_not_called()

    def test_refuses_a_file_url_against_the_real_urlopen(self):
        """Without the check, urllib would read this file and return its contents."""
        with self.assertRaises(ValueError):
            https_only.urlopen_https(f"file://{os.path.abspath(__file__)}")


if __name__ == "__main__":
    unittest.main()
