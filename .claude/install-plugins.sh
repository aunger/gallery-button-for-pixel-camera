#!/usr/bin/env bash
# .claude/install-plugins.sh: install this repository's Claude Code plugins, or move
# them to the commit .claude/gb4pc-tools pins.
#
# .claude/gb4pc-tools/.claude-plugin/marketplace.json lists the plugins, each a
# git-subdir source in aunger/dot-and-tilde pinned to one commit; this script
# installs every plugin it lists. Claude Code fetches each pinned folder itself and
# caches it. The installs are at user scope.
#
# Installing a plugin that is already installed does nothing, whatever the pin now
# says, so after the installs the script runs the update pair:
# `claude plugin marketplace update` re-reads the marketplace, and
# `claude plugin update` moves the one plugin it names to the new pin. A dependency
# is not updated with its dependent, so every plugin is named, github-supplement
# included.
#
# The session-start hook runs this in cloud sessions. Elsewhere, or when the hook
# did not run, run it by hand from anywhere. A plugin installed while Claude Code is
# running loads only after the user runs /reload-skills.
#
# Output: the claude commands' own output goes to stderr. Stdout is one line, which
# the session-start hook passes into the session's context: whether the install
# changed anything, and so whether to ask the user for /reload-skills.
#
# Exits non-zero at the first command that fails.

set -euo pipefail

MARKETPLACE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/gb4pc-tools"

# The marketplace's name, then one plugin name per line.
NAMES=$(python3 -c '
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    marketplace = json.load(handle)
print(marketplace["name"])
for plugin in marketplace["plugins"]:
    print(plugin["name"])
' "$MARKETPLACE_DIR/.claude-plugin/marketplace.json")
mapfile -t NAMES <<< "$NAMES"
MARKETPLACE="${NAMES[0]}"
PLUGINS=("${NAMES[@]:1}")

before=$(claude plugin list --json)
for plugin in "${PLUGINS[@]}"; do
    claude plugin install "$plugin" --marketplace "$MARKETPLACE_DIR" >&2
done
claude plugin marketplace update "$MARKETPLACE" >&2
for plugin in "${PLUGINS[@]}"; do
    claude plugin update "$plugin@$MARKETPLACE" >&2
done
after=$(claude plugin list --json)

if [[ "$before" == "$after" ]]; then
    echo "[install-plugins] ${PLUGINS[*]} were already installed at the commit .claude/gb4pc-tools pins. If /orchestrate is missing from this session, ask the user to run /reload-skills."
else
    echo "[install-plugins] Installed ${PLUGINS[*]} at the commit .claude/gb4pc-tools pins. They load in this session only after /reload-skills: ask the user to run /reload-skills."
fi
