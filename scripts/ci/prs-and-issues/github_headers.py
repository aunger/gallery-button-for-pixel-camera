#!/usr/bin/env python3
"""github_headers.py: Shared GitHub REST API request headers.

`post_pr_ci_summary_link.py`, `strip_session_bylines.py`, and
`file_test_failure_issues.py` each authenticate to the GitHub REST API the
same way. This module holds that one shared helper so the three scripts
import it instead of each carrying its own byte-identical copy (issue #822).

This module has no `main`; it is imported, not run directly.
"""


def github_headers(token: str) -> dict[str, str]:
    """Return the standard header set for an authenticated GitHub REST API call."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
