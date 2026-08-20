#!/usr/bin/env bash
# Run the concurrent-device pool scenarios against TWO emulators (BE-0298).
#
# This must run from *inside* android-e2e.yml's `reactivecircus/android-emulator-runner` step, like
# scripts/collect_android_diagnostics.sh: that action boots one emulator, runs its `script:`, and
# kills it when the step ends, so the second emulator has to be booted here — inside the same step —
# and there is no later step where a device is still attached. The action itself has no
# two-emulator mode, which is the whole reason this script exists rather than another `with:` input.
#
# Usage, from that job's `script:` (one line, since the emulator-runner runs each line as its own sh):
#   scripts/android_pool_e2e.sh <avd-name>
#
# It boots a second instance of the SAME cached AVD with `-read-only` — the flag that lets a second
# emulator process share the AVD's images through its own overlay. The job's own emulator carries the
# flag too, because an instance holding the AVD read-write locks it and refuses the second one
# outright ("run all emulators with -read-only flag"); `-read-only` also disables snapshot load, so
# this instance cold-boots rather than resuming the job's cached snapshot, which is what the generous
# ceiling below is sized for. Then it runs `make -C demos/showcase/android
# e2e-pool` with both serials in one `bajutsu run --workers 2`, asserts the pool's isolation
# invariant over what that run left on disk, sweeps the device diagnostics, and re-raises the run's
# own exit code. The second emulator is killed on every exit path, so the action's own teardown never
# has to reap it.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

avd="${1:?usage: android_pool_e2e.sh <avd-name>}"

# The emulator console port of the instance this script boots. Console ports are even and the action's
# own emulator normally holds 5554 — the first *free* even port rather than a guarantee — so the second
# instance takes the next one; adb names it after its console port. The distinctness check below is
# what keeps that "normally" from silently running against an emulator this script never booted.
readonly SECOND_PORT=5556
readonly SECOND_SERIAL="emulator-${SECOND_PORT}"
# How long the second emulator may take to report `sys.boot_completed`, counted from the moment it is
# launched. `-read-only` rules out a snapshot resume, so this is a full cold boot at one core beside a
# first emulator already up and contending for the host — hence a ceiling well above what a resume
# would need. It is one bound over both waits below, not one each: the job's own `timeout-minutes` has
# to stay above it, and a job GitHub cancels at that ceiling prints none of the diagnostics these
# waits exist to print. It bounds a condition poll, not a delay — each wait returns the moment its
# condition holds.
readonly BOOT_TIMEOUT=600

emulator_bin="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-}}/emulator/emulator"
if [ ! -x "$emulator_bin" ]; then
  echo "::error::no emulator binary at $emulator_bin — is the Android SDK on this runner?" >&2
  exit 1
fi

# The whole attached list, space-delimited, rather than only its first entry: the emulator the run
# leases is the first one, but the emulator that could already hold the second instance's port is any
# of them.
attached=$(adb devices | awk '/^emulator-/ {print $1}' | tr '\n' ' ')
first_serial=${attached%% *}
if [ -z "$first_serial" ]; then
  echo "::error::no emulator attached; this script runs inside the emulator-runner's own step" >&2
  exit 1
fi
# Nothing attached may already hold the port the second instance takes. `SECOND_SERIAL` is fixed while
# the action's own emulator takes only the first *free* even port, so on a collision the second
# `emulator` process fails to bind, both waits below answer from an emulator this script never booted,
# and `bajutsu run` gets either the same serial twice or a device nothing here verified — which the
# isolation assertion would then read as the pool's doing rather than as a second emulator that never
# came up. Matched with `case` rather than a `grep -q` pipeline, whose SIGPIPE exit under `pipefail`
# would read as "no match" and skip the very check this is. Checked before the trap is installed, since
# `emu kill "$SECOND_SERIAL"` would otherwise reap that other emulator on the way out.
case " $attached" in
  *" $SECOND_SERIAL "*)
    echo "::error::$SECOND_SERIAL is already attached, so the second instance cannot take port $SECOND_PORT" >&2
    exit 1
    ;;
