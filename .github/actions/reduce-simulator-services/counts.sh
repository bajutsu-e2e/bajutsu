#!/bin/bash
# Log the two figures BE-0517 compares across arms: services turned off inside the Simulator, and
# Simulator processes running on the host. Observer only, so it never fails the job.
# usage: counts.sh <udid> <label>
udid=$1
label=$2
disabled=$(xcrun simctl spawn "$udid" launchctl print-disabled system 2>/dev/null | grep -c '=> disabled')
# Simulator processes run from the runtime's volume, which names them apart from the host's own.
sim_procs=$(/bin/ps -axo comm= | grep -c -E 'simruntime|CoreSimulator/Volumes')
echo "simulator-services $label disabled=$disabled sim_procs=$sim_procs"
exit 0
