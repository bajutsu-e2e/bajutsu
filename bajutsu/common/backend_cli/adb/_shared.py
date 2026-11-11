"""The adb key codes, input-event codes, and command shapes the front end is built from."""

from __future__ import annotations

from collections.abc import Callable

# argv -> stdout. adb needs no parent-process env (unlike simctl's SIMCTL_CHILD_*).
RunFn = Callable[[list[str]], str]


KEYCODE_HOME = 3  # the Home button: backgrounds the app without terminating it (BE-0478).
KEYCODE_BACK = 4  # `input keyevent` code for the system back button (Android's true system back).
KEYCODE_DEL = 67  # backspace — deletes the character before the cursor (BE-0265 delete / clear).
KEYCODE_CTRL_LEFT = 113  # left Control, the modifier for the select-all / copy key combinations.
KEYCODE_A = 29  # the `A` key — with Ctrl held, "select all" in a focused text field (BE-0265).
KEYCODE_C = 31  # the `C` key — with Ctrl held, "copy" the active selection (BE-0265).
# The server's own package (its applicationId); force-stopped at lease end to kill any device-side
# instrumentation the local adb client's exit did not.
RESIDENT_SERVER_PACKAGE = "dev.bajutsu.android.server"
# The instrumentation APK's own package: the server package plus Android's conventional `.test`
# suffix. Named here so the resident channel can clear both before it installs either.
RESIDENT_TEST_PACKAGE = f"{RESIDENT_SERVER_PACKAGE}.test"
