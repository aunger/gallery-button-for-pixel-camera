#!/usr/bin/env bash
# backup-restore-check.sh: back up GB4PC's prefs on a connected device, wipe the app, restore, and
# check which prefs files came back (issues #1431, #1432, #1433, for PR #1430 and issue #1235).
#
# It seeds both prefs files with known values through `run-as`, so the app's debug build must be
# installed. It then runs the local transport's backup and restore, and reads the files back. It
# tests what the backup rules decide, which files travel, not what the app does with them after.
#
# Usage:
#   scripts/ci/test-support/backup-restore-check.sh <adb> <mode> <expect>
#
# Arguments:
#   <adb>     Path to the adb binary.
#   <mode>    cloud           an ordinary backup and restore. On API 31+ this is the
#                             <cloud-backup> section of data_extraction_rules.xml; on API 30 it
#                             is backup_rules.xml (android:fullBackupContent).
#             device-transfer the same, with the local transport told it is a device transfer
#                             (is_device_transfer=true), so the <device-transfer> section applies.
#   <expect>  excluded        gb4pc_install_prefs.xml must not come back, the rules' intent.
#             restored        gb4pc_install_prefs.xml must come back. Used against a build whose
#                             rules drop that section's exclusion, to show the mode reaches that
#                             section: an "excluded" result alone cannot, since both sections
#                             exclude the same file.
#
# In both cases gb4pc_prefs.xml must come back, or the run fails: a backup that captured nothing
# would otherwise pass "excluded" vacuously.
#
# Exits 0 when the files are as <expect> says, 1 when they are not, 2 on a usage error. Every wait
# carries a bound of its own and names itself when it expires.

set -uo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 <adb> <cloud|device-transfer> <excluded|restored>" >&2
  exit 2
fi
ADB="$1"
MODE="$2"
EXPECT="$3"
[[ "$MODE" == cloud || "$MODE" == device-transfer ]] || { echo "unknown mode: $MODE" >&2; exit 2; }
[[ "$EXPECT" == excluded || "$EXPECT" == restored ]] || { echo "unknown expectation: $EXPECT" >&2; exit 2; }

PKG=com.gb4pc
PREFS=gb4pc_prefs.xml
INSTALL_PREFS=gb4pc_install_prefs.xml
TRANSPORT=com.android.localtransport/.LocalTransport
TRANSFER_PARAM=backup_local_transport_parameters

fail() {
  echo "FAIL ($MODE, expect $EXPECT): $*" >&2
  exit 1
}

# Always leave the device as found: the transfer parameter outlives this script otherwise.
cleanup() { "$ADB" shell settings delete secure "$TRANSFER_PARAM" > /dev/null 2>&1 || true; }
trap cleanup EXIT

# write_prefs <file> <body>: writes <body> as the app's own shared_prefs/<file>.
write_prefs() {
  printf "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>\n<map>\n%s\n</map>\n" "$2" \
    | "$ADB" shell "run-as $PKG sh -c 'mkdir -p shared_prefs && cat > shared_prefs/$1'" \
    || fail "could not write $1 through run-as (is the debug build installed?)"
}

# read_prefs <file>: prints the file, or nothing when it is absent.
read_prefs() { "$ADB" shell "run-as $PKG cat shared_prefs/$1" 2> /dev/null | tr -d '\r'; }

# A package that was installed and never launched is in the stopped state, which the backup
# manager can refuse to back up; launch once, then seed without touching the app again.
echo "=== Launching the app once"
"$ADB" shell monkey -p "$PKG" -c android.intent.category.LAUNCHER 1 > /dev/null 2>&1 || true
sleep 5

echo "=== Seeding both prefs files"
write_prefs "$PREFS" '    <string name="gallery_package">com.example.gallery</string>
    <string name="overlay_positions">backup-restore-check</string>
    <long name="camera_debounce_ms" value="123" />
    <boolean name="focusable_overlay" value="true" />'
