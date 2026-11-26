#!/bin/bash
# Turn off the services in disabled-services.txt inside a booted Simulator (mode 'reduced'), then
# reboot it. Mode 'stock' reboots alone, so both arms of BE-0517's measurement pay the same reboot.
# `launchctl disable` cannot reach a shut-down device, hence disable-then-reboot rather than an
# offline write into the Simulator's private launchd store (BE-0517, unit 2).
# usage: apply.sh <udid> <reduced|stock>
set -euo pipefail
udid=$1
mode=$2
here=$(cd "$(dirname "$0")" && pwd)

case "$mode" in
  reduced | stock) ;;
  *)
    echo "::error::unknown mode '$mode' (expected 'reduced' or 'stock')" >&2
    exit 1
    ;;
esac

start=$(date +%s)
if [ "$mode" = "reduced" ]; then
  labels=$(grep -v -e '^#' -e '^[[:space:]]*$' "$here/disabled-services.txt")
  # A label this runtime does not know is not a failure. Every label failing is: `simctl spawn` or
  # the `system/` domain itself was refused, and the job would run a stock Simulator while logging
  # itself as the reduced arm of BE-0517's measurement.
  # shellcheck disable=SC2016  # $1 and $2 belong to the inner shell
  failed=$(printf '%s\n' "$labels" |
    xargs -P 4 -I {} sh -c 'xcrun simctl spawn "$1" launchctl disable "system/$2" >/dev/null || echo "$2"' \
      _ "$udid" {})
  total=$(printf '%s\n' "$labels" | wc -l | tr -d ' ')
  nfailed=$(printf '%s' "$failed" | grep -c . || true)
  if [ -n "$failed" ]; then
    printf '%s\n' "$failed" | sed 's/^/not disabled: /'
  fi
  echo "simulator-services disable_failed=$nfailed of $total"
  if [ "$nfailed" -eq "$total" ]; then
    echo "::error::launchctl disable failed for every listed service; the Simulator would run stock" >&2
    exit 1
  fi
fi
xcrun simctl shutdown "$udid"
xcrun simctl boot "$udid"
xcrun simctl bootstatus "$udid" -b
echo "simulator-services route=disable-then-reboot mode=$mode reboot_seconds=$(($(date +%s) - start))"
