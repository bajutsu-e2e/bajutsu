#!/bin/bash
# EXPERIMENT ONLY, not for merge: reboot a booted Simulator, optionally with the services listed in
# disabled-services.txt turned off first. Both arms reboot, so they differ only in the disabled set.
# usage: disable.sh <udid> <true|false>
set -euo pipefail
udid=$1
slim=$2
here=$(cd "$(dirname "$0")" && pwd)

if [ "$slim" = "true" ]; then
  # A label the runtime does not know is not a failure; the count printed below is the record.
  # shellcheck disable=SC2016  # $1 belongs to the inner shell
  xargs -P 4 -I {} sh -c 'xcrun simctl spawn "$1" launchctl disable "system/$2" || echo "not disabled: $2"' \
    _ "$udid" {} <"$here/disabled-services.txt"
fi
xcrun simctl shutdown "$udid"
xcrun simctl boot "$udid"
xcrun simctl bootstatus "$udid" -b
