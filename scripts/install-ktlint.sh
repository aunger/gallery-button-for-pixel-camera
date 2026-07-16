#!/usr/bin/env bash
# scripts/install-ktlint.sh -- install the ktlint CLI, pinned and SHA-256 verified.
#
# Shared by .claude/hooks/session-start.sh (STEP 3a) and the CI ktlint lint job
# (.github/workflows/lint.yml) so both provision the identical, integrity-checked
# ktlint rather than duplicating the version, checksum, and download-and-verify
# logic.
#
# ktlint is fetched as the ktlint-cli fat JAR from Maven Central--the same
# trusted, immutable, checksummed host Gradle already resolves this build
# from--rather than from GitHub Releases, which this sandbox blocks with a 403
# (issue #667). The published SHA-256 is verified before the JAR is used, so a
# proxy error page fails loudly here instead of masquerading as a corrupt
# "binary" that only breaks later with an opaque Exec format error. A small
# wrapper on PATH runs the JAR via `java -jar`.
#
# Install locations (override via the environment if needed):
#   $KTLINT_BIN_DIR/ktlint            wrapper script on PATH (default ~/.local/bin)
#   $KTLINT_JAR_DIR/ktlint-cli-*.jar  the verified JAR (default ~/.local/lib/ktlint)
# Idempotent: if both already exist, it does nothing (beyond the stale-JAR
# cleanup below).
#
# On a KTLINT_VERSION bump, the previous version's JAR is not overwritten (the
# filename is version-suffixed) and the wrapper is simply repointed at the new
# one, so the old JAR would otherwise be left behind in $KTLINT_JAR_DIR forever
# (issue #700). cleanup_stale_jars() removes every ktlint-cli-*-all.jar in that
# directory other than the one this script's KTLINT_VERSION currently names,
# once that current JAR is confirmed present, whether this run just verified
# and installed it or found it already there.
#
# When bumping KTLINT_VERSION, update KTLINT_SHA256 to the new release's
# published checksum from
# https://repo1.maven.org/maven2/com/pinterest/ktlint/ktlint-cli/<version>/ktlint-cli-<version>-all.jar.sha256

set -euo pipefail

KTLINT_VERSION="1.8.0"
KTLINT_SHA256="369ad2b789f95a011f807e1fcb690ccef80bd7cd014fd139e73ae82dcc0baeab"

KTLINT_BIN_DIR="${KTLINT_BIN_DIR:-$HOME/.local/bin}"
KTLINT_JAR_DIR="${KTLINT_JAR_DIR:-$HOME/.local/lib/ktlint}"
KTLINT_JAR="$KTLINT_JAR_DIR/ktlint-cli-$KTLINT_VERSION-all.jar"
KTLINT_BIN="$KTLINT_BIN_DIR/ktlint"

# Remove any previously installed ktlint-cli-*-all.jar other than the current
# KTLINT_VERSION's, left behind by an earlier version bump. Only called once
# the current JAR is confirmed on disk, so a failed download never costs us
# the previously working install.
cleanup_stale_jars() {
    local jar
    [[ -d "$KTLINT_JAR_DIR" ]] || return 0
    shopt -s nullglob
    for jar in "$KTLINT_JAR_DIR"/ktlint-cli-*-all.jar; do
        [[ "$jar" == "$KTLINT_JAR" ]] && continue
        echo "[install-ktlint] removing stale $(basename "$jar")"
        rm -f "$jar"
    done
    shopt -u nullglob
}

if [[ -x "$KTLINT_BIN" && -f "$KTLINT_JAR" ]]; then
    echo "[install-ktlint] ktlint $KTLINT_VERSION present--skip"
    cleanup_stale_jars
    exit 0
fi

echo "[install-ktlint] installing ktlint $KTLINT_VERSION from Maven Central..."
mkdir -p "$KTLINT_JAR_DIR" "$KTLINT_BIN_DIR"
KTLINT_URL="https://repo1.maven.org/maven2/com/pinterest/ktlint/ktlint-cli/$KTLINT_VERSION/ktlint-cli-$KTLINT_VERSION-all.jar"
TMP_JAR=$(mktemp "$KTLINT_JAR_DIR/.download-XXXXXX")
trap 'rm -f "$TMP_JAR"' EXIT
# -f: fail on an HTTP error status instead of writing the error response body to
# the JAR as if it were the artifact (issue #667).
curl -fsSL "$KTLINT_URL" -o "$TMP_JAR"
ACTUAL_SHA=$(sha256sum "$TMP_JAR" | cut -d' ' -f1)
if [[ "$ACTUAL_SHA" != "$KTLINT_SHA256" ]]; then
    echo "[install-ktlint] ERROR: ktlint SHA-256 mismatch (refusing to install)" >&2
    echo "[install-ktlint]   expected $KTLINT_SHA256" >&2
    echo "[install-ktlint]   actual   $ACTUAL_SHA" >&2
    exit 1
fi
mv "$TMP_JAR" "$KTLINT_JAR"
trap - EXIT
cat > "$KTLINT_BIN" << EOF
#!/usr/bin/env bash
exec java -jar "$KTLINT_JAR" "\$@"
EOF
chmod +x "$KTLINT_BIN"
cleanup_stale_jars
echo "[install-ktlint] ktlint installed and SHA-256 verified"
