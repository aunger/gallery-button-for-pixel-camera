#!/usr/bin/env bash
# .claude/install-plugins.sh: install this repository's Claude Code plugins, or move
# them to the commit .claude/gb4pc-tools pins.
#
# .claude/gb4pc-tools/.claude-plugin/marketplace.json lists the plugins, each a
# git-subdir source in aunger/dot-and-tilde pinned to one commit; this script
# installs every plugin it lists that is not turned off for this checkout. Claude
# Code fetches each pinned folder itself and caches it.
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
# Installing a plugin that is already installed and enabled does nothing, whatever
# the pin now says, so after the installs the script runs the update pair:
# `claude plugin marketplace update` re-reads the marketplace, and
# `claude plugin update` moves the one plugin it names to the new pin. A dependency
# is not updated with its dependent, so every plugin is named, github-supplement
# included.
#
# A plugin turned off for this checkout, one that .claude/settings.local.json sets
# to false, is left alone: `claude plugin install` would turn it back on (seen with
# Claude Code 2.1.322), so the script neither installs nor updates it, and its
# output names it with the command that turns it back on. That holds whether or
# not `claude plugin list` shows an install for this checkout, which it does not
# after the checkout moves. An install whose entry in that file is gone (after a
# re-clone or `git clean -fdx`) lists as not enabled, so it counts as missing and is
# installed again. Only that file is read, so a plugin turned off in another
# settings file counts as missing too. A file that is not valid JSON, or whose
# enabledPlugins is not a JSON object, is ignored, and the output says so.
#
# Installing a plugin turns its turned-off dependencies back on; orchestrate
# depends on github-supplement. So after the installs, the script turns each
# plugin that came back on that way off again, together with every plugin that
# depends on it (a plugin cannot run without its dependencies, and Claude Code
# refuses to turn a dependency off while a plugin that needs it is on), and names
# them all in its output as left off. Until then they are on: a session that
# loads plugins before the hook finishes could load them.
#
# All of that is skipped when one `claude plugin list --json` already shows every
# plugin turned off or installed for this checkout at the pinned commit (its
# version starts with the pin's first 12 hex digits), which is the usual case: the
# session-start hook runs this on every SessionStart, including resume, /clear and
# compaction.
#
# The session-start hook runs this in cloud sessions. Elsewhere, or when the hook
# did not run, run it by hand from anywhere. The exception is a cloud session with
# more than one repository attached: its project is their parent directory, so the
# hook does not run and installs recorded for this checkout do not load there
# either; such a session needs only this repository attached. A cloud
# environment's Setup script can also run it before Claude Code launches, which
# then loads the plugins at startup
# (aunger/dot-and-tilde#1). A plugin installed while Claude Code is
# running loads only after the user runs /reload-skills; one that `plugin update`
# moved to a new pin takes effect only after Claude Code restarts, as that command
# itself says.
#
# Output: the claude commands' own output goes to stderr. Stdout is one line, which
# the session-start hook passes into the session's context: whether the run changed
# anything, and so what to ask the user for. That advice is for a running session,
# so the line gives it only inside Claude Code, which sets CLAUDECODE=1 in its Bash
# tool and its hooks. Run anywhere else, such as from a cloud environment's Setup
# script before Claude Code launches, or from a terminal, the line says instead that
# Claude Code loads the plugins when it next starts in this checkout.
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
# are installed and enabled for this checkout at the pin, with "missing:",
# "disabled:" or "moved:" before each that is not: one enabled at another version
# is moved, one set to false in .claude/settings.local.json is disabled, installed
# or not, and any other is missing. When .claude/settings.local.json is not valid
# JSON, or its enabledPlugins is not a JSON object, the file is ignored, so no
# plugin counts as disabled, and the first line is "settings:ignored:" and why.
# Only local-scope entries recorded for this repository root count: the list also
# shows other checkouts' installs, and marks them enabled when this checkout's
# settings enable the same plugin id.
plugin_states() {
    python3 -c '
import json, os, sys
marketplace, pin, root = sys.argv[1], sys.argv[2][:12], os.path.realpath(sys.argv[3])
listed = json.load(sys.stdin)
settings_path = os.path.join(root, ".claude", "settings.local.json")
ignored = ""
try:
    with open(settings_path, encoding="utf-8") as handle:
        settings = json.load(handle)
except OSError:
    settings = {}
except ValueError:
    settings, ignored = {}, "it is not valid JSON"
if not isinstance(settings, dict):
    settings, ignored = {}, "it is not a JSON object"
enabled_plugins = settings.get("enabledPlugins", {})
if not isinstance(enabled_plugins, dict):
    enabled_plugins, ignored = {}, "its enabledPlugins is not a JSON object"
if ignored:
    print("settings:ignored:" + ignored)
for name in sys.argv[4:]:
    entries = [
        entry
        for entry in listed
        if entry.get("id") == name + "@" + marketplace
        and entry.get("scope") == "local"
        and os.path.realpath(str(entry.get("projectPath", ""))) == root
    ]
    versions = [str(entry.get("version", "")) for entry in entries if entry.get("enabled")]
    if pin and any(version.startswith(pin) for version in versions):
        print(name)
    elif versions:
        print("moved:" + name)
    elif enabled_plugins.get(name + "@" + marketplace) is False:
        print("disabled:" + name)
    else:
        print("missing:" + name)
' "$MARKETPLACE" "$PIN" "$REPO_ROOT" "${PLUGINS[@]}"
}

