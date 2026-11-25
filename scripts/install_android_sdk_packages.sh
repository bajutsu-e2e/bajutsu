#!/usr/bin/env bash
# Install Android SDK packages with `sdkmanager`, retrying once, and fail by package name when one
# did not land.
#
# `sdkmanager` reports an archive it cannot unpack ("An error occurred while preparing SDK package
# ...: Error reading Zip content ...") as a *warning* and still exits 0, so a damaged download
# leaves the package missing without failing anything. `reactivecircus/android-emulator-runner`
# discards that output, so the job then dies at its first `adb` call with a `Connection refused`
# against an emulator that never booted, and the log never says the system image is missing
# (issue #2519). The damage is intermittent — a re-run of the same commit passes — so one retry
# absorbs it, and a second failure stops the step naming the package.
#
# Run before the emulator-runner step: the action's own `sdkmanager --install` then finds each
# package already up to date and downloads nothing.
#
# Usage: scripts/install_android_sdk_packages.sh <package>...
#   e.g. scripts/install_android_sdk_packages.sh emulator 'system-images;android-34;google_apis;x86_64'
# Environment: ANDROID_HOME (the SDK root), SDKMANAGER (defaults to `sdkmanager` on PATH),
#   SDK_CHANNEL (defaults to 0, the stable channel the emulator-runner action installs from).
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "usage: $0 <package>..." >&2
  exit 2
fi
: "${ANDROID_HOME:?ANDROID_HOME must point at the Android SDK root}"

SDKMANAGER=${SDKMANAGER:-sdkmanager}
SDK_CHANNEL=${SDK_CHANNEL:-0}
ATTEMPTS=2
# The warning sdkmanager prints, and keeps going after, when a downloaded archive is unusable.
DAMAGED='An error occurred while preparing SDK package'

# sdkmanager writes package.xml only once a package is fully installed, at the package id's path
# with each `;` as a directory separator.
installed() {
  [ -f "$ANDROID_HOME/${1//;//}/package.xml" ]
}

for package in "$@"; do
  attempt=1
  while :; do
    echo "Installing $package (attempt $attempt of $ATTEMPTS)"
    status=0
    output=$("$SDKMANAGER" --install "$package" --channel="$SDK_CHANNEL" --sdk_root="$ANDROID_HOME" 2>&1) || status=$?
    # Echo the output this script captured, so the log keeps what sdkmanager said either way.
    printf '%s\n' "$output"
    if [ "$status" -ne 0 ]; then
      reason="sdkmanager exited $status"
    elif grep -qF "$DAMAGED" <<<"$output"; then
      reason="sdkmanager could not unpack the downloaded archive"
    elif ! installed "$package"; then
      reason="sdkmanager finished but $ANDROID_HOME/${package//;//}/package.xml is missing"
    else
      break
    fi
    if [ "$attempt" -ge "$ATTEMPTS" ]; then
      echo "::error::Android SDK package '$package' is not installed after $ATTEMPTS attempts: $reason"
      exit 1
    fi
    echo "::warning::Android SDK package '$package': $reason; retrying"
    # sdkmanager can resume from what it staged, which would hand the retry the same damaged
    # archive; clear its staging so the second attempt downloads afresh.
    rm -rf "$ANDROID_HOME/.temp" "$ANDROID_HOME/.downloadIntermediates"
    attempt=$((attempt + 1))
  done
done
