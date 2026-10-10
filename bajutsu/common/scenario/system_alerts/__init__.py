"""Locale-keyed button labels for the iOS system prompts a permission preset cannot reach (BE-0320).

`handleSystemAlert` taps a SpringBoard prompt's button by its visible text, and BE-0320 pins the
Simulator's system language so that text is deterministic. This table closes the remaining gap for
the prompts BE-0276's `permissions` presets cannot pre-answer — notification authorization is not a
TCC (Transparency, Consent, and Control) service, App Tracking Transparency (ATT) has no `simctl`
toggle at all, and the cross-process paste consent is TCC-backed as `kTCCServicePasteboard` yet has
no `simctl` toggle either (BE-0369) — so a scenario about any of them can name the *intent*
(`grant` / `deny`) instead of transcribing whichever language the pinned locale renders.

Deliberately narrow. It covers those named prompts alone, never an open-ended translation of
arbitrary SpringBoard text, and only the languages whose values have been read back from a
Simulator. Every other alert keeps using the literal `label` / `labelMatches` a scenario supplies,
unchanged.

A language outside the table no longer fails the `handleSystemAlert` step (BE-0445): each SpringBoard
prompt also carries a position rule (`system_alert_role`), measured to hold whatever language the
prompt renders in, which the step falls back to. The reactive guard does not, since a position cannot
tell it which declared prompt is on screen; its rules still need this table's labels.

This is a source of button *labels*, not a claim about which process owns the alert: `savePassword`
is raised into the application's own process, and BE-0406 added it here anyway because the path that
taps a label is chosen separately, by whether the SpringBoard query can see the alert. `_SURFACES`
below is what records that difference, per prompt.

The values are Apple's own, transcribed from the iOS Simulator runtime's shipped strings:
`UserNotificationsServer.framework/<lang>.lproj/Localizable.strings` (`PERMISSION_ALERT_ALLOW` /
`PERMISSION_ALERT_DENY`), `TCC.framework/<lang>.lproj/Localizable.strings`
(`REQUEST_ACCESS_ALLOW_kTCCServiceUserTracking` / `REQUEST_ACCESS_DENY_kTCCServiceUserTracking`),
`DragUI.framework/<lang>.lproj/Localizable.strings` (`PASTE_AUTHORIZATION_BUTTON_ALLOW` /
`PASTE_AUTHORIZATION_BUTTON_DENY`), and — for `savePassword` — `WebUI.framework`'s
`Save Password (save login information sheet)`, `... (save login information sheet in app)`,
`Never for This Website (save login information sheet)`, `Not Now (save login information sheet)`
and `Never for This Card (save credit card data sheet)`.
Re-reading those files under a new runtime is what checks this table, rather than trusting it. Two
properties of `WebUI.framework`'s location matter when re-checking it: it lives in the runtime's
cryptex, at `System/Cryptexes/OS/System/Library/PrivateFrameworks/`, rather than beside the three
frameworks above; and every `.strings` file is an Apple binary property list, so it yields its
contents to `plutil` rather than to a plain-text search.
"""

from ._functions import _LABELS as _LABELS
from ._functions import _ROLES as _ROLES
from ._functions import _SURFACES as _SURFACES
from ._functions import (
    TITLE_MARKER,
    SystemAlertChoice,
    SystemAlertPrompt,
    alert_surfaces,
    alert_title_marker,
    covered_languages,
    labels_cover,
    system_alert_label,
    system_alert_role,
    system_alert_shapes,
)
from ._functions import _shapes as _shapes
from ._prompt_surfaces import _PromptSurfaces as _PromptSurfaces
from ._prompts import _Prompts as _Prompts
from ._shape import _Shape as _Shape
from .alert_surfaces import AlertSurfaces
from .resolved_alert_shape import ResolvedAlertShape
from .system_alert_role import SystemAlertRole
from .uncovered_system_alert_locale import UncoveredSystemAlertLocale

__all__ = [
    "TITLE_MARKER",
    "AlertSurfaces",
    "ResolvedAlertShape",
    "SystemAlertChoice",
    "SystemAlertPrompt",
    "SystemAlertRole",
    "UncoveredSystemAlertLocale",
    "alert_surfaces",
    "alert_title_marker",
    "covered_languages",
    "labels_cover",
    "system_alert_label",
    "system_alert_role",
    "system_alert_shapes",
]