# Reads `claude plugin list --json` on stdin. Prints the plugins named as
# arguments, and every plugin installed and enabled for this checkout that
# depends on one of them, directly or through another, one per line, each before
# the plugins it depends on: the order `claude plugin disable` accepts. A
# plugin's dependencies are those its installed plugin.json declares.
turn_off_order() {
    python3 -c '
import json, os, sys
marketplace, root, targets = sys.argv[1], os.path.realpath(sys.argv[2]), set(sys.argv[3:])
needs = {}
for entry in json.load(sys.stdin):
    name, _, market = str(entry.get("id", "")).partition("@")
    if (
        market != marketplace
        or entry.get("scope") != "local"
        or not entry.get("enabled")
        or os.path.realpath(str(entry.get("projectPath", ""))) != root
    ):
        continue
    manifest = os.path.join(str(entry.get("installPath", "")), ".claude-plugin", "plugin.json")
    try:
        with open(manifest, encoding="utf-8") as handle:
            declared = json.load(handle).get("dependencies") or []
    except (OSError, ValueError, AttributeError):
        declared = []
    needs[name] = {
        str(dep.get("name", "") if isinstance(dep, dict) else dep).partition("@")[0]
        for dep in declared
    }
off = set(targets)
grown = True
while grown:
    grown = False
    for name, deps in needs.items():
        if name not in off and deps & off:
            off.add(name)
            grown = True
order = []
while off:
    ready = sorted(p for p in off if not any(p in needs.get(q, ()) for q in off if q != p))
    order += ready or sorted(off)
    off -= set(ready or off)
print("\n".join(order))
' "$MARKETPLACE" "$REPO_ROOT" "$@"
}

before=$(claude plugin list --json | plugin_states)

# Appended to the one line of output when the settings file was ignored, since a
# plugin turned off there may then have been installed and turned on.
settings_warning=""
ignored_because=$(sed -n 's/^settings:ignored://p' <<< "$before")
if [[ -n "$ignored_because" ]]; then
    settings_warning=" Warning: ignored $REPO_ROOT/.claude/settings.local.json because $ignored_because, so no plugin counted as turned off; one the user turned off there may have been installed and turned on. Tell the user."
fi

# The plugins to work on: those missing or at another version, not those turned off.
PENDING=()
while IFS= read -r line; do
    case "$line" in
        missing:* | moved:*) PENDING+=("${line#*:}") ;;
    esac
done <<< "$before"

# Appended to the one line of output: the plugins turned off in the plugin_states
# output $1, with the command that turns each back on.
left_off_warning() {
    local off plugin commands=""
    off=$(sed -n 's/^disabled://p' <<< "$1" | paste -sd ' ' -)
    [[ -n "$off" ]] || return 0
    for plugin in $off; do
        commands+="${commands:+; }\`claude plugin enable $plugin@$MARKETPLACE --scope local\`"
    done
    printf ' Warning: left alone because turned off for this checkout: %s. Tell the user, who can turn each back on in %s with %s, then rerun .claude/install-plugins.sh.' "$off" "$REPO_ROOT" "$commands"
}

# Whether a running Claude Code session reads stdout: see "Output" above.
in_session=false
[[ "${CLAUDECODE:-}" == "1" ]] && in_session=true
next_start="Claude Code loads them when it next starts in $REPO_ROOT."

