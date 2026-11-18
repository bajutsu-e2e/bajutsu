"""Print UIKit's localized edit-menu "Paste" label for every language an iOS runtime ships.

Usage: python extract_paste_labels.py <path to UIKitCore.framework inside a simruntime>

The table in `bajutsu/common/drivers/xcuitest/paste_menu.py` is this script's output, read from the
iOS 26.5 (23F77) Simulator runtime. UIKit renders the edit menu with the app's own localization, so
the label is whichever of these the app under test resolves to — not necessarily the device language.
"""

from __future__ import annotations

import plistlib
import sys
from pathlib import Path


def main(framework: Path) -> None:
    labels: dict[str, str] = {}
    for lproj in sorted(framework.glob("*.lproj")):
        strings = lproj / "Localizable.strings"
        if strings.exists():
            with strings.open("rb") as fh:
                value = plistlib.load(fh).get("Paste")
            if value:
                labels[lproj.stem] = value
    for language, label in labels.items():
        print(f'    "{language}": "{label}",')


if __name__ == "__main__":
    main(Path(sys.argv[1]))