esac

# Kill the second emulator on every exit path, pass or fail: leaving it behind would outlive this
# step and could be picked up by anything else on the runner. Best-effort — a teardown failure must
# not replace the run's own verdict.
trap 'adb -s "$SECOND_SERIAL" emu kill >/dev/null 2>&1 || true' EXIT

echo "Booting a second instance of AVD '$avd' on port $SECOND_PORT (read-only)"
# Detached with its output kept: a second emulator that fails to come up is diagnosable only from its
# own log, and the boot wait below would otherwise report nothing but a timeout.
mkdir -p runs/diagnostics
"$emulator_bin" -avd "$avd" -read-only -port "$SECOND_PORT" \
  -memory 3072 -cores 1 -no-window -gpu swiftshader_indirect -noaudio -no-boot-anim \
  -no-snapshot-save -camera-back none -camera-front none \
  >runs/diagnostics/emulator-second.log 2>&1 &

# Wait for the device to attach, then for the framework to finish booting. `wait-for-device` returns
# as soon as adbd answers, which is well before the system is usable, so the property poll is what
# actually gates the run. Both waits share one deadline taken here, so `BOOT_TIMEOUT` is the ceiling
# for the whole bring-up rather than for each half of it — the difference decides whether the script
# fails loudly or the job is cancelled out from under it.
deadline=$((SECONDS + BOOT_TIMEOUT))
if ! timeout "$BOOT_TIMEOUT" adb -s "$SECOND_SERIAL" wait-for-device; then
  echo "::error::$SECOND_SERIAL never attached within ${BOOT_TIMEOUT}s" >&2
  tail -n 40 runs/diagnostics/emulator-second.log >&2 || true
  exit 1
fi
booted=0
while [ "$SECONDS" -lt "$deadline" ]; do
  if [ "$(adb -s "$SECOND_SERIAL" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; then
    booted=1
    break
  fi
  sleep 2
done
if [ "$booted" -ne 1 ]; then
  echo "::error::$SECOND_SERIAL did not report sys.boot_completed within ${BOOT_TIMEOUT}s of launch" >&2
  tail -n 40 runs/diagnostics/emulator-second.log >&2 || true
  exit 1
fi
echo "Second emulator ready: $SECOND_SERIAL (first: $first_serial)"

make -C demos/showcase/android e2e-pool SERIALS="${first_serial},${SECOND_SERIAL}"
rc=$?

# The isolation verdict, only over a run that produced a manifest — a run that died earlier has
# already failed the job, and asserting over a directory it never wrote would bury that failure under
# a parse error. Every run id is a zero-padded UTC timestamp (BE-0200) and this script runs `bajutsu
# run` exactly once, so the shape resolves to exactly one directory; `runs/` also holds the
# diagnostics directory, which the shape excludes by construction.
if [ "$rc" -eq 0 ]; then
  shopt -s nullglob
  dirs=(runs/[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9])
  if [ "${#dirs[@]}" -ne 1 ]; then
    echo "::error::expected exactly one run directory under runs/, found ${#dirs[@]}" >&2
    rc=1
  else
    uv run --no-sync python scripts/assert_pool_isolation.py \
      --run-dir "${dirs[0]}" --expect-devices 2 || rc=$?
  fi
fi

# Scoped to the first emulator with ANDROID_SERIAL: the sweep's reads are bare `adb` calls, which
# with two devices attached would every one of them fail on "more than one device". One device's
# state is what the sweep is for — the host telemetry the job brackets this step with covers the
# contention both of them create — and the second emulator's own boot log is already collected above.
ANDROID_SERIAL="$first_serial" scripts/collect_android_diagnostics.sh "$rc" || true
exit "$rc"
