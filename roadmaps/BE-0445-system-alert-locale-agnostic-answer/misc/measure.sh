#!/usr/bin/env bash
# BE-0445 Unit 1: dump every SpringBoard permission-prompt button under each (runtime, language, tap)
# and write one JSON line per prompt to misc/results.jsonl.
#
# Each combination gets its own freshly created Simulator, pinned to the language the same way
# BE-0320's run pins it (a one-element AppleLanguages plus AppleLocale, then a boot), so no prompt
# was answered before and no already-booted device is touched. The Simulators are deleted afterwards.
#
# Usage: roadmaps/BE-0445-system-alert-locale-agnostic-answer/misc/measure.sh
# Override the matrix with RUNTIMES="iOS-18-6:iPhone-16 iOS-26-5:iPhone-17" LOCALES="en_US ja_JP",
# and the output file with OUT=<path>. The committed results.jsonl is the default matrix followed by
# RUNTIMES="iOS-26-5:iPhone-17" LOCALES="ar_SA", the right-to-left check.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../../.." && pwd)
app_dir="$root/demos/showcase/ios/swiftui"
out=${OUT:-"$here/results.jsonl"}
dd="$app_dir/build/be0445-dd"
runtimes=${RUNTIMES:-"iOS-18-6:iPhone-16 iOS-26-5:iPhone-17"}
locales=${LOCALES:-"en_US ja_JP"}

(cd "$app_dir" && xcodegen generate >/dev/null)
xcodebuild build-for-testing -project "$app_dir/BajutsuShowcaseSwiftUI.xcodeproj" -scheme UITests \
    -destination 'generic/platform=iOS Simulator' -derivedDataPath "$dd" -quiet
xctestrun=$(find "$dd/Build/Products" -name '*.xctestrun' | head -n 1)

# A failed boot or write under `set -e` must not leave the combination's Simulator behind.
udid=""
trap '[ -n "$udid" ] && xcrun simctl delete "$udid" >/dev/null 2>&1 || true' EXIT

: >"$out"
for pair in $runtimes; do
    runtime=${pair%%:*}
    device=${pair#*:}
    for locale in $locales; do
        for tap in 0 1; do
            udid=$(xcrun simctl create "be0445-$runtime-$locale-$tap" \
                "com.apple.CoreSimulator.SimDeviceType.$device" \
                "com.apple.CoreSimulator.SimRuntime.$runtime")
            # `defaults write` needs the data container, which exists only once booted; the second
            # boot is what makes SpringBoard render in the pinned language.
            xcrun simctl boot "$udid"
            xcrun simctl bootstatus "$udid" >/dev/null
            xcrun simctl spawn "$udid" defaults write .GlobalPreferences AppleLanguages -array "${locale%%_*}"
            xcrun simctl spawn "$udid" defaults write .GlobalPreferences AppleLocale -string "$locale"
            xcrun simctl shutdown "$udid"
            xcrun simctl boot "$udid"
            xcrun simctl bootstatus "$udid" >/dev/null
            log="$dd/probe-$runtime-$locale-$tap.log"
            TEST_RUNNER_BE0445_PROBE=1 TEST_RUNNER_BE0445_TAP=$tap \
                xcodebuild test-without-building -xctestrun "$xctestrun" \
                -destination "id=$udid" \
                -only-testing:BajutsuShowcaseSwiftUIUITests/SystemAlertProbeUITests >"$log" 2>&1 || true
            grep -o 'BE0445PROBE .*' "$log" | sed 's/^BE0445PROBE //' | sort -u >>"$out" || true
            xcrun simctl shutdown "$udid" || true
            xcrun simctl delete "$udid"
            udid=""
        done
    done
done
echo "wrote $(wc -l <"$out" | tr -d ' ') record(s) to $out"
