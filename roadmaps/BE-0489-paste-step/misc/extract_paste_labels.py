"""Print UIKit's localized edit-menu labels for every language an iOS runtime ships.

Usage: python extract_paste_labels.py <path to UIKitCore.framework inside a simruntime>

The tables in `bajutsu/common/drivers/xcuitest/paste_menu.py` are this script's output, read from
the iOS 26.5 (23F77) Simulator runtime: one table per edit-menu item the iOS driver taps, "Paste"
(BE-0489) and "Select All" and "Copy" (BE-0493). UIKit renders the edit menu with the app's own
localization, so the label is whichever of these the app under test resolves to — not necessarily
the device language.
"""

from __future__ import annotations

import plistlib
import sys
from pathlib import Path

# The `Localizable.strings` key of each menu item, and the name its table takes in `paste_menu.py`.
_ITEMS = {"Paste": "PASTE", "Select All": "SELECT_ALL", "Copy": "COPY"}


def _labels(framework: Path, key: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for lproj in sorted(framework.glob("*.lproj")):
        strings = lproj / "Localizable.strings"
        if strings.exists():
            with strings.open("rb") as fh:
                value = plistlib.load(fh).get(key)
            if value:
                labels[lproj.stem] = value
    return labels


def main(framework: Path) -> None:
    for key, name in _ITEMS.items():
        print(f"{name}_MENU_LABEL_BY_LANGUAGE: dict[str, str] = {{")
        for language, label in _labels(framework, key).items():
            print(f'    "{language}": "{label}",')
        print("}\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
