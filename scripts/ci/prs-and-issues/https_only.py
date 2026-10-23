#!/usr/bin/env python3
"""https_only.py: Build and open urllib requests for https URLs only.

`urllib.request` opens `file:` and `ftp:` URLs as readily as `https:` ones, so
a URL that reaches it from a variable (an API response, a caller's argument)
could read a local file. ruff's S310 audits for this (issue #1341), and these
two functions are how the scripts that call the GitHub REST API or Maven
repositories satisfy it: each refuses a URL whose scheme is not `https` with a
ValueError, before anything is opened.

Each builds its `Request` from a literal `"https://"` followed by the rest of
the URL, which is the URL it was given with its scheme in lower case. The
literal is what S310 recognizes as safe, so the rule passes here without a
suppression and still fires on any other `urlopen` or `Request` whose URL is a
variable.

`https_request` is for a caller that keeps its own opener (to refuse or
rewrite redirects); `urlopen_https` is for one that uses `urlopen` itself.

`scripts/ci_monitor/ci_monitor.py` carries its own copy of the check, so that it
stays a single file that can be copied into another repository.

This module has no `main`; it is imported, not run directly.
"""

import socket
import urllib.request


def _after_https_scheme(url: str) -> str:
    """Return *url* without its `https://` prefix, refusing any other scheme."""
    scheme, separator, rest = url.partition("://")
    if not separator or scheme.lower() != "https" or not rest:
        raise ValueError(f"refusing to open {url!r}: only https URLs are allowed")
    return rest


def https_request(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    method: str | None = None,
) -> urllib.request.Request:
    """Return a `Request` for *url*, or raise ValueError if it is not https."""
    return urllib.request.Request(
        f"https://{_after_https_scheme(url)}", data=data, headers=headers or {}, method=method
    )


def urlopen_https(
    url: str,
    data: bytes | None = None,
    *,
    headers: dict[str, str] | None = None,
    method: str | None = None,
    timeout: float | None = None,
):
    """Open *url* with `urllib.request.urlopen`, or raise ValueError if it is not https.

    *data* is passed to `urlopen` as its second argument, as a direct call
    would pass it. A *timeout* of None stands for socket's default timeout,
    which is what `urlopen` applies when it is given none. It is a named
    parameter rather than `**kwargs` because S310 flags any call that passes
    `**kwargs` on to `urlopen`.
    """
    if timeout is None:
        timeout = socket.getdefaulttimeout()
    return urllib.request.urlopen(
        urllib.request.Request(
            f"https://{_after_https_scheme(url)}", headers=headers or {}, method=method
        ),
        data,
        timeout=timeout,
    )
