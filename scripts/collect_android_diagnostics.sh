#!/usr/bin/env bash
# Collect the emulator's own state at the end of an android-e2e.yml job (BE-0367 Layer 2).
#
# This must run from *inside* each job's `reactivecircus/android-emulator-runner` step. That step
# boots the emulator, runs its `script:`, and kills it when the step ends, so an ordinary step placed
# after it — and a step-level `if: failure()` gate — would run with no device attached and every
# `adb` read here would collect nothing. The failure tier therefore keys off the run command's own
# exit code, passed in as $1, the same shape the workflow's `poll_cpuinfo` poller already uses to
# survive a failing run.
#
# Usage, from a job's `script:` (one line, since the emulator-runner runs each line as its own sh):
#   make -C demos/showcase/android e2e ; rc=$? ;
#     scripts/collect_android_diagnostics.sh "$rc" || true ; exit $rc
#
# Every collection is best-effort: nothing here may change a job's verdict, so `set -e` is
# deliberately absent, each command is bounded by `timeout` and its failure is recorded in the
# artifact rather than raised. The caller preserves and re-raises the real exit code itself.
#
# Output lands under `runs/diagnostics/`, which each job's existing `Upload run artifacts` step
# already carries — no new upload wiring.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 0

rc="${1:-0}"
case "$rc" in
  '' | *[!0-9]*) rc=0 ;;  # a caller that lost the exit code gets the cheap tier, never a crash
esac

out="runs/diagnostics"
mkdir -p "$out" || exit 0

# Bounds every collection. The cheap tier is a handful of device reads; `adb bugreport` is Android's
# own comprehensive collector and runs tens of seconds, so it gets its own longer ceiling below.
readonly CHEAP_TIMEOUT=120
readonly BUGREPORT_TIMEOUT=300

# Run one collection into `$out/<name>`, recording a failure in the file instead of aborting.
collect() {
  local name="$1"
  local seconds="$2"
  shift 2
  local path="$out/$name"
  printf '+ %s\n' "$*" >"$path"
  # `$?` on the right of `||` is the collection's own status, which `if !` would have swallowed.
  timeout "$seconds" "$@" >>"$path" 2>&1 || printf '<collection failed (exit %s)>\n' "$?" >>"$path"
}

# --- Always: cheap reads, every run ---
#
# The full-buffer dump is not the per-scenario `deviceLog` interval evidence over again: that streams
# only while a scenario with `capture: [deviceLog]` runs, so it has nothing to show for a job that
# failed before any scenario began, or whose stream cut off mid-write when the process serving it
# died. This reads the device's own retained ring buffer instead.
collect logcat-full.txt "$CHEAP_TIMEOUT" adb logcat -d -b main,system,crash,events,radio
collect meminfo.txt "$CHEAP_TIMEOUT" adb shell dumpsys meminfo
# The environment snapshot — API level, ABI, and the emulator's own command-line flags — is what lets
# a later reader ask whether failures cluster on one emulator configuration.
collect getprop.txt "$CHEAP_TIMEOUT" adb shell getprop
collect devices.txt "$CHEAP_TIMEOUT" adb devices -l

if [ "$rc" -eq 0 ]; then
  # Say where the evidence went. Every collection above writes to a file and prints nothing, so
  # without this the job log's only trace of the sweep is an unexplained gap — and on the failing
  # tier below that gap is the tens of seconds `adb bugreport` takes, which reads as the job having
  # hung rather than having collected. A reader should not have to know this script exists to find
  # what it wrote.
  printf 'collect_android_diagnostics: wrote the always tier to %s\n' "$out"
  exit 0
fi

# --- On failure only: the heavy collectors ---
#
# A bugreport is a multi-megabyte archive; reserving it for the runs that need it keeps the cheap
# tier unconditional without paying that cost on six jobs of every green run.
collect bugreport.log "$BUGREPORT_TIMEOUT" adb bugreport "$out/bugreport.zip"

# Native crash reports and Application Not Responding (ANR) traces live device-side on Android — the
# two classes the iOS sweep gets for free from the host's own DiagnosticReports directory — so they
# need an explicit rooted pull. The AVD profile these jobs use (`target: google_apis`, not
# `google_apis_playstore`) permits `adb root`; `wait-for-device` covers adbd restarting as root.
if timeout "$CHEAP_TIMEOUT" adb root >>"$out/root.log" 2>&1 &&
  timeout "$CHEAP_TIMEOUT" adb wait-for-device >>"$out/root.log" 2>&1; then
  collect tombstones.log "$CHEAP_TIMEOUT" adb pull /data/tombstones "$out/tombstones"
  collect anr.log "$CHEAP_TIMEOUT" adb pull /data/anr "$out/anr"
else
  printf '<adb root unavailable; tombstones and ANR traces not pulled>\n' >>"$out/root.log"
fi

printf 'collect_android_diagnostics: wrote the always and on-failure tiers to %s (run exit %s)\n' \
  "$out" "$rc"
exit 0
