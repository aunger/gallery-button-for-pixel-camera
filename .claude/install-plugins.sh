#!/usr/bin/env bash
# .claude/install-plugins.sh: install this repository's Claude Code plugins, or move
# them to the commit .claude/gb4pc-tools pins.
#
# .claude/gb4pc-tools/.claude-plugin/marketplace.json lists the plugins, each a
# git-subdir source in aunger/dot-and-tilde pinned to one commit; this script
# installs every plugin it lists. Claude Code fetches each pinned folder itself and
# caches it.
#
# The marketplace and the installs are at local scope: Claude Code records them in
# this checkout's .claude/settings.local.json (gitignored), so the plugins and their
# hooks are enabled in this repository only, not in the user's other projects. The
# script runs its commands from the repository root, so that is the project they
# are recorded for wherever it is started from.
#
# `claude plugin marketplace add` comes first and names this checkout's copy of the
# marketplace. Claude Code keeps one source per marketplace name for the whole user,
# and the add re-points it here when another checkout (a second clone, a worktree, a
# moved repository) added it first; `plugin install --marketplace <path>` would
# instead refuse a name that is already added from a different path.
#
# Installing a plugin that is already installed does nothing, whatever the pin now
# says, so after the installs the script runs the update pair:
# `claude plugin marketplace update` re-reads the marketplace, and
# `claude plugin update` moves the one plugin it names to the new pin. A dependency
# is not updated with its dependent, so every plugin is named, github-supplement
# included.
#
# All of that is skipped when one `claude plugin list --json` already shows every
# plugin installed for this checkout at the pinned commit (its version starts with
# the pin's first 12 hex digits), which is the usual case: the session-start hook
# runs this on every SessionStart, including resume, /clear and compaction.
#
# The session-start hook runs this in cloud sessions. Elsewhere, or when the hook
# did not run, run it by hand from anywhere. A plugin installed while Claude Code is
# running loads only after the user runs /reload-skills; one that `plugin update`
# moved to a new pin takes effect only after Claude Code restarts, as that command
# itself says.
#
# Output: the claude commands' own output goes to stderr. Stdout is one line, which
# the session-start hook passes into the session's context: whether the run changed
# anything, and so what to ask the user for.
#
# Exits non-zero at the first command that fails.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARKETPLACE_DIR="$REPO_ROOT/.claude/gb4pc-tools"
cd "$REPO_ROOT"

# The marketplace's name, its pin, then one plugin name per line. A while-read loop
# rather than mapfile, which bash 3.2 (macOS's /bin/bash) lacks.
NAMES=()
while IFS= read -r name; do
    NAMES+=("$name")
done < <(python3 -c '
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    marketplace = json.load(handle)
print(marketplace["name"])
print(marketplace["plugins"][0]["source"].get("sha", ""))
for plugin in marketplace["plugins"]:
    print(plugin["name"])
' "$MARKETPLACE_DIR/.claude-plugin/marketplace.json")
MARKETPLACE="${NAMES[0]}"
PIN="${NAMES[1]}"
PLUGINS=("${NAMES[@]:2}")

# Reads `claude plugin list --json` on stdin. Prints the plugins (one per line) that
# are installed and enabled for this checkout at the pin, with "missing:" or
# "moved:" before each that is not, a plugin with no such install being missing and
# one at another version moved. Only local-scope entries recorded for this
# repository root count: the list also shows other checkouts' installs, and marks
# them enabled when this checkout's settings enable the same plugin id.
plugin_states() {
    python3 -c '
import json, os, sys
marketplace, pin, root = sys.argv[1], sys.argv[2][:12], os.path.realpath(sys.argv[3])
listed = json.load(sys.stdin)
for name in sys.argv[4:]:
    versions = [
        str(entry.get("version", ""))
        for entry in listed
        if entry.get("id") == name + "@" + marketplace
        and entry.get("enabled")
        and entry.get("scope") == "local"
        and os.path.realpath(str(entry.get("projectPath", ""))) == root
    ]
    if pin and any(version.startswith(pin) for version in versions):
        print(name)
    elif versions:
        print("moved:" + name)
    else:
        print("missing:" + name)
' "$MARKETPLACE" "$PIN" "$REPO_ROOT" "${PLUGINS[@]}"
}

before=$(claude plugin list --json | plugin_states)
if ! grep -q ':' <<< "$before"; then
    echo "[install-plugins] ${PLUGINS[*]} were already installed at the commit .claude/gb4pc-tools pins. If /orchestrate is missing from this session, ask the user to run /reload-skills."
    exit 0
fi

claude plugin marketplace add "$MARKETPLACE_DIR" --scope local >&2
for plugin in "${PLUGINS[@]}"; do
    claude plugin install "$plugin@$MARKETPLACE" --scope local >&2
done
claude plugin marketplace update "$MARKETPLACE" >&2
for plugin in "${PLUGINS[@]}"; do
    claude plugin update "$plugin@$MARKETPLACE" --scope local >&2
done
after=$(claude plugin list --json | plugin_states)

if grep -q ':' <<< "$after"; then
    echo "[install-plugins] warning: after installing, these are still not enabled at the commit .claude/gb4pc-tools pins: $(grep ':' <<< "$after" | tr '\n' ' ')" >&2
    exit 1
fi
if grep -q '^moved:' <<< "$before"; then
    echo "[install-plugins] Moved ${PLUGINS[*]} to the commit .claude/gb4pc-tools pins. A plugin that was already loaded keeps its earlier version until Claude Code restarts: ask the user to restart the session, or to run /reload-skills if /orchestrate is missing."
else
    echo "[install-plugins] Installed ${PLUGINS[*]} at the commit .claude/gb4pc-tools pins. They load in this session only after /reload-skills: ask the user to run /reload-skills."
fi