if [[ ${#PENDING[@]} -eq 0 ]]; then
    current=$(grep -v ':' <<< "$before" | paste -sd ' ' - || true)
    if $in_session; then
        advice=" If /orchestrate or another plugin skill is missing from this session, ask the user to run /reload-skills."
    else
        advice=""
    fi
    echo "[install-plugins]${current:+ Already installed at the commit .claude/gb4pc-tools pins: $current.$advice}$(left_off_warning "$before")$settings_warning"
    exit 0
fi

claude plugin marketplace add "$MARKETPLACE_DIR" --scope local >&2
for plugin in "${PENDING[@]}"; do
    claude plugin install "$plugin@$MARKETPLACE" --scope local >&2
done
claude plugin marketplace update "$MARKETPLACE" >&2
for plugin in "${PENDING[@]}"; do
    claude plugin update "$plugin@$MARKETPLACE" --scope local >&2
done
listed=$(claude plugin list --json)
after=$(plugin_states <<< "$listed")

# Installing a plugin turns its dependencies back on, those turned off included.
# Each plugin that was turned off and no longer is came back on that way, and is
# turned off again, after every plugin that depends on it.
TURNED_ON=()
for plugin in $(sed -n 's/^disabled://p' <<< "$before"); do
    grep -Fqx "disabled:$plugin" <<< "$after" || TURNED_ON+=("$plugin")
done
TURNED_OFF=()
if [[ ${#TURNED_ON[@]} -gt 0 ]]; then
    order=$(turn_off_order "${TURNED_ON[@]}" <<< "$listed")
    for plugin in $order; do
        claude plugin disable "$plugin@$MARKETPLACE" --scope local >&2
        TURNED_OFF+=("$plugin")
    done
    after=$(claude plugin list --json | plugin_states)
fi

# Whether plugin $1 is one this run turned off again.
turned_off() {
    local plugin
    for plugin in ${TURNED_OFF[@]+"${TURNED_OFF[@]}"}; do
        [[ "$plugin" == "$1" ]] && return 0
    done
    return 1
}

# The words of $1 that are not plugins this run turned off again.
still_on() {
    local plugin kept=""
    for plugin in $1; do
        turned_off "$plugin" || kept+="${kept:+ }$plugin"
    done
    printf '%s' "$kept"
}

turned_off_warning=""
if [[ ${#TURNED_OFF[@]} -gt 0 ]]; then
    turned_off_warning=" Warning: installing turned back on, as a dependency, what this checkout had turned off (${TURNED_ON[*]}), so the script turned off again ${TURNED_OFF[*]}: each plugin turned back on, and the plugins that depend on one."
fi

# Each plugin worked on must now be at the pin, or off again if this run turned
# it off.
failed=""
for plugin in "${PENDING[@]}" ${TURNED_ON[@]+"${TURNED_ON[@]}"}; do
    if turned_off "$plugin"; then
        grep -Fqx "disabled:$plugin" <<< "$after" && continue
    else
        grep -Fqx "$plugin" <<< "$after" && continue
    fi
    failed+="$(grep -E "(^|:)$plugin\$" <<< "$after") "
done
if [[ -n "$failed" ]]; then
    echo "[install-plugins] warning: after installing, these are still not enabled at the commit .claude/gb4pc-tools pins: $failed" >&2
    exit 1
fi
# Name what changed: plugins at another version were moved, and plugins with no
# install for this checkout (a plugin newly listed, say) were installed. A plugin
# turned off again is named only as left off.
moved=$(still_on "$(sed -n 's/^moved://p' <<< "$before")")
added=$(still_on "$(sed -n 's/^missing://p' <<< "$before")")
warnings="$turned_off_warning$(left_off_warning "$after")$settings_warning"
if [[ -z "$moved" && -z "$added" ]]; then
    echo "[install-plugins]$warnings"
elif [[ -n "$moved" ]]; then
    changed="Moved $moved to the commit .claude/gb4pc-tools pins"
    if [[ -n "$added" ]]; then
        changed+=" and installed $added there"
    fi
    if $in_session; then
        echo "[install-plugins] $changed. A plugin that was already loaded keeps its earlier version until Claude Code restarts: ask the user to restart the session, or to run /reload-skills if /orchestrate or another plugin skill is missing.$warnings"
    else
        echo "[install-plugins] $changed. $next_start A session already running there keeps the earlier version until it restarts.$warnings"
    fi
elif $in_session; then
    if [[ $(wc -w <<< "$added") -gt 1 ]]; then
        they="They load"
    else
        they="It loads"
    fi
    echo "[install-plugins] Installed $added at the commit .claude/gb4pc-tools pins. $they in this session only after /reload-skills: ask the user to run /reload-skills.$warnings"
else
    echo "[install-plugins] Installed $added at the commit .claude/gb4pc-tools pins. $next_start$warnings"
fi