write_prefs "$INSTALL_PREFS" '    <boolean name="service_enabled" value="true" />
    <boolean name="setup_completed" value="true" />
    <boolean name="permission_history_seeded" value="true" />
    <boolean name="runtime_permission_requested_android.permission.READ_MEDIA_IMAGES" value="true" />'
[[ -n "$(read_prefs "$INSTALL_PREFS")" ]] || fail "$INSTALL_PREFS reads back empty after being written"

echo "=== Selecting the local transport ($MODE)"
"$ADB" shell bmgr enable true
"$ADB" shell bmgr transport "$TRANSPORT" | grep -q . || fail "bmgr transport printed nothing for $TRANSPORT"
if [[ "$MODE" == device-transfer ]]; then
  "$ADB" shell settings put secure "$TRANSFER_PARAM" 'is_device_transfer=true'
else
  "$ADB" shell settings delete secure "$TRANSFER_PARAM"
fi

echo "=== Backing up"
backup_out="$("$ADB" shell bmgr backupnow "$PKG" 2>&1)"
echo "$backup_out"
if ! grep -q "Package $PKG with result: Success" <<< "$backup_out"; then
  echo "--- diagnostics" >&2
  "$ADB" shell dumpsys package "$PKG" | grep -iE "flags=|pkgFlags|stopped|versionName|targetSdk" >&2 || true
  "$ADB" shell dumpsys backup | head -60 >&2 || true
  fail "bmgr backupnow did not report Success for $PKG"
fi

echo "=== Clearing the app's data"
"$ADB" shell pm clear "$PKG" | grep -q Success || fail "pm clear did not report Success"
[[ -z "$(read_prefs "$PREFS")" ]] || fail "$PREFS survived pm clear, so the restore proves nothing"

echo "=== Restoring"
# `bmgr restore <package>` is rejected on API 30 and 35 ("restore <token> <package>" is required),
# so list the transport's restore sets and try each token until the settings file reappears.
sets_out="$("$ADB" shell bmgr list sets 2>&1)"
echo "$sets_out"
tokens="$(sed -n 's/^[[:space:]]*\([0-9a-fA-F][0-9a-fA-F]*\)[[:space:]]*:.*/\1/p' <<< "$sets_out")"
[[ -n "$tokens" ]] || fail "bmgr list sets showed no restore set"

# The restore is asynchronous: wait, up to a bound, for the settings file to reappear.
restored=false
for token in $tokens; do
  echo "--- bmgr restore $token $PKG"
  "$ADB" shell bmgr restore "$token" "$PKG" 2>&1
  WAIT_START=$SECONDS
  until [[ -n "$(read_prefs "$PREFS")" ]]; do
    [[ $((SECONDS - WAIT_START)) -ge 30 ]] && break
    sleep 2
  done
  if [[ -n "$(read_prefs "$PREFS")" ]]; then restored=true; break; fi
done
$restored || fail "$PREFS did not come back from any restore set within 30s each"

echo "=== Restored files"
restored_settings="$(read_prefs "$PREFS")"
restored_install="$(read_prefs "$INSTALL_PREFS")"
echo "--- $PREFS"
echo "$restored_settings"
echo "--- $INSTALL_PREFS"
echo "${restored_install:-(absent)}"

grep -q 'com.example.gallery' <<< "$restored_settings" || fail "gallery_package did not come back"
grep -q 'name="camera_debounce_ms" value="123"' <<< "$restored_settings" \
  || fail "camera_debounce_ms did not come back"

if [[ "$EXPECT" == excluded ]]; then
  [[ -z "$restored_install" ]] || fail "$INSTALL_PREFS came back, but the rules exclude it"
else
  grep -q 'name="service_enabled" value="true"' <<< "$restored_install" \
    || fail "$INSTALL_PREFS did not come back, though this build's rules do not exclude it for $MODE"
fi
echo "PASS ($MODE, expect $EXPECT)"
