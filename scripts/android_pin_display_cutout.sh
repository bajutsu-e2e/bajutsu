#!/usr/bin/env bash
# Pin the pixel_6 display-cutout emulation and lay System UI out against it, so the status bar's
# geometry is the same on every boot (the `visual (adb)` job's status_bar_android baseline).
#
# The emulator turns the pixel_6 cutout on after boot by enabling two runtime resource overlays.
# How that lands against System UI's own start varies from boot to boot:
#   - enabled before System UI lays out: a 128-pixel status bar, the clock inset from the rounded
#     corner. This is what the committed baseline shows.
#   - enabled after: the cutout inset changes but System UI keeps part of its first layout, so the
#     clock and the battery glyph sit a few pixels off.
#   - never enabled (seen after an `idmap` crash at boot): no cutout, a 63-pixel status bar.
# All three were observed on CI against an unchanged emulator build. Enabling both overlays here
# (a no-op when the emulator already did) and then restarting System UI makes it lay out once,
# against the final cutout, whatever the boot did.
#
# Usage: scripts/android_pin_display_cutout.sh   (ADB overrides the adb invocation, e.g. "adb -s X")
set -euo pipefail

ADB=${ADB:-adb}
OVERLAYS=(com.android.internal.emulation.pixel_6 com.android.systemui.emulation.pixel_6)
# The pixel_6 overlay's cutout depth, in screenshot pixels. The status bar takes this height once
# System UI has laid out against the cutout.
STATUS_BAR_HEIGHT=128
# A bounded condition wait: System UI is back within a few seconds on the CI software renderer.
ATTEMPTS=120

for overlay in "${OVERLAYS[@]}"; do
  $ADB shell cmd overlay enable --user 0 "$overlay"
done

# A pinned overlay that did not take would make the restart below pointless; fail loudly instead.
insets=$($ADB shell dumpsys window displays | grep -o -m1 'DisplayCutout{insets=Rect([^)]*)' || true)
if [[ "$insets" != *"Rect(0, $STATUS_BAR_HEIGHT - 0, 0)"* ]]; then
  echo "android_pin_display_cutout: unexpected display cutout: ${insets:-none}" >&2
  exit 1
fi

old_pid=$($ADB shell pidof com.android.systemui | tr -d '\r')
# System UI is a persistent process: the system restarts it at once after the crash.
$ADB shell am crash com.android.systemui

for ((i = 0; i < ATTEMPTS; i++)); do
  pid=$($ADB shell pidof com.android.systemui | tr -d '\r' || true)
  if [[ -n "$pid" && "$pid" != "$old_pid" ]]; then
    window=$($ADB shell dumpsys window windows \
      | sed -n '/Window{[^}]* StatusBar}:/,/WindowStateAnimator/p' || true)
    if [[ "$window" == *"isReadyForDisplay()=true"* \
      && "$window" == *"frame=[0,0][1080,$STATUS_BAR_HEIGHT]"* ]]; then
      echo "android_pin_display_cutout: System UI $old_pid -> $pid, status bar ${STATUS_BAR_HEIGHT}px"
      exit 0
    fi
  fi
  sleep 0.5
done

echo "android_pin_display_cutout: System UI did not come back with a ${STATUS_BAR_HEIGHT}px status bar" >&2
exit 1
