#!/usr/bin/env python3
"""check_config.py: Validate a project's orchestrate configuration file.

The orchestrate plugin reads a project's facts from a JSON file, by convention
`.claude/orchestrate.json` in the project; README.md at the plugin root
documents every key. This script says whether such a file holds what the
plugin's Orchestrator, agents, and CI Monitor expect, so a project can check
its configuration in CI rather than discover a typo mid-cycle.

Usage:
    check_config.py PATH

Exit codes:
    0  the file is a usable configuration (any warnings are printed)
    1  the file is missing something the plugin needs; each problem is printed

Standard library only.
"""

import json
import re
import sys

REQUIRED_KEYS = {
    "repository": "the OWNER/REPO the cycle runs in",
    "named_checks": "the CI checks the Orchestrator drives to green",
}
OPTIONAL_KEYS = {
    "verification_plan_marker": "<!-- orchestrate-verification-plan -->",
    "default_model": "opus",
    "max_rounds": 4,
}
MONITOR_REGEX_KEYS = (
    "artifact_name_regex",
    "interesting_step_regex",
    "deferred_verdict_step_regex",
    "test_marker_regex",
    "ignored_check_regex",
)
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+")


def is_one_html_comment(text):
    """Return True when `text` is exactly one non-empty HTML comment."""
    if not isinstance(text, str):
        return False
    text = text.strip()
    if not (text.startswith("<!--") and text.endswith("-->")) or len(text) < 8:
        return False
    inner = text[4:-3]
    return bool(inner.strip()) and "-->" not in inner and "<!--" not in inner


def problems(config):
    """Return (errors, warnings) for a parsed configuration object."""
    errors = []
    warnings = []
    if not isinstance(config, dict):
        return ["the file must hold a JSON object"], warnings
    for key, what in REQUIRED_KEYS.items():
        if key not in config:
            errors.append("missing required key %r (%s)" % (key, what))
    repository = config.get("repository")
    if "repository" in config and (
        not isinstance(repository, str) or not REPOSITORY_PATTERN.fullmatch(repository)
    ):
        errors.append("repository must be an OWNER/REPO string; got %r" % (repository,))
    checks = config.get("named_checks")
    if "named_checks" in config:
        if not isinstance(checks, list) or not checks:
            errors.append(
                "named_checks must be a non-empty list of check names; got %r" % (checks,)
            )
        elif not all(isinstance(name, str) and name.strip() for name in checks):
            errors.append("every named check must be a non-empty string; got %r" % (checks,))
        elif len(set(checks)) != len(checks):
            errors.append("named_checks lists a check twice: %r" % (checks,))
    marker = config.get("verification_plan_marker")
    if "verification_plan_marker" in config and not is_one_html_comment(marker):
        errors.append(
            "verification_plan_marker must be one HTML comment such as "
            "'<!-- orchestrate-verification-plan -->'; got %r" % (marker,)
        )
    model = config.get("default_model")
    if "default_model" in config and (not isinstance(model, str) or not model.strip()):
        errors.append("default_model must be a model name; got %r" % (model,))
    rounds = config.get("max_rounds")
    if "max_rounds" in config and (
        isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1
    ):
        errors.append("max_rounds must be a positive integer; got %r" % (rounds,))
    for key in MONITOR_REGEX_KEYS:
        if key not in config:
            continue
        value = config[key]
        if not isinstance(value, str):
            errors.append("%s must be a regex string; got %r" % (key, value))
            continue
        try:
            re.compile(value)
        except re.error as error:
            errors.append("%s does not compile as a regex: %s" % (key, error))
    if (
        "ignored_check_regex" in config
        and isinstance(checks, list)
        and isinstance(config["ignored_check_regex"], str)
    ):
        try:
            ignored = re.compile(config["ignored_check_regex"])
        except re.error:
            ignored = None
        if ignored is not None:
            for name in checks:
                if isinstance(name, str) and ignored.search(name):
                    errors.append(
                        "named check %r is matched by ignored_check_regex; a check cannot be "
                        "both driven to green and left out of the Monitor's verdict" % name
                    )
    for key, default in OPTIONAL_KEYS.items():
        if key not in config:
            warnings.append("%s is not set; the plugin uses %r" % (key, default))
    return errors, warnings


def main(argv):
    if len(argv) != 2 or argv[1] in ("-h", "--help"):
        sys.stderr.write(__doc__)
        return 1
    path = argv[1]
    try:
        with open(path, encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, ValueError) as error:
        print("%s: cannot read as JSON: %s" % (path, error))
        return 1
    errors, warnings = problems(config)
    for warning in warnings:
        print("%s: warning: %s" % (path, warning))
    for error in errors:
        print("%s: error: %s" % (path, error))
    if errors:
        return 1
    print("%s: ok" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
