"""UIKit's edit-menu "Paste" label in every language an iOS runtime ships.

The iOS `paste` step taps the edit menu's Paste item rather than sending Cmd+V: a hardware Cmd+V
inserts nothing when the clipboard was written by another process (a `setClipboard` step, which goes
through simctl), and reports no error either. The edit menu's Paste is a system paste control, so it
reads that clipboard without iOS's paste-consent prompt.

The menu item carries no identifier, only its label, and UIKit renders that label in the *app's*
localization, not the device language: an app that ships no Japanese strings shows "Paste" on a
Japanese device. So the label cannot be looked up from the scenario's locale; the driver instead
accepts any of these labels and requires exactly one to be on screen.

Values are UIKit's own `Localizable.strings` entries for the key `Paste`, read from the iOS 26.5
Simulator runtime's `UIKitCore.framework`, not transcribed by hand.
"""

from __future__ import annotations

PASTE_MENU_LABEL_BY_LANGUAGE: dict[str, str] = {
    "ar": "لصق",
    "bg": "Постави",
    "bn": "পেস্ট",
    "ca": "Enganxa",
    "cs": "Vložit",
    "da": "Sæt ind",
    "de": "Einsetzen",
    "el": "Επικόλληση",
    "en": "Paste",
    "en_AU": "Paste",
    "en_GB": "Paste",
    "es": "Pegar",
    "es_419": "Pegar",
    "fi": "Sijoita",
    "fr": "Coller",
    "fr_CA": "Coller",
    "gu": "પેસ્ટ કરો",
    "he": "הדבקה",
    "hi": "पेस्ट",
    "hr": "Zalijepi",
    "hu": "Beillesztés",
    "id": "Tempel",
    "it": "Incolla",
    "ja": "ペースト",
    "kk": "Қою",
    "kn": "ಪೇಸ್ಟ್",
    "ko": "붙여넣기",
    "lt": "Įklijuoti",
    "ml": "പേസ്റ്റ്",
    "mr": "पेस्ट करा",
    "ms": "Tampal",
    "nl": "Plak",
    "no": "Lim inn",
    "or": "ପେଷ୍ଟ୍ କରନ୍ତୁ",
    "pa": "ਪੇਸਟ",
    "pl": "Wklej",
    "pt": "Colar",
    "pt_PT": "Colar",
    "ro": "Lipiți",
    "ru": "Вставить",
    "sk": "Vložiť",
    "sl": "Prilepi",
    "sv": "Klistra in",
    "ta": "ஒட்டு",
    "te": "పేస్ట్",
    "th": "วาง",
    "tr": "Yapıştır",
    "uk": "Вставити",
    "ur": "پیسٹ کریں",
    "vi": "Dán",
    "zh_CN": "粘贴",
    "zh_HK": "貼上",
    "zh_TW": "貼上",
}

PASTE_MENU_LABELS: frozenset[str] = frozenset(PASTE_MENU_LABEL_BY_LANGUAGE.values())
