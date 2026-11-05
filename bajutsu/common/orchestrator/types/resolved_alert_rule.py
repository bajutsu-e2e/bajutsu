"""One `systemAlert` interrupts entry, resolved for a locale."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResolvedAlertRule:
    """One shape of one `systemAlert` interrupts entry, resolved for a locale.

    `identifying_labels` are the labels that together name this shape, resolved from
    `bajutsu.common.scenario.system_alerts` for the run's locale — matching requires all of them,
    since a single shared label (e.g. "Allow") cannot by itself tell two covered prompts apart.
    `tap_label` is the label the rule's `choice` names. One scenario rule resolves to several of
    these when its prompt renders differently by context or iOS version (BE-0406).

    `excluded_labels` rules the shape *out* where its identifying labels alone would also fit another
    alert — iOS 26.5's in-app save sheet is "Save" and "Not Now", the same pair the credit-card update
    sheet offers, and only "Never for This Card" tells them apart. An exclusion rather than a demand
    that the shape's labels *be* the alert's whole button set, because the in-tree paths this reaches
    match over every identifier-less labelled button in the poll's tree rather than one alert's, so
    there is no button set there to compare against.

    `native` and `in_tree` carry the prompt's `AlertSurfaces` record, and each gates a different
    matching site in the XCUITest runner (BE-0461): `in_tree` arms its in-tree pass, and `native`
    its SpringBoard check and interruption monitor. Keeping them apart is not merely tidy — an
    in-tree-only shape's identifying labels are ordinary vocabulary a real SpringBoard alert could
    coincidentally offer (the 26.5 save sheet's shape is just "Save" / "Not Now"), so without the
    split a SpringBoard alert satisfying that pair would be answered for a prompt that surface can
    never actually reach.
    """

    identifying_labels: frozenset[str]
    tap_label: str
    excluded_labels: frozenset[str] = frozenset()
    native: bool = True
    in_tree: bool = False
