#!/usr/bin/env bash
# scripts/install-pinned-requirements.sh -- install a hash-pinned pip lock into
# the user site, skipping the work when that exact lock is already installed.
#
# Shared by the locks .claude/hooks/session-start.sh provisions per session, so
# the install command and its skip marker are defined once instead of being
# copied for each lock.
#
# Usage: install-pinned-requirements.sh <lock-file> <description>
#
#   <lock-file>    a fully resolved requirements lock: every package, top-level
#                  and transitive, pinned to an exact version and a SHA-256 hash.
#   <description>  what the lock provides, for this script's log lines.
#
# --require-hashes makes pip refuse to install anything whose artifact does not
# match a hash in the lock, so a compromised or substituted wheel on PyPI (or an
# intercepted download) cannot slip in (issue #699). It also forces every
# dependency to be hash-pinned, which is why these locks list the full transitive
# closure rather than just the top-level packages.
#
# --force-reinstall installs what the lock names even when something already
# satisfies the requirement. That is what makes the lock authoritative: without
# it, a package the base image happens to ship at the pinned version is left in
# place and the session runs bytes the repository never declared.
#
# CI does not call this script for its own installs (its pip is not always on
# PATH, e.g. the shell-tests job's venv, and it has no marker to gate). Its
# inline pip install lines pass --force-reinstall independently instead, and
# scripts/test_install_pinned_requirements.sh case (i) checks every one of them
# does, so CI cannot drift back to running whatever a runner image happens to
# ship (issue #810).
#
# The install is gated on the SHA-256 of the lock: a marker file records the hash
# that was last installed, so a re-run against an unchanged lock skips the work
# while any edit to the pinned versions reinstalls. The marker is named after the
# lock, and is written only after pip succeeds, so a failed install is retried
# rather than being remembered as done.
#
# The marker lives under $HOME, which is the session's own filesystem and not the
# cached image: only .claude/setup-environment.sh writes into that (see
# .claude/environment.md). So this skips repeat runs within a container, which is
# what the session-start hook's idempotence needs, and a later session installs
# again from scratch.
#
# Idempotent: re-running against an already installed lock is a fast no-op.

set -euo pipefail

LOCK="${1:-}"
LABEL="${2:-}"

if [[ -z "$LOCK" || -z "$LABEL" ]]; then
    echo "[install-pinned-requirements] usage: $(basename "$0") <lock-file> <description>" >&2
    exit 2
fi

if [[ ! -f "$LOCK" ]]; then
    echo "[install-pinned-requirements] ERROR: no such lock file: $LOCK" >&2
    exit 1
fi

# Two locks whose paths differ but whose file names match would share a marker,
# and the second would be skipped as "already installed" on the strength of the
# first. Nothing here can see the other call sites, so the guard against that
# lives in scripts/test_install_pinned_requirements.sh, which checks that every
# lock the session-start hook installs has a distinct file name.
MARKER_DIR="$HOME/.local/share/gb4pc"
MARKER="$MARKER_DIR/$(basename "$LOCK" .txt).sha256"
LOCK_SHA=$(sha256sum "$LOCK" | cut -d' ' -f1)

if [[ -f "$MARKER" && "$(cat "$MARKER" 2>/dev/null)" == "$LOCK_SHA" ]]; then
    echo "[install-pinned-requirements] $LABEL up to date--skip"
    exit 0
fi

echo "[install-pinned-requirements] installing $LABEL from $(basename "$LOCK")..."
pip install --user --force-reinstall --require-hashes --quiet -r "$LOCK"
mkdir -p "$MARKER_DIR"
echo "$LOCK_SHA" > "$MARKER"
echo "[install-pinned-requirements] $LABEL installed"
